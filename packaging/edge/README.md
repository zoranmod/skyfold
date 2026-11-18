# Edge Add-ons: submitting "Skyfold – Safari Bookmarks"

Package to upload: `dist/extension/skyfold-safari-bookmarks-store-1.0.0.zip` (made by `python extension/build.py`;
the store version has no development `key`, the store gives the extension its own id).

Images (this folder): `logo-300x300.png`, `screenshot-1280x800.png`.
Privacy policy URL: https://github.com/zoranmod/skyfold/blob/main/extension/PRIVACY.md

## Store listing (English)

**Name:** Skyfold – Safari Bookmarks

**Short description** (shown in search):
Your Safari bookmarks from iPhone, iPad and Mac in Edge, kept up to date in a "Safari Bookmarks" folder. Works with the free Skyfold app for Windows.

**Description:**
Use Safari on your iPhone, iPad or Mac and Edge on Windows? This extension shows your Safari bookmarks in Edge, in a "Safari Bookmarks" folder under Other favorites, with the same folders and the same order as in Safari.

It stays up to date by itself: every 30 minutes, when Edge starts, and when you click "Update now". No exporting, no importing, no duplicates.

How it works
• The free, open-source Skyfold app for Windows reads your Safari bookmarks from your own iCloud account (read-only).
• This extension gets them from Skyfold on your PC through the browser's native messaging. It never connects to the internet.
• It only manages its own "Safari Bookmarks" folder; your other favorites are never touched.

Requirements
• Skyfold for Windows, 1.0.13 or later: https://github.com/zoranmod/skyfold
• In Skyfold: Settings → Notes → Safari bookmarks turned on.

Good to know
• One-way: Safari → Edge. The folder is replaced when your Safari bookmarks change, so add your own favorites elsewhere.
• The Reading List and tab groups are left out.
• Not affiliated with Apple. Safari, iCloud, iPhone, iPad and Mac are trademarks of Apple Inc.

**Category:** Productivity
**Website:** https://github.com/zoranmod/skyfold
**Support:** https://github.com/zoranmod/skyfold/issues

## Notes for certification (paste into "Notes for certification")

This extension needs the companion desktop app Skyfold (open source, https://github.com/zoranmod/skyfold), which registers the native messaging host "com.skyfold.bookmarks" for this extension. Without it, the popup explains that Skyfold isn't set up and nothing else happens; the extension makes no network requests at all.

Permissions:
- bookmarks: to create and update its own "Safari Bookmarks" folder (only that folder is changed).
- nativeMessaging: to receive the bookmarks from the Skyfold app on the same PC.
- storage: to remember the id of its folder and when it last updated.
- alarms: to refresh every 30 minutes.

Testing without an Apple account: the Skyfold app answers from a local cache file. To test, install Skyfold, then put a file %LOCALAPPDATA%\Skyfold\safari-bookmarks.json with the content
{"updated":"2026-11-18T10:00:00+00:00","tree":[{"t":"Favorites","c":[{"t":"Example","u":"https://example.com/"}]}]}
and turn on Settings → Notes → Safari bookmarks so Skyfold registers the native host. Then click the extension's "Update now".

Source code of the extension: https://github.com/zoranmod/skyfold/tree/main/extension
