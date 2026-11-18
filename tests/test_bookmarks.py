"""Safari bookmarks → bookmarks file (synthetic CloudKit records shaped like the real ones)."""
import base64
import json
import zlib
from types import SimpleNamespace

import pytest

from skyfold import bookmarks as bm
from skyfold.engine import SyncError


def enc(text):
    return {"type": "ENCRYPTED_BYTES", "value": base64.b64encode(text.encode()).decode()}


def pos(value, change=1):
    raw = zlib.compress(json.dumps({"default": {"sortValues": [
        {"sortValue": value, "deviceIdentifier": "d", "changeID": change}]}}).encode())
    return {"type": "BYTES", "value": base64.b64encode(raw).decode()}


def leaf(name, title, url, parent, order, **extra):
    return {"recordName": name, "recordType": "BookmarkLeaf", "fields": {
        "Title": enc(title), "URL": enc(url), "ParentFolder": {"type": "REFERENCE", "value": {"recordName": parent}},
        "Position": pos(order), "DateAdded": {"type": "TIMESTAMP", "value": 1700000000000}, **extra}}


def folder(name, title, parent, order, **extra):
    return {"recordName": name, "recordType": "BookmarkList", "fields": {
        "Title": enc(title), "ParentFolder": {"type": "REFERENCE", "value": {"recordName": parent}},
        "Position": pos(order), **extra}}


ROOT = "com.apple.Safari.TopBookmark"
RECORDS = [
    {"recordName": "x", "recordType": "EncryptionInfo", "fields": {}},
    folder("bar", "BookmarksBar", ROOT, 1),  # Safari's internal name for Favorites
    folder("work", "Work", "bar", 2),
    leaf("b2", "Second", "https://example.com/2", "bar", 3),
    leaf("b1", "First", "https://example.com/1", "bar", 1),
    leaf("w1", "Docs & <stuff>", "https://example.com/docs?a=1&b=2", "work", 1),
    leaf("gone", "Deleted one", "https://example.com/x", "bar", 4, Deleted={"type": "INT64", "value": 1}),
    {"recordName": "tomb", "deleted": True},
    leaf("evil", "Script", "javascript:alert(1)", "bar", 5),
    folder("rl", "com.apple.ReadingList", ROOT, 9),  # (its real title)
    folder("menu", "BookmarksMenu", ROOT, 2),
    leaf("m1", "In the menu", "https://example.com/m", "menu", 1),
    leaf("r1", "Read later", "https://example.com/rl", "rl", 1),
    folder("tabs", "Trip tabs", ROOT, 8, IsTabGroup={"type": "INT64", "value": 1}),
    leaf("t1", "Tab", "https://example.com/tab", "tabs", 1),
]


def test_the_tree_keeps_folders_order_and_leaves_out_what_isnt_a_bookmark():
    nodes = bm.build(RECORDS)
    assert [n.title for n in nodes] == ["Favorites", "Bookmarks Menu"]  # Safari's names, not internal ones
    fav = nodes[0]
    assert [c.title for c in fav.children] == ["First", "Work", "Second"]  # by Safari's position
    assert bm.count(nodes) == 4  # no deleted, script, Reading List or tab group entries


def test_the_file_is_the_standard_format_and_escaped():
    page = bm.render(bm.build(RECORDS), "Safari Bookmarks")
    assert page.startswith("<!DOCTYPE NETSCAPE-Bookmark-file-1>") and bm.MARK in page
    assert '<A HREF="https://example.com/docs?a=1&amp;b=2"' in page and "Docs &amp; &lt;stuff&gt;" in page
    assert "javascript:" not in page and "Read later" not in page and "<H3>Safari Bookmarks</H3>" in page


class FakeApi:
    def __init__(self, pages):
        self.pages, self.sent = list(pages), []
        self.params = {}
        self.session = SimpleNamespace(post=self.post)

    def get_webservice_url(self, name):
        assert name == "ckdatabasews"
        return "https://p00-ckdatabasews.icloud.com"

    def post(self, url, params=None, json=None, timeout=None, allow_redirects=None):
        assert url.endswith(".WBSCloudBookmarksStore/production/private/changes/zone") and allow_redirects is False
        self.sent.append(json)
        page = self.pages.pop(0)
        return SimpleNamespace(status_code=200, json=lambda: {"zones": [page]})


def test_all_pages_are_read_with_the_sync_token():
    api = FakeApi([{"records": RECORDS[:5], "moreComing": True, "syncToken": "T1"},
                   {"records": RECORDS[5:], "moreComing": False}])
    assert len(bm.fetch(api)) == len(RECORDS)
    assert api.sent[1]["zones"][0]["syncToken"] == "T1"


def test_export_writes_its_own_file_and_never_someone_elses(tmp_path):
    api = FakeApi([{"records": RECORDS, "moreComing": False}] * 3)
    path, n, changed = bm.export(api, tmp_path)
    assert path.name == bm.FILE_NAME and n == 4 and changed
    assert bm.export(api, tmp_path)[2] is False  # nothing new: not written again
    path.write_text("<html>my own bookmarks</html>", encoding="utf-8")
    with pytest.raises(SyncError):
        bm.export(api, tmp_path)
    assert path.read_text(encoding="utf-8") == "<html>my own bookmarks</html>"


def test_a_refusal_keeps_the_last_good_file(tmp_path):
    api = FakeApi([{"records": RECORDS, "moreComing": False}])
    path, _, _ = bm.export(api, tmp_path)
    before = path.read_text(encoding="utf-8")
    api.pages = [{"serverErrorCode": "ACCESS_DENIED"}]
    with pytest.raises(bm.BookmarksUnavailable):
        bm.export(api, tmp_path)
    assert path.read_text(encoding="utf-8") == before


def test_no_bookmarks_in_icloud_is_an_empty_file_not_an_error(tmp_path):
    api = FakeApi([{"serverErrorCode": "ZONE_NOT_FOUND"}])
    path, n, _ = bm.export(api, tmp_path)
    assert n == 0 and bm.MARK in path.read_text(encoding="utf-8")
