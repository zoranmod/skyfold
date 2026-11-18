"""iPhone (HEIC) photos and drawings in notes: shown at full size, the preview stays on any problem."""
import os

from skyfold import notes_engine as ne
from tests.test_notes import env as notes_env  # noqa: F401 - fixture


def _page(tmp_path, src: str):
    assets = tmp_path / ne.ASSETS / "n1"
    assets.mkdir(parents=True)
    page = tmp_path / "Note.html"
    page.write_text(f'<img class="attachment image" data-uti="public.heic" data-id="A1" src="{src}">',
                    encoding="utf-8")
    return assets, page


def _download_into(name: bytes = b"HEIC ORIGINAL"):
    got = []

    def download(url, folder):
        p = os.path.join(str(folder), "icloud-asset-full")
        with open(p, "wb") as fh:
            fh.write(name)
        got.append(p)
        return p
    return download, got


def test_a_heic_preview_is_replaced_by_a_jpeg_of_the_original(tmp_path):
    assets, page = _page(tmp_path, "_assets/n1/preview.png")
    (assets / "preview.png").write_bytes(b"PREVIEW")
    made = {ne._file_identity(assets / "preview.png")}
    download, got = _download_into()

    def convert(src, folder):
        out = folder / "photo.jpg"
        out.write_bytes(b"JPEG of " + open(src, "rb").read())
        return out

    assert ne.upgrade_heic(page, {"A1": "url"}, download, made=made, dirs={assets}, convert=convert) == 1
    assert 'src="_assets/n1/photo.jpg"' in page.read_text(encoding="utf-8")
    assert (assets / "photo.jpg").read_bytes() == b"JPEG of HEIC ORIGINAL"
    assert not os.path.exists(got[0])  # the downloaded original was only needed for the conversion
    assert not (assets / "preview.png").exists()  # downloaded by this export for this page


def test_when_it_cant_be_converted_the_preview_stays(tmp_path):
    assets, page = _page(tmp_path, "_assets/n1/preview.png")
    (assets / "preview.png").write_bytes(b"PREVIEW")
    before = page.read_text(encoding="utf-8")
    download, got = _download_into()
    assert ne.upgrade_heic(page, {"A1": "url"}, download, made=set(), dirs={assets},
                           convert=lambda *_a: None) == 0
    assert page.read_text(encoding="utf-8") == before and (assets / "preview.png").exists()
    assert not os.path.exists(got[0])


def test_a_preview_this_export_didnt_make_stays(tmp_path):
    assets, page = _page(tmp_path, "_assets/n1/preview.png")
    (assets / "preview.png").write_bytes(b"SOMEONE ELSE'S")
    download, _ = _download_into()
    ne.upgrade_heic(page, {"A1": "url"}, download, made=set(), dirs={assets},
                    convert=lambda src, folder: (folder / "photo.jpg").write_bytes(b"J") and folder / "photo.jpg")
    assert (assets / "preview.png").read_bytes() == b"SOMEONE ELSE'S"


def test_an_original_heic_in_the_page_is_converted_in_place(tmp_path):
    assets, page = _page(tmp_path, "_assets/n1/IMG_1.heic")
    (assets / "IMG_1.heic").write_bytes(b"ORIGINAL")
    seen = []

    def convert(src, folder):
        seen.append(src.name)
        out = folder / "IMG_1.jpg"
        out.write_bytes(b"J")
        return out

    assert ne.upgrade_heic(page, {}, lambda *_a: None, convert=convert) == 1
    assert seen == ["IMG_1.heic"] and 'src="_assets/n1/IMG_1.jpg"' in page.read_text(encoding="utf-8")


def test_the_windows_converter_gives_up_cleanly_on_something_that_isnt_a_photo(tmp_path):
    bad = tmp_path / "x.heic"
    bad.write_bytes(b"not a photo at all")
    assert ne.heic_to_jpeg(bad, tmp_path) is None
    assert sorted(p.name for p in tmp_path.iterdir()) == ["x.heic"]  # nothing left behind


def test_only_notes_with_photos_or_drawings_are_exported_again(notes_env):
    s, remote, root, _ = notes_env
    remote.put("n1", "Plain")
    remote.put("n2", "Sketch")
    s.run()
    rows = s.state.all()
    sketch = root / rows["n2"][1]
    sketch.write_text(sketch.read_text(encoding="utf-8") + '<img data-uti="com.apple.paper" src="x.png">',
                      encoding="utf-8")  # (a preview that can't even be read counts as small)
    s.state.set("quality", None)
    s._refresh_for_quality()
    rows = s.state.all()
    assert rows["n2"][4] is None and rows["n1"][4] is not None and s.state.get("quality") == ne.QUALITY


def test_a_wide_low_drawing_replaces_the_square_preview(tmp_path):
    from PySide6.QtGui import QColor, QImage

    assets = tmp_path / ne.ASSETS / "n1"
    assets.mkdir(parents=True)
    preview = QImage(768, 768, QImage.Format_RGB32)
    preview.fill(QColor("white"))
    preview.save(str(assets / "prev"), "PNG")
    page = tmp_path / "Note.html"
    page.write_text('<img data-uti="com.apple.paper" data-id="D1" src="_assets/n1/prev">', encoding="utf-8")

    def download(_url, folder):
        out = folder / "full"
        img = QImage(1536, 256, QImage.Format_RGB32)
        img.fill(QColor("blue"))
        img.save(str(out), "PNG")
        return str(out)

    assert ne.upgrade_sketches(page, {"D1": "u"}, download) == 1
    assert "full.png" in page.read_text(encoding="utf-8")


def test_a_full_drawing_smaller_than_the_preview_is_not_used(tmp_path):
    from PySide6.QtGui import QColor, QImage

    assets = tmp_path / ne.ASSETS / "n1"
    assets.mkdir(parents=True)
    QImage(768, 768, QImage.Format_RGB32).save(str(assets / "prev"), "PNG")
    page = tmp_path / "Note.html"
    page.write_text('<img data-uti="com.apple.paper" data-id="D1" src="_assets/n1/prev">', encoding="utf-8")

    def download(_url, folder):
        out = folder / "full"
        img = QImage(600, 300, QImage.Format_RGB32)
        img.fill(QColor("blue"))
        img.save(str(out), "PNG")
        return str(out)

    assert ne.upgrade_sketches(page, {"D1": "u"}, download) == 0


def test_notes_with_a_scan_or_a_small_photo_are_exported_again_once(notes_env):
    s, remote, root, _ = notes_env
    for nid, title in (("n1", "Plain"), ("n2", "Scan"), ("n3", "Photo")):
        remote.put(nid, title)
    s.run()
    rows = s.state.all()
    scan, photo = root / rows["n2"][1], root / rows["n3"][1]
    scan.write_text(scan.read_text(encoding="utf-8") + '<a data-uti="com.apple.paper.doc.scan">Scan</a>',
                    encoding="utf-8")
    photo.write_text(photo.read_text(encoding="utf-8") + '<img data-uti="public.jpeg" src="small.jpg">',
                     encoding="utf-8")
    s.state.set("quality", "2")  # what 1.0.10 left
    s._refresh_for_quality()
    rows = s.state.all()
    assert rows["n2"][4] is None and rows["n3"][4] is None and rows["n1"][4] is not None
