"""A paged Notes feed may report a note more than once before the cursor advances."""

from dataclasses import replace

import pytest

from tests.test_notes import env


def test_repeated_new_note_changes_leave_one_owned_page(env):
    sync, feed, root, _ = env
    feed.put("start", "Welcome")
    sync.run()
    feed.put("garden", "Garden plan", body="first")
    feed.put("garden", "Garden plan", body="revised")
    sync.run()
    pages = list(root.rglob("Garden plan*.html"))
    assert len(pages) == 1
    assert "revised" in pages[0].read_text(encoding="utf-8")
    feed.put("garden", "Garden plan", locked=True)
    sync.run()
    assert not list(root.rglob("Garden plan*.html"))


@pytest.mark.parametrize("final", ["locked", "deleted"])
def test_final_private_or_deleted_event_never_exports_the_intermediate_note(env, monkeypatch, final):
    sync, feed, root, _ = env
    feed.put("start", "Welcome")
    sync.run()
    feed.put("garden", "Garden plan", body="unexpected retained text")
    first = feed.notes["garden"]
    last = replace(first, **{final: True})
    monkeypatch.setattr(feed, "changes", lambda since: [first, last])
    sync.run()
    assert not list(root.rglob("Garden plan*.html"))
    row = sync.state.all().get("garden")
    if final == "locked":
        assert row[5] and row[1] is None
    else:
        assert row is None


def test_restored_note_after_deletion_keeps_its_final_snapshot_and_neighbours(env, monkeypatch):
    sync, feed, root, _ = env
    feed.put("garden", "Old plan", body="original")
    feed.put("neighbour", "Garden plan", body="other note")
    sync.run()
    old = feed.notes["garden"]
    feed.put("garden", "Garden plan", body="restored")
    monkeypatch.setattr(feed, "changes", lambda since: [replace(old, deleted=True), feed.notes["garden"]])
    sync.run()
    rows = sync.state.all()
    restored = root / rows["garden"][1]
    neighbour = root / rows["neighbour"][1]
    assert restored != neighbour and "restored" in restored.read_text(encoding="utf-8")
    assert "other note" in neighbour.read_text(encoding="utf-8")
    assert sorted(root.rglob("Garden plan*.html")) == sorted([restored, neighbour])
