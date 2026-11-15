"""Thirty-second follow-up review (Codex review 34): own regression tests next to the kept probes."""
import shutil
from types import SimpleNamespace

from tests.test_drive import env as drive_env, write  # noqa: F401 - fixtures
from tests.test_engine import env as photos_env  # noqa: F401


def _asked_in_icloud(s, cloud, root):
    s.delete_threshold = 1
    for p in ("a.txt", "b.txt"):
        cloud.put(p, b"ORIGINAL")
    s.run()
    for p in ("a.txt", "b.txt"):
        (root / p).unlink()
    res = s.run()
    assert set(res.pending_to_cloud) == {"a.txt", "b.txt"}
    return res


# question versions -----------------------------------------------------------------------------
def test_an_answer_to_an_older_version_of_the_question_is_not_taken(drive_env):
    s, cloud, root, _ = drive_env
    res = _asked_in_icloud(s, cloud, root)
    seen = s.question_gen
    cloud.trash(cloud._item("a.txt"))
    cloud.put("a.txt", b"ORIGINAL")  # another item at that name now: another question
    s.run()
    assert s.question_gen != seen
    assert s.approve(res.pending_to_cloud, "cloud", seen) == []
    assert s.approve(res.pending_to_cloud, "cloud", s.question_gen) == res.pending_to_cloud


def test_the_same_question_keeps_its_version(drive_env):
    s, cloud, root, _ = drive_env
    _asked_in_icloud(s, cloud, root)
    seen = s.question_gen
    s.run()
    s.run()
    assert s.question_gen == seen


def test_the_worker_ignores_a_keep_answer_to_an_older_question(drive_env):
    from skyfold.worker import SyncWorker

    s, cloud, root, _ = drive_env
    res = _asked_in_icloud(s, cloud, root)
    now = SimpleNamespace(to_pc=[], to_cloud=list(res.pending_to_cloud), gen=s.question_gen + 1)
    dropped = []
    worker = SimpleNamespace(_pending=None, _drive_pending=now, drive=s,
                             drive_state=SimpleNamespace(drop=dropped.append),
                             pendingChanged=SimpleNamespace(emit=lambda _p: None), _kick_drive=lambda **_k: None)
    SyncWorker.resolve_pending(worker, f"reject:to_cloud:{s.question_gen}", res.pending_to_cloud)
    assert not dropped and worker._drive_pending is now


# A34-6 -----------------------------------------------------------------------------------------
def test_the_question_counts_an_empty_folder_too(drive_env):
    from skyfold.worker import SyncWorker

    s, cloud, root, _ = drive_env
    cloud.put("a.txt", b"A")
    cloud.put("Empty", None)
    s.run()
    worker = SimpleNamespace(_pending=None, _drive_pending=None, drive=s, drive_state=s.state,
                             notify=SimpleNamespace(emit=lambda *_a: None),
                             pendingChanged=SimpleNamespace(emit=lambda _p: None))
    SyncWorker._on_drive_pending(worker, ["a.txt", "Empty"], [])
    assert worker._drive_pending.files_to_cloud == 2


# A34-2 -----------------------------------------------------------------------------------------
def test_an_ok_for_a_folder_covers_its_files_listed_one_by_one(drive_env):
    s, cloud, root, _ = drive_env
    s.delete_threshold = 1
    for p in ("Trip/a.txt", "Trip/b.txt", "Trip/c.txt"):
        cloud.put(p, b"ORIGINAL")
    s.run()
    shutil.rmtree(root / "Trip")
    q = s.run()
    assert q.pending_to_cloud == ["Trip"]
    assert s.approve(q.pending_to_cloud, "cloud", s.question_gen) == ["Trip"]
    s.run()
    assert not [p for p in cloud.items if p.startswith("Trip/")]


# A34-7 / A34-8 ---------------------------------------------------------------------------------
def test_a_photos_ok_for_icloud_never_deletes_on_this_pc(photos_env):
    s, phone, root, local_trash = photos_env
    s.settings.delete_threshold = 1
    phone.add("A1", "a.jpg", b"one")
    phone.add("A2", "b.jpg", b"two")
    s.run(force_full=True)
    s.approve(["A1", "A2"], "cloud")
    phone.remove("A1")
    phone.remove("A2")
    res = s.run(force_full=True)
    assert set(res.pending.to_pc) == {"A1", "A2"} and not local_trash


def test_a_photos_question_keeps_its_version_until_it_changes(photos_env):
    s, phone, root, _ = photos_env
    s.settings.delete_threshold = 1
    phone.add("A1", "a.jpg", b"one")
    phone.add("A2", "b.jpg", b"two")
    s.run(force_full=True)
    phone.remove("A1")
    phone.remove("A2")
    first = s.run(force_full=True).pending
    again = s.run(force_full=True).pending
    assert first.gen == again.gen
    phone.add("A2", "b.jpg", b"two")
    third = s.run(force_full=True).pending
    assert third.gen != first.gen and third.to_pc == ["A1"]
