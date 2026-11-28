"""Checking the cost of forgetting thousands of Drive records without wall-clock limits."""

from skyfold import drive_engine


def test_forgetting_unrelated_leaves_uses_work_proportional_to_removed_rows(tmp_path):
    state = drive_engine.DriveState(tmp_path / "drive.db")
    paths = [f"Garden/item-{n}.txt" for n in range(80)]
    retained = [f"Bed/plan-{n}.txt" for n in range(500)] + ["Garden/item-1.txt0"]
    for path in paths + retained + ["Garden/item-1.txt/old"]:
        state.put(drive_engine.Base(path, False, "e", 1, 1.0, 1, False))

    # A callback every 1,000 SQLite instructions counts work independently of disk and CPU speed.
    steps = []
    state._db.set_progress_handler(lambda: (steps.append(1), 0)[1], 1000)
    try:
        state.drop_many(paths)
    finally:
        state._db.set_progress_handler(None, 0)
    assert len(steps) < 40  # 40,000 instructions leave ample room for indexed lookups
    assert set(state.all()) == set(retained)
