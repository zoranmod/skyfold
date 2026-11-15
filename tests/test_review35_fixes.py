"""Thirty-third follow-up review (Codex review 35): own regression tests next to the kept probes."""
from tests.test_drive import env as drive_env, write  # noqa: F401 - fixtures


def test_pc_deletions_in_two_folders_are_counted_together_by_a_quick_pass(drive_env):
    s, cloud, root, _ = drive_env
    s.delete_threshold = 1
    for p in ("Alpha/a.txt", "Beta/b.txt"):
        cloud.put(p, b"ORIGINAL")
    s.run()
    (root / "Alpha/a.txt").unlink()  # the watcher reports only this folder at first
    (root / "Beta/b.txt").unlink()
    res = s.run(changed={"Alpha/a.txt"})
    assert res.full and set(res.pending_to_cloud) == {"Alpha/a.txt", "Beta/b.txt"}
    assert "Alpha/a.txt" in cloud.items and "Beta/b.txt" in cloud.items


def test_a_quick_pass_without_deletions_stays_quick(drive_env):
    s, cloud, root, _ = drive_env
    cloud.put("Docs/a.txt", b"A")
    s.run()
    write(root, "Docs/new.txt", b"NEW")
    res = s.run(changed={"Docs/new.txt"})
    assert not res.full and cloud.items["Docs/new.txt"]["data"] == b"NEW"


def test_files_asked_one_by_one_and_then_seen_as_their_folder_are_asked_again(drive_env):
    s, cloud, root, recycled = drive_env
    s.delete_threshold = 1
    for p in ("Trip/a.txt", "Trip/b.txt"):
        cloud.put(p, b"ORIGINAL")
    s.run()
    write(root, "Trip/draft.tmp", b"scratch")  # keeps the folder: its files are asked one by one
    cloud.trash(cloud._item("Trip"))
    first = s.run()
    assert set(first.pending_to_pc) == {"Trip/a.txt", "Trip/b.txt"}
    (root / "Trip/draft.tmp").unlink()
    second = s.run()
    assert second.pending_to_pc == ["Trip"] and not recycled  # the same deletion, asked as the folder
