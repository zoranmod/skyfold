# Changelog

All notable changes to Skyfold are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed
- New look (redesign branch): flat, calm surfaces in light and dark mode; the tray panel has a quiet brand mark, a grouped service list with icon tiles, a segmented iCloud storage bar with a legend (Photos / Drive / Other; click for details), a one-line activity list, and a footer with one primary button plus icon buttons (Open iCloud.com and Pause moved into the "More" menu; when paused, the primary button is "Resume"). Dialogs use the same tokens. No change to what is synced or deleted.

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
- Twelfth follow-up review (tests in tests/test_review14_codex_probes.py):
  - A rename from iCloud that was cut short on this PC is settled by simple rules: only when the original item is still under the old name did the rename not happen; otherwise the database follows to the new name and the usual rules apply (deleted on this PC since -> deleted in iCloud with the usual approval; saved over by an editor -> uploaded). A file that can't be read for a moment is shown as an error and checked again. File ids are stored with their volume, and file systems without ids are handled.
  - Sign-in: errors are logged and shown only as error kinds, HTTP status codes and code locations, never Apple's page text (which can carry names, e-mail addresses or sign-in values).
  - Quit: the worker is marked as closing first, so a sync cycle still finishing can't start new iCloud Drive work.
- Thirteenth follow-up review (tests in tests/test_review15_codex_probes.py):
  - Recovering an interrupted rename from iCloud takes over iCloud's version only when it is exactly the one recorded before the rename (same version tag and item id); an iCloud edit or replacement made since stays a change and is downloaded or kept as a conflict, never trashed. Something new under the old name with nothing under the new one is no longer read as "renamed and then deleted".
  - Quit: a sync cycle still finishing doesn't start Notes, watchers or iCloud Drive any more.
  - Find My errors are logged as error kinds and codes only (never Apple's page text). Error summaries also keep the HTTP status and Windows error numbers.
- Fourteenth follow-up review: recovery of interrupted renames/moves in iCloud Drive redesigned (tests in tests/test_review16_codex_probes.py and tests/test_review16_fixes.py):
  - One rule everywhere: the database only describes what this PC has, and a new iCloud version tag is accepted without downloading only when size and modified time are those of the synced version (the item was merely renamed, moved or opened). Recovery after a crash just lets the database follow the item's path; it no longer keeps its own evidence about iCloud versions. An iCloud edit made before, during or after an interrupted rename is therefore always downloaded or kept as a conflict copy.
  - The same rule now also applies when a file or folder is deleted on this PC right after it was renamed in iCloud: the deletion goes to iCloud (with the usual approval) instead of the files coming back, while files really edited in iCloud are kept.
  - A rename that was cut short no longer waits forever when the old folder has become a file in the meantime.
  - Logs and error messages: an error that comes from Apple is written only as its kind, numeric codes and place in the code, everywhere (sync, storage, retries, Find My, sign-in); tracebacks are no longer written as text. Skyfold's own messages and Windows errors stay readable. A drawing that can't be used is logged without any of its bytes.
  - Quit: nothing starts after Quit begins, also not the start-up steps (session restore, folder watchers) or background requests still waiting for a free thread (Find My).
- Fifteenth follow-up review: iCloud Drive decides by content, not by size and time (tests in tests/test_review17_codex_probes.py and tests/test_review17_fixes.py):
  - Skyfold now remembers a fingerprint (SHA-256) of every file's synced content. A version in iCloud whose tag is new or unknown is treated as unknown until its content has been looked at: it is downloaded and compared. If it is what the PC already has, the PC file is left untouched (nothing goes to the Recycle Bin, no "downloaded" entry) and only the version is remembered; if it differs, it is an edit.
  - This replaces every shortcut that took equal size and modified time as proof: after Skyfold's own upload, after a rename or move (also one cut short by a crash), for "changed on both sides", and on a first sync into a folder that already has files. A file copied or restored in iCloud with the same size and time is no longer missed.
  - Deleting on the PC a file or folder whose iCloud tag has moved: the deletion goes to iCloud only when iCloud's content is proven to be the synced one; otherwise iCloud's version is kept and comes back. Files synced before this version have no fingerprint yet, so for them nothing is assumed (they come back once).
  - Cost: such files are downloaded once more just to compare them. Slower, but nothing is assumed.
  - Logs: an error's message is shown only when Skyfold worded it itself or when it is a plain Windows/database error about a file - decided by where the text comes from, not by the kind of error. Apple's refusal codes are reduced to short symbolic codes.
  - Quit can't be undone by a pass that is just starting: each service has a stop that is never cleared once quitting began.
- Sixteenth follow-up review: the PC side is checked by content too (tests in tests/test_review18_codex_probes.py and tests/test_review18_fixes.py):
  - Notes: a previous page is deleted only when it is provably Skyfold's (its recorded fingerprint, or byte for byte the page just written); a page from before 1.0.6 without a fingerprint goes to the Recycle Bin.
  - iCloud Drive: before a PC file is replaced by a download or sent to the Recycle Bin because it was deleted in iCloud, its content is compared with the fingerprint; a file changed while keeping its size and time is kept (as a conflict copy, or uploaded). The comparison during a download also checks that the file is still the very same file afterwards. Each full pass re-reads up to 64 MB of files that look in sync, in turn, to find such silent edits and upload them.
  - A content check counts only when the whole file arrived (length checked; a partial HTTP answer is refused).
  - Photos: a file with the same name and size elsewhere in the folder is taken as the moved photo only when its content equals iCloud's copy. If it differs, the photo is forgotten on this PC only; nothing is deleted in iCloud and the other file is left alone.
  - Quit while an upload is being prepared: nothing is trashed or uploaded any more.
  - Disk space: downloads that only compare, or replace a file of the same size, no longer need free space for the whole batch - only for what really grows plus the files in flight.
- Seventeenth follow-up review (tests in tests/test_review19_codex_probes.py):
  - Photos moved inside the folder: every file with the same name and size is compared with iCloud's copy (not just the first one found), each file is matched to one photo only, and a download that didn't arrive completely decides nothing. When no look-alike is the photo, Skyfold only stops tracking it on this PC: nothing is deleted in iCloud and no file (e.g. its Live Photo video) is touched.
  - iCloud Drive: right before an approved deletion is carried out, the PC file's content is checked again (a file edited after the question, even with the same size and time, stays).
  - Quit or Pause: no further change in iCloud starts - not the next new folder, and not the second step of a move (it stays written down and is finished next time).
  - Disk space for downloads running at the same time is reserved in one place, and the free space is read afresh for each file (a replaced version stays on the disk, in the Recycle Bin).
- Eighteenth follow-up review: a decision to delete is looked at again right before it is carried out (tests in tests/test_review20_codex_probes.py and tests/test_review20_fixes.py):
  - Photos: a photo found missing on this PC is not deleted in iCloud if it is back by the time the deletion would start (Undo in Explorer, an editor's save), and the clean-up afterwards never touches a photo put back. A photo edited on this PC stays when it is deleted in iCloud.
  - Photos moved inside the folder: while one comparison couldn't be made, none of the photos that share its look-alike files is decided.
  - iCloud Drive: a folder deleted in iCloud is looked into again right before it goes to the Recycle Bin; files added or changed in it meanwhile stay and are uploaded.
  - Pause takes effect the moment it is pressed, also while a sync cycle is busy: no further service starts.
- Safer default: new installations ask before deleting more than 5 items at once (was 25). Existing settings are kept.
- Nineteenth follow-up review (tests in tests/test_review21_codex_probes.py and tests/test_review21_fixes.py):
  - Log: what other libraries (pyicloud, requests…) write is no longer trusted. Only their own format string and numbers are kept, with the place in the code; every text argument is blanked, a finished one-piece message is withheld, and an error used as the message is reduced to its kind. Apple's answers can no longer reach the log that way.
  - Photos: Skyfold remembers a fingerprint (SHA-256) of every photo and video it downloads. Before a file goes to the Recycle Bin because the photo was deleted in iCloud (or as a leftover), its content is compared: a photo edited on this PC stays, also when the edit didn't change its size. Photos downloaded by earlier versions have no fingerprint yet; for them only the size can be compared.
- Twentieth follow-up review (tests in tests/test_review22_codex_probes.py and tests/test_review22_fixes.py): Skyfold's own log lines no longer print values that come from Apple as they are. Sign-in status flags, the way a code was sent, and the details of a drawing in Notes are written only as real booleans and numbers, or as a word from a fixed list for that field; anything else is logged as "invalid" and lists are only counted.
- Twenty-first follow-up review (tests in tests/test_review23_codex_probes.py and tests/test_review23_fixes.py):
  - Accounts: a Settings window that was open while another Apple ID signed in (or out) is closed, and whatever it still saves is ignored; Settings can never change who is signed in. Whose iCloud Drive folder it is now comes from the session actually in use, so a folder that belongs to another Apple ID is refused ("choose a new, empty folder") instead of its files being uploaded into the wrong account.
  - Log: when iCloud refuses a rename, move or other change, only a code from a fixed list of known codes (CONFLICT, ITEM_NOT_FOUND…) or a number is kept; anything else is written as "other".
  - Notes: the clean-up of leftover pages removes a page only when it is named like a note's page in that folder and is byte for byte a page Skyfold wrote. A copy you made under another name, or one you changed, is yours and stays.
- Twenty-second follow-up review (tests in tests/test_review24_codex_probes.py and tests/test_review24_fixes.py) - Notes and files Skyfold doesn't track:
  - A new or renamed note never takes the name of a file that already exists and that Skyfold doesn't track, whatever the file looks like (also a copy of one of its own pages): the note gets the next free name instead.
  - The clean-up of leftover pages no longer recognises pages by name or content. Skyfold writes down a page (with its fingerprint) only when it meant to remove it after a rename and couldn't; only such a page, still unchanged, is removed later. Copies you make - "Budget (2).html", "Budget - Copy.html" - are never touched.
- Twenty-third follow-up review (tests in tests/test_review25_codex_probes.py and tests/test_review25_fixes.py) - Skyfold tidies up only what it wrote down as its own:
  - Private working files (unfinished downloads and exports, files moved aside while a new version is put in place) are written down before they are created, in Notes and in iCloud Drive. After a crash only those are removed, recycled or put back. A file of yours that merely has such a name (".skyfold-tmp-…") is never touched, renamed or uploaded, however old it is.
  - Notes: a Notes.html that looks like Skyfold's index but that this setup never wrote (a copy brought into the folder, a new database) is left exactly as it is; Skyfold says so in the activity list and writes no index until you move it.
  - Album folders: a file found at an album path is removed only when it is the very file Skyfold put there. A picture you saved there instead stays. Empty album folders are removed only when Skyfold made them; your own empty folders under Albums stay. (Album folders made by earlier versions are no longer removed when they end up empty.)
  - The portable-mode write test creates a new file of its own and no longer deletes an existing data\.write-test.
- Twenty-fourth follow-up review (no release blockers; six smaller findings fixed; tests in tests/test_review26_codex_probes.py and tests/test_review26_fixes.py):
  - iCloud Drive: a replaced version on its way to the Recycle Bin stays written down under its readable name too ("name (previous version)"), together with which file it is. If Skyfold stops at that moment, the next sync finishes the move instead of uploading it as a new file. Records of private files that no longer exist are dropped.
  - Album folders: an entry is treated as Skyfold's only when its recorded identity is known and matches; an unknown identity never counts as a match. An entry that was under its private name when Skyfold stopped gets its name back. An album folder is removed only when it is the very folder Skyfold made, not another one created later under that name.
  - Notes: when the Recycle Bin refuses a previous page or index, the file stays written down and the move is tried again on the next sync.
  - A marker file from an earlier version of the app (.isync, .galerija-sync) is taken over only when the database really tracks files in that folder; otherwise a file with such a name is left alone.

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
