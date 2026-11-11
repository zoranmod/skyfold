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
- Third follow-up review (tests in tests/test_review5_fixes.py):
  - Photos: a new photo is put in place with an operation that refuses an existing name, so a file saved there at the same moment is never overwritten; album cleanup never removes a link or junction.
  - iCloud Drive: a database from before 1.0.6 is only used for the folder its cache shows it synced; an approved folder deletion waits when the folder can't be read completely; edits on both sides keep both versions unless it is provably Skyfold's own download; nothing is uploaded or downloaded inside a folder iCloud listed only partly; quick checks keep the spelling already synced; a few transferred files get one activity entry each.
  - Notes: a locked note whose old page couldn't be removed yet is tried again.
  - Panel: keeps its exact size when placed, and is placed again after the size changes while it is open.
- Fourth follow-up review (tests in tests/test_review6_fixes.py):
  - Photos: an album entry is removed directly only when it is provably a second name of the photo; anything else there goes to the Recycle Bin.
  - Notes: a page is overwritten or removed only when Skyfold wrote it; a personal file at a note's old path is left alone and the note gets a new name.
  - iCloud Drive: renames keep each file's previous iCloud version tag, so an edit made in iCloud meanwhile still comes down; another folder is checked before anything the database knows is forgotten; a download is put in place by moving the old file aside first (never overwriting a save made at that moment); a folder answer with an error status is never read as empty; nothing is renamed across a partly listed folder, and its last known contents stay remembered; an approved folder deletion waits when a link or junction appeared inside.
  - Panel: progress, pending deletions and state changes all use the same sizing, so the buttons stay on screen; service rows are never squeezed below their text; a few transferred files in one pass get one entry each; files without an extension show name and folder.
- Fifth follow-up review (tests in tests/test_review7_fixes.py):
  - iCloud Drive: the PC version replaced by a newer iCloud download goes to the Recycle Bin (as "… (previous version)") instead of being deleted, and any failure while putting a download in place gives the PC version back or keeps it as a conflict copy; a version left aside by a crash is shown again on the next full sync; a partly listed root keeps what it held before, so the spelling already synced keeps winning; a rename held back by a partly listed folder waits as a whole (nothing created or deleted halfway); an approval given during a pass is checked against iCloud again, and a folder that got something new waits for a new question.
  - Notes: pages and the index are written under a private name and put in place without overwriting; a personal file that appears there is given back; a previous page that was changed after Skyfold wrote it goes to the Recycle Bin.
  - Photos: album entries are created without ever overwriting a file saved at that moment, and removed only after being moved to a private name and checked; files are never moved to the Recycle Bin through a link or junction.
  - Sign-in: whose session may be saved is checked under the same lock that sign-in and sign-out use.
  - Log: cookie values and tokens are removed from the log, and sign-in errors log only their kind.
  - Release: the zip is built from an allowlist and checked for settings or sign-in data; a release tag must match the version and be on main.
  - Panel: the content scrolls when the screen is small, so the buttons always stay visible; the panel is never wider than the screen; the Photos, iCloud Drive and Notes rows can be reached with Tab and opened with Enter or Space.
- Internal audit (tests in tests/test_audit_fable_fixes.py):
  - Recycle Bin: deletions use Windows' modern file operation (pywin32), which fails instead of deleting for good when something can't be recycled; nothing is deleted on network shares or removable drives without a Recycle Bin.
  - iCloud Drive: a folder renamed in iCloud and first seen by a quick check is renamed here (not deleted and downloaded again); new nested folders and their files go up in one pass; a folder Skyfold made that vanished from iCloud is never stuck; a file that iCloud replaced with a folder is never mistaken for a deletion; a move never passes through a name that is taken in the target folder, and an item Apple refuses is never taken as moved; changes saved during a full pass stay queued; iCloud's time is set before a download is put in place; leftovers of interrupted downloads are tidied; long file names can be updated.
  - Photos: a download that keeps failing waits for the next full read instead of re-reading the whole library every poll; bringing back a deleted folder brings back Live Photo videos too; album entries follow the album when two photos share a name; moving a photo into the Albums folder isn't a deletion; a photo iCloud didn't answer for isn't forgotten.
  - Notes: the tidy-up never goes through a link and only looks in the notes' own folders; a note that keeps failing is still tried again; a page whose attachment didn't download keeps the previous page; private files left by a crash are tidied; long folder names ending in a space work.
  - Sign-in: the sync database is tied to the Apple ID; signing out can never undo a newer sign-in; another Apple ID's old session files are removed; a signed-out start says so instead of "session expired"; every request (also while asking for the code) has a timeout; one Skyfold per Windows user, even with two quick launches.
  - Log: a new filter removes the Apple ID in every spelling, cookies, tokens, passwords and people's names, without blurring ordinary lines, and stays fast on long text; unexpected errors are logged.
  - Panel and dialogs: no needless scroll bar; the panel no longer closes when a new entry arrives; activity entries and the first-run layout choice work with the keyboard; clearer focus rings and scroll bar; a long Apple ID no longer cuts off Settings; icon-only buttons on very narrow screens.
  - Release: the tag reaches the build as data only; the checksum file uses LF line endings; the zip check also covers database side files, rotated logs and the Drive listing.
- Sixth follow-up review (tests in tests/test_review8_fixes.py):
  - Recycle Bin: every item is checked the moment Windows is about to delete it, and the operation is stopped if it wouldn't go to the Recycle Bin (e.g. a subst drive, a disabled or full Recycle Bin); the file then stays where it is.
  - iCloud Drive: a rename coming from iCloud never replaces a file saved at that moment under the new name; a move that iCloud refuses after the rename is remembered as it is and finished on the next pass, never leaving two copies.
  - Notes: the index's fingerprint is taken from Skyfold's own private file before it is published, so an edit made right after is never taken for Skyfold's.
  - Log: lines from a damaged cookie file and long hex values never reach the log.
  - Restart (after changing the language): the old copy lets go of the single-instance lock and the new one waits for it to quit.
  - Sign-in and first-run windows fit small screens at high scaling.
- Seventh follow-up review (tests in tests/test_review9_fixes.py):
  - Notes: the previous index is deleted only when it is provably exactly what Skyfold wrote; if its fingerprint is unknown (e.g. after a crash or a rebuilt database) it goes to the Recycle Bin.
  - iCloud Drive: a rename/move in iCloud is written down before it starts. If it is cut short (refused halfway, a crash, an answer that never arrived), the next full pass finds the item by its iCloud id and continues from where it really is, in either order of the two steps, never leaving two copies.
  - Log: an error about a damaged cookie file never quotes the cookie, whatever the value looks like.
  - Restart: the old copy keeps the single-instance lock until it has ended; the new copy waits for that and gives up if the old one doesn't end, so two copies never run at once.
- Eighth follow-up review (tests in tests/test_review10_fixes.py) – interrupted renames/moves in iCloud Drive:
  - Each item has its own record (a second move never erases the first one's), and the database rows move together with that record in one transaction, so a crash can't leave one without the other.
  - Nothing is settled while iCloud lists any of the folders involved only partly: a moved folder is never uploaded a second time.
  - iCloud's version is taken over only when size and modified time prove it is the same file; an edit made in iCloud meanwhile is downloaded, even one of the same size.
  - An item deleted in iCloud before the move was settled is deleted on the PC as well (Recycle Bin, with the usual approval for many files) instead of being uploaded again.
  - When the PC copy is still the one that was moved, the move is finished right away.
- Quitting or pausing while a pass runs no longer shows a "Sync error" in the panel (e.g. "cannot schedule new futures after shutdown"); it is a stop.
- Ninth follow-up review (tests in tests/test_review11_codex_probes.py and tests/test_review10_codex_probes.py, Codex's own reproductions):
  - iCloud Drive conflicts: the PC copy's new "(PC conflict …)" name is taken with a rename that refuses an existing file, so a file another program saves there at that moment is never overwritten.
  - Interrupted moves: a retry keeps iCloud's size/time from when the move first started, so an iCloud edit made in between is always downloaded; a finished move updates the database and drops its record in one transaction; anything still unsettled is kept out of the rest of the pass (no duplicates); records from the previous build are completed from the database; an incomplete listing of the Drive's top level holds every pending move; switching the Drive folder forgets the moves written down for the old one.
  - A failed download no longer looks like "Sync stopped": sign-in loss, iCloud access problems and database errors are reported again.
- Tenth follow-up review (tests in tests/test_review12_codex_probes.py and tests/test_review12_fixes.py):
  - iCloud Drive downloads: the database records the size and time of the version Skyfold put in place, so an edit another program makes a moment later is uploaded instead of being taken as already synced.
  - A rename or move coming from iCloud is written down before the PC file or folder is renamed, and the database follows in one transaction: a crash in between can no longer make the folder look new on the PC and upload it again (e.g. after it was deleted in iCloud).
  - A rename that can't be finished in iCloud (e.g. the target folder was deleted there) is let go and synced as add + delete instead of waiting forever.
  - Pause: no further Drive pass starts after Pause; the changes waiting stay queued for Resume.
  - Build: folders on PATH that bring their own ICU (e.g. Poppler) are left out while packaging, so Qt always gets its own ICU.
- Sign-in: when Apple answers the request for the two-factor page with status 409 together with the page, the sign-in continues with that page instead of stopping with "Authentication required for Account (409)".
- Eleventh follow-up review (tests in tests/test_review13_codex_probes.py):
  - A rename from iCloud that was cut short on this PC is decided by the item's file id: a new item made under the old name, or a folder that can't be read for a moment, never makes Skyfold forget the rename (and upload a folder deleted in iCloud again). When it can't be told, both are left alone and an error is shown.
  - A file renamed in iCloud keeps its synced version's size/time on this PC, so an edit saved right after the rename is uploaded.
  - Session restore: an Apple server error seen while the saved sign-in is checked keeps it an outage (Skyfold retries) instead of ending as "No password set" and asking to sign in; error codes sent as text ("503") count too.
  - Stop and Quit: no further iCloud Drive pass starts after Stop or Quit; the next sync request starts it again.
- Autostart: the Windows Run entry is written only when it changes (e.g. the app was moved), not on every start.

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
