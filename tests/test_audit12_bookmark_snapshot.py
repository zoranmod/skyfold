"""Preserve the last complete Safari export when a changes page is incomplete."""
import json
from types import SimpleNamespace

import pytest

from skyfold import bookmarks
from tests.test_bookmarks import FakeApi, leaf


class FakeEnvelopes(FakeApi):
    def post(self, *args, **kwargs):
        body = self.pages.pop(0)
        return SimpleNamespace(status_code=200, json=lambda: body)


def zone(**fields):
    return {"zones": [{"zoneID": {"zoneName": bookmarks.ZONE}, **fields}]}


@pytest.mark.parametrize("pages", [
    [{"zones": []}],
    [{"serverErrorCode": "SERVICE_UNAVAILABLE"}],
    [{"zones": [{}]}],
    [{"zones": [{"zoneID": {"zoneName": "Other"}, "records": []}]}],
    [zone(records=None)],
    [zone(records=[], moreComing=True), zone(records=[])],
    [zone(records=[], moreComing=True, syncToken="t1"), {"zones": []}],
    [zone(records=[], moreComing=True, syncToken="t1"),
     zone(records=[], moreComing=True, syncToken="t1"), zone(records=[])],
    [zone(records=[], moreComing=True, syncToken="t1"), zone(serverErrorCode="ZONE_NOT_FOUND")],
], ids=["no-zone", "request-error", "unidentified-zone", "wrong-zone", "invalid-records", "missing-cursor",
        "missing-final-zone", "repeated-cursor", "zone-lost"])
def test_incomplete_snapshot_preserves_both_copies(tmp_path, _bookmarks_cache_in_tmp, pages):
    records = [leaf("garden", "Garden plan", "https://garden.example.test", "", 1)]
    target, count, _ = bookmarks.export(FakeEnvelopes([zone(records=records)]), tmp_path)
    cache = _bookmarks_cache_in_tmp
    assert count == 1 and "Garden plan" in target.read_text(encoding="utf-8")
    before = (target.read_bytes(), cache.read_bytes())

    with pytest.raises(bookmarks.BookmarksUnavailable):
        bookmarks.export(FakeEnvelopes(pages), tmp_path)
    assert (target.read_bytes(), cache.read_bytes()) == before


@pytest.mark.parametrize("empty", [zone(records=[]), zone(), zone(serverErrorCode="ZONE_NOT_FOUND")])
def test_a_confirmed_empty_zone_can_still_replace_a_previous_export(tmp_path, _bookmarks_cache_in_tmp, empty):
    records = [leaf("garden", "Garden plan", "https://garden.example.test", "", 1)]
    api = FakeEnvelopes([zone(records=records), empty])
    bookmarks.export(api, tmp_path)
    target, count, changed = bookmarks.export(api, tmp_path)
    assert changed and count == 0 and "Garden plan" not in target.read_text(encoding="utf-8")
    assert json.loads(_bookmarks_cache_in_tmp.read_text(encoding="utf-8"))["tree"] == []
