"""Notes: a page is removed by the clean-up only when Skyfold itself wrote it down as left behind."""

import os

from tests.test_notes import env  # noqa: F401


def test_a_page_that_could_not_be_removed_on_rename_is_removed_later(env, monkeypatch):  # noqa: F811
    s, r, root, trashed = env
    r.put("n1", "Old title")
    s.run()
    real = s._remove
    monkeypatch.setattr(s, "_remove", lambda row, res, count=True: False)  # e.g. the page is open in a program
    r.put("n1", "New title")
    s.run()
    assert (root / "Notes/Old title.html").exists() and (root / "Notes/New title.html").exists()
    assert s.state.get("obsolete:Notes/Old title.html")
    monkeypatch.setattr(s, "_remove", real)
    s.run(full=True)
    assert not (root / "Notes/Old title.html").exists() and "Old title.html" in trashed
    assert not s.state.get("obsolete:Notes/Old title.html")


def test_a_left_behind_page_the_user_changed_is_kept(env, monkeypatch):  # noqa: F811
    s, r, root, trashed = env
    r.put("n1", "Old title")
    s.run()
    monkeypatch.setattr(s, "_remove", lambda row, res, count=True: False)
    r.put("n1", "New title")
    s.run()
    old = root / "Notes/Old title.html"
    old.write_text(old.read_text(encoding="utf-8") + "my notes", encoding="utf-8")
    s.run(full=True)
    assert old.exists() and "Old title.html" not in trashed


def test_a_new_note_never_takes_an_existing_untracked_file(env):  # noqa: F811
    s, r, root, _ = env
    (root / "Notes").mkdir(parents=True)
    mine = root / "Notes/Plan.html"
    mine.write_text('<style id="skyfold"></style>looks like a Skyfold page, but it is mine', encoding="utf-8")
    r.put("n1", "Plan")
    s.run()
    assert "but it is mine" in mine.read_text(encoding="utf-8")
    assert sorted(os.listdir(root / "Notes")) == ["Plan (2).html", "Plan.html"]
