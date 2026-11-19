"""The upload is one-way (1.0.16): sent files stay ordinary files, and the photo they became isn't
downloaded back. Records of the two-way upload of 1.0.15 are carried over."""
import json

from skyfold import engine as eng
from tests.test_engine import FakeRemote, env  # noqa: F401
from tests.test_drive import write


def test_sent_photo_is_independent_on_both_sides(env):  # noqa: F811
    s, remote, root, trashed = env
    s.settings.photos_upload = True
    s.run()
    p = write(root, "beach.jpg", b"sand", age=300)
    s.run()
    (aid,) = remote.assets
    assert not s.state.files() and s.state.get("upload_sent:" + aid)
    s.run(force_full=True)
    assert [q.name for q in root.rglob("*.jpg")] == ["beach.jpg"]  # not downloaded back
    remote.remove(aid)
    s.run(force_full=True)
    assert p.exists() and not trashed  # deleted in iCloud: the PC file stays
    s.run(force_full=True)
    assert remote.uploads == ["beach.jpg"]  # and isn't sent again


def test_lost_reply_is_recognised_by_contents(env):  # noqa: F811
    s, remote, root, _ = env
    s.settings.photos_upload = True
    s.run()
    real = remote.upload

    def lost(path):
        real(path)
        raise ConnectionError("reply lost")

    remote.upload = lost
    write(root, "late.jpg", b"late", age=300)
    s.run()
    s.run(force_full=True)
    assert [q.name for q in root.rglob("*.jpg")] == ["late.jpg"] and remote.uploads == ["late.jpg"]


def test_two_way_upload_of_1_0_15_becomes_one_way(env):  # noqa: F811
    s, remote, root, trashed = env
    remote.add("UP1", "mine.jpg", b"mine")
    s.run()
    row = s.state.files()[0]
    s.state.set("upload_unlisted:UP1", "1")  # sent with 1.0.15, not in the full listing yet
    s.run()
    p = root / row.rel_path
    assert not s.state.files() and s.state.get("upload_sent:UP1") and not s.state.get("upload_unlisted:UP1")
    remote.remove("UP1")
    s.run(force_full=True)
    assert p.exists() and not trashed


def test_photos_folder_records_its_account(env, tmp_path):  # noqa: F811
    s, remote, root, _ = env
    s.settings.apple_id = "a@example.invalid"
    s.run()
    owner = (root / eng.OWNER_FILE).read_text(encoding="utf-8").strip()
    assert owner == s._owner_fp() and not s._foreign
    s.settings.apple_id = "b@example.invalid"
    s.settings.photos_upload = True
    write(root, "a-private.jpg", b"a", age=300)
    s.run()
    assert s._foreign and not getattr(remote, "uploads", [])
    assert json.loads(s.state.get("foreign_ids"))
