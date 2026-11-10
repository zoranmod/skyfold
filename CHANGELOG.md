# Changelog

All notable changes to Skyfold are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.0.6] - 2026-11-10

Fixes from the second independent code review (OpenAI Codex) of 1.0.5. Each has a regression test that fails on 1.0.5.

### Fixed
- iCloud Drive: when only some sub-folders are chosen, their parent folders are never deleted as a whole (they can hold folders that aren't synced).
- iCloud Drive: junctions and links inside the Drive folder are never followed, so nothing is written outside it.
- iCloud Drive: a Drive folder remembers which Apple ID it belongs to; another account can't sync into it.
- iCloud Drive: a deletion approved later is checked again first; a file edited after the question stays.
- iCloud Drive: a deleted folder and a new look-alike folder in iCloud are no longer taken for a rename (iCloud's item identity is checked).
- iCloud Drive: a folder that iCloud lists only partly never counts as "deleted in iCloud".
- iCloud Drive: two iCloud names that differ only in letter case no longer overwrite each other on Windows.
- Notes: an existing Notes.html that isn't Skyfold's is never replaced; an empty answer from Apple never removes notes, however few.
- Photos: a file already in place is adopted only when name, size and the photo's date all match.

- Follow-up review of the 1.0.6 candidate (tests in tests/test_review3_fixes.py):
  - Links/junctions are never followed by Photos, Notes or a quick Drive check, and nothing is uploaded from outside the Drive folder.
  - A Drive folder whose sync marker can't be read, or an old folder that this sync database never synced, is refused instead of claimed by the signed-in Apple ID.
  - An approved deletion is skipped when the file became a folder, or came back on the PC, since the question.
  - A folder renamed in iCloud brings changes made to files inside it; a folder that iCloud lists only partly is never deleted in iCloud as a whole; folders whose names differ only in letter case stay apart.
  - Photos: a file already in place is compared byte for byte with the photo before it is adopted; otherwise the photo gets its own name.
  - Notes: a note missing from Apple's answer is removed only when the next full read confirms it; the index page is recognised by a mark at its top and is written through a uniquely named temporary file.
  - Sign out is remembered even if a session file can't be deleted, so a leftover session is never used.
  - Planning many folder deletions is fast.
- Second follow-up review (tests in tests/test_review4_fixes.py):
  - Photos: every file name that is already taken is compared with the photo, and an existing file is never replaced; album folders never lead outside the photos folder.
  - iCloud Drive: the sync database remembers which folder it synced, and another folder starts afresh; an approval is skipped when a file and a folder swapped places; edits on both sides close together keep both versions; nothing at all is decided inside a folder iCloud listed only partly; the spelling already synced keeps winning among names that differ only in letter case.
  - Notes: a note seen alive again isn't removed because of an earlier absence; a page that couldn't be removed is tried again.
  - Sign-in: only an accepted sign-in clears the "signed out" mark (a cancelled one doesn't).
  - Panel: brighter keyboard focus ring in dark mode, sections come back when there is room again, the panel is placed again after its final size is known, long file names are shortened safely.

### Changed
- Panel: no focus frame on the settings button when the panel opens; the activity list gives way on small screens so the buttons stay visible; each file keeps its own activity row; long folders are shortened; the deletion banner counts files; one Resume button when paused; better contrast.
- Deletion dialog and Find My fit small screens (long paths shortened, many devices scroll).

## [1.0.5] - 2026-11-09

### Changed
- New tray panel layout, inspired by the OneDrive panel: the sync status sits right under the title, recent activity reads like a file list (file name, what happened and where, when), the panel is taller, and a bottom bar holds Sync, Open folder, iCloud.com, Find My and Pause.

## [1.0.4] - 2026-11-09

Fixes from an independent code review (OpenAI Codex), each with a regression test.

### Fixed
- Photos: an album folder never replaces a file that Skyfold didn't put there.
- Notes: a note never overwrites an existing .html file it didn't write; a full read that suddenly misses most notes no longer removes their pages.
- Notes: drawings and PDFs are only replaced inside the notes' own attachments folder.
- iCloud Drive: a file whose size iCloud doesn't report is treated as "unknown", never as empty.
- iCloud Drive: planning very large deletions is fast (no more comparing every path with every other).
- Sign-in: a sign-in that was cancelled can't complete in the background later.
- Sign-out: session files are removed reliably, and no running request can write them back.
- Find My: the Map button says that it opens Google Maps.

## [1.0.3] - 2026-11-09

(1.0.1 and 1.0.2 were tagged but never released: their automated builds stopped on test-suite problems, now fixed. The app itself is unchanged.)

### Fixed
- iCloud Drive: a file that was only opened on an iPhone or iPad (which changes its iCloud version tag, not its content) is no longer downloaded again.

## [1.0.0] - 2026-11-08

First public release.

### Added
- **iCloud Photos:** downloads the whole library (photos, videos, Live Photo videos) to a folder of your choice, as one folder, by year or by year/month. Deletions are synced in both directions: PC files go to the Recycle Bin, iCloud items to "Recently Deleted".
- **Albums as folders** under `Albums\`, using hard links (no extra disk space).
- **iCloud Drive two-way sync:** full local copy or chosen top-level folders; new, changed, deleted, renamed and moved files and folders in both directions; conflicts keep both versions (`name (PC conflict …)`); Mac packages are left in iCloud.
- **Apple Notes export** to HTML with pictures and attachments, a searchable `Notes.html` index, and deleted notes moved to the Recycle Bin.
- **Find My:** device list with battery and last-seen time, map link, Play sound.
- **iCloud storage** usage in the tray panel.
- **Safety:** confirmation before more deletions than a limit (default 25), protection against missing or unplugged folders, disk-space check that keeps 2 GB free, no-warranty notice before the first sync.
- Sign-in with two-factor authentication (trusted-device or SMS code); the password is never stored. Session and database live in `%LOCALAPPDATA%\Skyfold`.
- Tray panel and settings with Windows light/dark theme, download speed limit, pause, Start with Windows, notifications.
- English and Bosnian.
- Portable build (`Skyfold-windows.zip`) from GitHub Actions, with a packaged self-test (`--selftest`).

[Unreleased]: https://github.com/zoranmod/skyfold/compare/v1.0.6...HEAD
[1.0.6]: https://github.com/zoranmod/skyfold/compare/v1.0.5...v1.0.6
[1.0.5]: https://github.com/zoranmod/skyfold/compare/v1.0.4...v1.0.5
[1.0.4]: https://github.com/zoranmod/skyfold/compare/v1.0.3...v1.0.4
[1.0.3]: https://github.com/zoranmod/skyfold/compare/v1.0.0...v1.0.3
[1.0.0]: https://github.com/zoranmod/skyfold/releases/tag/v1.0.0
