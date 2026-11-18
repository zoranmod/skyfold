"""Codex image-quality investigation (of 56e809b), kept as tests. The cases that were strict xfails
(planned improvements) are done since 1.0.11 and are ordinary tests now. The tests of Codex's separate
reference module are left out (it is not part of Skyfold).

Offline quality contracts for Skyfold 56e809b; no production monkeypatches.

Run with notes_quality_runner.py. Strict xfails document proposed changes;
--runxfail exposes the currently failing desired-behaviour assertions.
CloudKit records are synthetic, parsed by the actual pinned pyicloud models.
"""
import base64
import gzip
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

import pytest
from PySide6.QtGui import QImage, QImageReader, QPainter, QPdfWriter, QPageSize
from PySide6.QtWidgets import QApplication
from pyicloud.common.cloudkit import CKRecord
from pyicloud.services.notes.protobuf import notes_pb2 as pb
from pyicloud.services.notes.rendering import exporter as ex
from pyicloud.services.notes.rendering.ck_datasource import CloudKitNoteDataSource
from pyicloud.services.notes.rendering.options import ExportConfig
from skyfold import notes_engine as ne
from tests.test_notes import env  # existing database / syncer / service fixture


@pytest.fixture(scope="session", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication([])
    ne._prefer_sharpest_previews()
    yield app


def field(kind, value):
    return {"type": kind, "value": value}


def token(name, size=100):
    return {"downloadURL": "https://assets.invalid/" + name, "size": size}


def asset(name, size=100):
    return field("ASSETID", token(name, size))


def rec(uti="public.jpeg", name="a", **fields):
    return CKRecord(recordName=name, recordType="Attachment", fields={
        "UTI": field("STRING", uti), **fields})


def previews():
    return {"PreviewImages": field("ASSETID_LIST", [token("small", 100), token("large", 200)]),
            "PreviewAppearances": field("INT64_LIST", [0, 0])}


def data(record):
    ds = CloudKitNoteDataSource(_config=ExportConfig())
    ds.add_attachment_record(record)
    return ds


def wire(ds):
    media = rec(name="media", Image=asset("original", 1000))
    ex._wire_media_to_parent(ds, {"media": "a"}, media, ex._media_field_url(media), ExportConfig())


def png(path, w, h, fmt="PNG"):
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(0xfff4f4f4)
    assert img.save(str(path), fmt)
    return path


def sketch_case(tmp_path, dims=(768, 2048), fmt="PNG"):
    directory = tmp_path / "_assets" / "note"
    directory.mkdir(parents=True)
    preview = png(directory / "preview.png", 768, 768)
    fallback = png(tmp_path / "source", *dims, fmt)
    page = tmp_path / "stage.html"
    page.write_text('<img data-uti="com.apple.paper" data-id="a" src="_assets/note/preview.png">')
    def download(url, folder):
        dest = Path(folder) / "new-download"
        with dest.open("xb") as out:
            out.write(fallback.read_bytes())
        return str(dest)
    return page, preview, download


@pytest.mark.parametrize("uti", sorted(ne.FULL_IMAGE_UTIS))
def test_browser_image_media_promoted(uti):
    ds = data(rec(uti, **previews()))
    wire(ds)
    assert ds.get_primary_asset_url("a").endswith("/original")


def test_heic_nonfirst_preview_currently_stays():
    ds = data(rec("public.heic", **previews()))
    wire(ds)
    assert ds.get_primary_asset_url("a").endswith("/large")


def test_heic_single_preview_must_survive_without_codec():
    ds = data(rec("public.heic", PreviewImages=field("ASSETID_LIST", [token("small")])))
    wire(ds)
    assert ds.get_primary_asset_url("a").endswith("/small")


def test_preview_dimensions_beat_compressed_byte_size():
    ds = data(rec("com.apple.paper",
        PreviewImages=field("ASSETID_LIST", [token("384px", 90000), token("2048px", 12000)]),
        PreviewWidths=field("INT64_LIST", [384, 2048]),
        PreviewHeights=field("INT64_LIST", [384, 2048]),
        PreviewAppearances=field("INT64_LIST", [0, 0])))
    assert ds.get_primary_asset_url("a").endswith("/2048px")


def test_appearance_is_preserved_over_larger_dark_preview():
    ds = data(rec(PreviewImages=field("ASSETID_LIST", [token("light", 10), token("dark", 999)]),
                  PreviewAppearances=field("INT64_LIST", [0, 1])))
    assert ds.get_primary_asset_url("a").endswith("/light")


def test_unusable_largest_token_keeps_a_valid_smaller_preview():
    ds = data(rec(PreviewImages=field("ASSETID_LIST", [token("valid", 100), {"size": 1000}]),
                  PreviewAppearances=field("INT64_LIST", [0, 0])))
    assert ds.get_primary_asset_url("a").endswith("/valid")


def test_jpg_alias_uses_original():
    ds = data(rec("public.jpg", **previews()))
    wire(ds)
    assert ds.get_primary_asset_url("a").endswith("/original")


@pytest.mark.parametrize("field_name", ["Image", "Media", "FallbackImage"])
def test_direct_full_bitmap_can_beat_preview(field_name):
    ds = data(rec(**previews(), **{field_name: asset("original", 1000)}))
    assert ds.get_primary_asset_url("a").endswith("/original")


def test_media_image_beats_unrelated_thumbnail_field():
    media = rec(name="media", Thumbnail=asset("small"), Image=asset("original", 1000))
    assert ex._media_field_url(media).endswith("/original")


def test_full_asset_updates_inline_alias_too():
    ds = data(rec(AttachmentIdentifier=field("STRING", "inline-id"), **previews()))
    wire(ds)
    assert ds.get_primary_asset_url("inline-id").endswith("/original")


def test_shared_media_upgrades_all_parents():
    ds = CloudKitNoteDataSource(_config=ExportConfig())
    records = [rec(name=n, Media=field("REFERENCE", {"recordName": "media"}), **previews()) for n in ("a", "b")]
    media_map = ex._hydrate_attachment_records(ds, SimpleNamespace(records=records), ExportConfig())
    m = rec(name="media", Image=asset("original", 1000))
    ex._wire_media_to_parent(ds, media_map, m, ex._media_field_url(m), ExportConfig())
    assert all(ds.get_primary_asset_url(n).endswith("/original") for n in ("a", "b"))


def test_scan_fallback_pdf_is_selected():
    ds = data(rec("com.apple.paper.doc.scan", FallbackPDF=asset("full-scan.pdf"), **previews()))
    assert ds.get_primary_asset_url("a") == token("full-scan.pdf")["downloadURL"]


@pytest.mark.parametrize("uti", ["com.apple.drawing", "com.apple.drawing.2", "com.apple.notes.sketch"])
def test_legacy_drawing_fallback_bitmap_is_selected(uti):
    ds = data(rec(uti, FallbackImage=asset("full-drawing.png"), **previews()))
    assert ds.get_primary_asset_url("a") == token("full-drawing.png")["downloadURL"]


def test_pdf_priority_and_only_first_paper_asset():
    ds = data(rec("com.apple.paper.doc.pdf", FallbackPDF=asset("fallback.pdf"),
                  PaperAssets=field("ASSETID_LIST", [token("page1"), token("page2")]), **previews()))
    assert ds.get_primary_asset_url("a").endswith("/fallback.pdf")
    ds2 = data(rec("com.apple.paper.doc.pdf", PaperAssets=field("ASSETID_LIST", [token("page1"), token("page2")])))
    assert ds2.get_primary_asset_url("a").endswith("/page1")


def test_tall_uncropped_sketch_is_not_rejected(tmp_path):
    page, _, download = sketch_case(tmp_path)
    assert ne.upgrade_sketches(page, {"a": "full"}, download) == 1


def test_tiff_sketch_is_converted_to_browser_format(tmp_path):
    page, _, download = sketch_case(tmp_path, (2048, 2048), "TIFF")
    assert ne.upgrade_sketches(page, {"a": "full"}, download) == 1
    src = ne.IMG_TAG.search(page.read_text()).group(1)
    assert Path(src).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}


def test_full_sketch_success_preserves_unowned_preview(tmp_path):
    page, preview, download = sketch_case(tmp_path, (2048, 2048))
    original = preview.read_bytes()
    assert ne.upgrade_sketches(page, {"a": "full"}, download) == 1
    assert preview.read_bytes() == original


def test_failed_optional_sketch_download_keeps_html_and_preview(tmp_path):
    page, preview, _ = sketch_case(tmp_path)
    before = (page.read_bytes(), preview.read_bytes())
    def fail(*args):
        raise OSError("synthetic optional download failure")
    assert ne.upgrade_sketches(page, {"a": "full"}, fail) == 0
    assert (page.read_bytes(), preview.read_bytes()) == before


def test_new_image_never_overwrites_existing_target(tmp_path):
    target = tmp_path / "page.png"
    target.write_bytes(b"user file")
    img = QImage(10, 10, QImage.Format.Format_RGB32)
    img.fill(0xff112233)
    placed = ne._save_image_new(img, target)
    assert placed != target and placed.is_file()
    assert target.read_bytes() == b"user file"


def test_pdf_actual_dimensions_original_kept_and_collision_safe(tmp_path):
    pdf = tmp_path / "_assets" / "scan.pdf"
    pdf.parent.mkdir()
    writer = QPdfWriter(str(pdf))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    p = QPainter(writer)
    p.drawText(50, 100, "Synthetic scan page")
    p.end()
    del writer
    existing = pdf.with_name(pdf.name + ".p1.png")
    existing.write_bytes(b"user-owned collision")
    before = pdf.read_bytes()
    result = ne.pdfs_to_images('<object data="_assets/scan.pdf"></object>', tmp_path, "Original")
    src = ne.IMG_TAG.search(result).group(1)
    image = QImage(str(tmp_path / unquote(src)))
    assert image.width() == 1500 and 2100 <= image.height() <= 2130
    assert existing.read_bytes() == b"user-owned collision" and pdf.read_bytes() == before


def test_format_bump_reexports_unchanged_notes_once(env, monkeypatch):
    s, remote, root, _ = env
    remote.put("n1", "Quality", body='<p>unchanged</p>')
    assert s.run().exported == 1
    monkeypatch.setattr(ne, "FORMAT", ne.FORMAT + "-quality-test")
    assert s.run().exported == 1
    assert s.run().exported == 0
    assert (root / "Notes/Quality.html").is_file()


class OfflineCloudKit:
    """Synthetic supplement: FakeNotes does not hydrate/render attachments."""
    def __init__(self, records, blobs):
        self.records = {r.recordName: r for r in records}
        self.blobs = blobs
        self.downloads = []

    def lookup(self, names):
        return SimpleNamespace(records=[self.records[n] for n in names if n in self.records])

    def download_asset_to(self, url, directory):
        self.downloads.append(url)
        folder = Path(directory)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"download-{len(self.downloads)}"
        with target.open("xb") as f:
            f.write(self.blobs[url])
        return str(target)


def note_record(uti="public.jpeg"):
    msg = pb.NoteStoreProto()
    msg.document.note.note_text = "\ufffc"
    run = msg.document.note.attribute_run.add()
    run.length = 1
    run.attachment_info.attachment_identifier = "a"
    run.attachment_info.type_uti = uti
    return CKRecord(recordName="n1", recordType="Note", fields={
        "TextDataEncrypted": field("ENCRYPTED_BYTES", base64.b64encode(gzip.compress(msg.SerializeToString())).decode()),
        "Attachments": field("REFERENCE_LIST", [{"recordName": "a"}])})


def test_real_exporter_through_existing_notes_fixture_gets_full_jpeg(env, tmp_path):
    s, fake, root, _ = env
    source = png(tmp_path / "source.jpg", 2400, 1800, "JPEG")
    attachment = rec(Media=field("REFERENCE", {"recordName": "media"}), **previews())
    media = rec(name="media", Image=asset("original", source.stat().st_size))
    raw = OfflineCloudKit([attachment, media], {token("original")["downloadURL"]: source.read_bytes()})
    adapter = ne.ICloudNotes(SimpleNamespace(notes=SimpleNamespace(
        _raw=raw, _lookup_note_record=lambda nid: note_record())))
    fake.export = adapter.export  # use existing fake change feed + real exporter pipeline
    fake.put("n1", "Full JPEG")
    result = s.run()
    assert result.exported == 1 and result.errors == 0
    page = root / "Notes/Full JPEG.html"
    src = ne.IMG_TAG.search(page.read_text()).group(1)
    image = QImage(str((page.parent / unquote(src)).resolve()))
    assert (image.width(), image.height()) == (2400, 1800)
    assert raw.downloads == [token("original")["downloadURL"]]


def test_optional_full_image_failure_still_exports_available_preview(env, tmp_path):
    s, fake, root, _ = env
    source = png(tmp_path / "preview.jpg", 320, 240, "JPEG")
    attachment = rec(Media=field("REFERENCE", {"recordName": "media"}), **previews())
    media = rec(name="media", Image=asset("original", 1000))
    # No original blob: fake download raises KeyError, but BOTH previews exist.
    raw = OfflineCloudKit([attachment, media], {
        token(name)["downloadURL"]: source.read_bytes() for name in ("small", "large")})
    adapter = ne.ICloudNotes(SimpleNamespace(notes=SimpleNamespace(
        _raw=raw, _lookup_note_record=lambda nid: note_record())))
    fake.export = adapter.export
    fake.put("n1", "Fallback")
    result = s.run()
    assert result.exported == 1 and result.errors == 0
    page = root / "Notes/Fallback.html"
    src = ne.IMG_TAG.search(page.read_text()).group(1)
    assert not src.startswith("https:")
    assert QImage(str((page.parent / unquote(src)).resolve())).width() == 320
