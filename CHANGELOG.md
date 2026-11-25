# Changelog

All notable changes to Skyfold are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **A Photos window, as on an iPad.** Click *Photos* in the panel (or *Photos* in the tray menu): your synced photos and videos in an edge-to-edge grid, oldest at the top and the newest at the bottom, with *Years*, *Months* and *All Photos*. The sidebar has *Recently added* (by when a photo reached iCloud, so an old photo scanned today is there too), *Favourites*, *Videos*, *Live Photos*, *Screenshots* and your albums. `Ctrl` + mouse wheel (or `Ctrl +` / `Ctrl -`) makes the photos larger or smaller around the one under the mouse; *Search* finds file names, months, years, albums and kinds.
- **The viewer** shows a photo large (click it to see only the photo, on black), steps with the arrow keys or the strip below, and its *Info* says when it was taken, its size, its albums and where its files are on this PC (a Live Photo's photo and video both). *Play* opens a video or a Live Photo's video in your usual player; *Show in folder* and *Open* do what they say.
- **Photos come back at once.** The small pictures are Windows' own previews, the ones File Explorer keeps on disk, so a photo seen once appears in a moment the next time, even after closing the window or restarting Skyfold. Skyfold itself stores nothing extra. Big pictures (the viewer, *Years* and *Months*) are kept apart from the grid's, so looking at them never makes the grid start over.
- **Scrolling glides.** Each notch of the mouse wheel eases the photos along instead of jumping, and quick notches add up, as on an iPad (in *Years* and *Months* too). A touchpad moves them as it always did. With Windows' animation effects switched off, the wheel moves at once.
- **The mouse's Back and Forward buttons** work in the Photos window: Back goes from a photo to the photos, then from *All Photos* to *Months* and *Years*; Forward goes the same way back in, as in a web browser.
- **The window keeps your place.** Maximizing it, or making it larger or smaller, keeps the same photos in view, and the photos always fill the width at every size (no empty column on the right). In a narrow window the sidebar steps aside (the ☰ button still opens it) and the selection bar gets shorter.
- **Nothing gets selected by itself.** A press and drag beside or over the photos, a right-click, or Enter in the search box no longer selects photos or switches on *Select*; a double-click on a photo only opens it.
- **The pictures are ready ahead.** The photos a screen above and below are made while you look, the photo you stop on in the viewer comes first when you step quickly, and opening the window again within ten minutes shows the photos at once.
- **Smoother details:** the floating bars and buttons have smooth round edges, search waits for a pause in your typing, numbers and dates follow the language (1,284 / 1.284), and Bosnian words (months, *video*, *omiljene*) find photos too.
- **Safer details in the Photos window:** a photo changed in place gets a fresh picture; closing the window never waits for a big picture still being made; hearts sent while offline stop at the first lost connection instead of trying every photo; a click opens the photo pressed even if the list changed meanwhile; Favourites drops an unhearted photo once iCloud agrees, and two quick failed clicks go back to what iCloud has; a deleted photo put back from the Recycle Bin shows again; in a narrow window *Info* and the sidebar never squeeze the photo away.
- **The heart works both ways.** Mark a photo as a favourite here and it is a favourite on your iPhone too (and the other way round). If iCloud can't be told, the heart goes back and you're told why.
- **Delete from the Photos window** asks first (*Cancel* is the default), then moves the files to the Recycle Bin, exactly as deleting them in the folder would, so iCloud gets them in *Recently Deleted* at the next sync (or keeps them, if you chose that in Settings). Only the files Skyfold synced for that photo go: one moved or replaced in File Explorer meanwhile is left alone. A Live Photo goes as a pair; if its photo can't be deleted (open in another program), nothing is deleted in iCloud and its video comes back.
- Pictures come from Windows itself (the previews File Explorer shows), so the window takes no extra disk space and Skyfold stays the same size. HEIC photos need Microsoft's free *HEIF Image Extensions*, which most Windows 11 PCs already have; the window says so if they're missing.

### Changed
- **The tray panel keeps its height while it's open.** When *Sync now* starts (or a sync ends), the "Syncing…" card no longer makes the whole panel jump: the list of recent activity makes room instead. Opened again, the panel has its natural height.
- After this update Skyfold reads your photo library once in full (nothing is downloaded again), to learn each photo's favourite, date added, size and length. From then on these come with every sync.
- The panel's folder button opens the folders again for Photos, iCloud Drive and Notes; a click on the *Photos* row opens the Photos window.
- **Replacing a file in iCloud Drive is safer.** When a file changed on this PC goes up, its new contents reach iCloud first; only then is the old version moved to *Recently Deleted*. A transfer that fails (no connection, Stop pressed) no longer leaves the file missing on your other devices.
- **A lost connection no longer puts photos aside for a day.** When downloads fail because the network is gone or Apple asks to wait, Skyfold stops after three and tries again at the next check, instead of marking every remaining photo as failed until the next daily full check.
- **A photo hidden on your iPhone stays on this PC.** Hiding isn't deleting: the file is kept as an ordinary file instead of going to the Recycle Bin. A photo missing from one full read of the library is also asked about on its own before anything happens to its file.
- A photos folder on a drive without a Recycle Bin no longer gets stuck: a photo deleted in iCloud is kept on this PC (and you're told), instead of the same error at every check.
- Switching on videos or Live Photo videos in Settings fetches the ones skipped so far right away, not at the next daily full check.
- Locking a note on your iPhone now deletes its page and pictures on this PC outright, instead of putting a readable copy in the Recycle Bin. A Notes folder or title called *Con*, *Aux*, *Nul* and the like (names Windows keeps for itself) is now saved with a `_` in front instead of failing every time.
- Half-downloaded photos left by a crash or power cut are cleaned up, and a downloaded photo gets its date before its name.
- *Pause* also stops a sync that was already waiting to start, and saving *Settings* no longer undoes an update notice that came while the window was open.

## [1.0.30] - 2026-11-24

### Changed
- **Fewer false virus alarms.** Skyfold.exe now starts with its own small starter program, compiled from PyInstaller's source when the release is built, instead of the ready-made one that countless other programs (malware included) share. Antivirus programs that judge by resemblance, like Windows Defender's `!ml` detections, had flagged Skyfold because of it.
- **Start with Windows is written only when you switch it on.** Skyfold no longer rewrites Windows' autostart entry by itself at every start (antivirus programs treat a program that keeps re-adding itself there as suspicious). If the entry points at an older copy or was removed, *Settings → Start with Windows* shows it off; switch it on to point Windows at this copy.

## [1.0.29] - 2026-11-24

### Added
- **Connection diagnostics for bug reports** (**Settings → General**). *Record for 30 minutes* writes `connections.log` with only the kind of each request, Apple's status code and how long it took: never a name, a file name, an address, an id, a cookie or a password. It stops by itself after 30 minutes (also if the PC slept), at sign-out and when Skyfold quits, and *Delete* removes the file.

### Security
- **Every request is checked before it is sent.** Skyfold's own requests, and each redirect, may only go to Apple's iCloud servers or GitHub's update servers over HTTPS; anything else is refused before a byte is sent and the panel says so. The iCloud sign-in never goes to GitHub. The packaged self-test checks this too.
- **The update window's *What's new* text loads nothing by itself** (no picture from the internet or a network share); links still open in your browser when clicked.

### Changed
- The privacy section of the README now says that the ordinary log can contain note titles and album names, and which links open in your browser.

## [1.0.28] - 2026-11-24

### Changed
- **Scanned documents open with all their pages** (up to 60; *Show more pages* then shows the rest at once, as many as it says). Pictures are loaded as they come into view, so a long scan still opens quickly.
- **The Notes window comes back where you were.** Close it and open it again (while Skyfold runs) and it shows the same folder and note, scrolled to the same place. Sign out or another Apple ID forgets it.
- **The panel says what happened to a note or file.** A note you moved to another folder on your iPhone, iPad or Mac now shows as *moved to* that folder, a note with a new title as *renamed*, and an edited note as *updated*, instead of *downloaded* for all of them. In iCloud Drive, a newer version of a file you already had shows as *updated* (moved and renamed files were already shown as such).

### Fixed
- **Skyfold could crash or freeze with the Notes window open** shortly after it started. When the saved sign-in was restored, the Notes window was closed from Skyfold's background thread instead of its window thread, which Qt doesn't allow. It is now closed on the right thread, and only when another Apple ID signs in (or you sign out); the same Apple ID signing in again, as at every start, keeps the window open. Each note also gets a fresh page of its own, and *Show more pages* and `Ctrl` + mouse wheel reload the note only after Qt has finished handling the click or scroll.
- **If Skyfold ever crashes, it now says where.** A `crash.log` file next to `skyfold.log` (in `%LOCALAPPDATA%\Skyfold`) records which part of the program was running. It contains no note text, file contents or password; attach it to a bug report.

## [1.0.27] - 2026-11-24

### Added
- **Read your notes inside Skyfold.** Click *Notes* in the panel (or *Notes* in the tray menu) for a Notes window: folders on the left as in Apple Notes (with their note counts, and subfolders you can fold), your notes with their date and first line (newest first, oldest first or by title), and the note itself on the right, with its checklists, tables, pictures and scans. Search finds words in the titles, folders and the text of your notes, with or without č, ć, đ, š, ž, and marks them in the note. Make the text larger or smaller (`Ctrl +`, `Ctrl -`, `Ctrl 0` or `Ctrl` + mouse wheel) and fill the screen (maximize, or `F11`); the ⋯ menu also gives the window its own theme (light, dark or OLED) and opens the note in your browser as before. Attachments open in their usual app. Locked notes are listed but never shown, a note that gets locked or deleted leaves the window as soon as Skyfold's sync has noticed it, and after *Sign out* the window shows nothing until you sign in again. Whatever way the window is closed, the text it read is dropped. `Ctrl+K` searches, `F6` moves between the parts of the window.

## [1.0.26] - 2026-11-24

### Fixed
- **iCloud storage shows your plan's real size.** A 200 GB plan read "214.7 GB", because Apple counts a gigabyte as 1024 MB here and the panel counted 1000. The total, the used amount and the Photos / Drive / Other parts now use Apple's units, so the numbers match iPhone Settings.

## [1.0.25] - 2026-11-23

### Changed
- **Each service has its own colour**, in every theme: Photos pink, iCloud Drive blue, Notes amber, Safari bookmarks violet, on tinted tiles in the panel and as icons in Settings (the same colours as on the website).
- **A soft glow** lights up a service's tile while the pointer is over its row (or the row has keyboard focus), and the Skyfold mark in the panel's header glows under the pointer.
- **The sync arrows next to "Syncing…" turn** while a pass runs.
- With animations turned off in Windows, the glow appears without fading and the arrows stay still.

## [1.0.24] - 2026-11-23

### Changed
- **Safari bookmarks have their own page in Settings** (*Settings → Bookmarks*), with its own on/off switch, the folder for the bookmarks file, how to import it in Firefox, Chrome or Edge, and what is left out. They used to be on the Notes page.

## [1.0.23] - 2026-11-23

### Fixed
- **Settings: changing your mind no longer deletes a Drive folder.** If saving failed and you chose to keep syncing a folder you had first set to recycle, Skyfold could still recycle it and then delete it in iCloud. The cleanup now follows only the choice finally saved.
- **Recycling a folder you stop syncing checks the contents**, not only size and time: an edited file with the old size and time stays on the PC.
- **iCloud Drive: two files whose names differ only in capital letters** (Windows can hold only one) no longer let a deleted PC folder take the other one with it in iCloud.
- **Notes:** pictures a failed export had already downloaded go away when the note is locked or deleted; a PDF that couldn't be downloaded is tried again instead of staying an online link; a note deleted while Apple reset the change token is removed from the PC at the next check.
- The panel says uploads to iCloud Photos are waiting while a refused photo waits for its next try.
- *Stop* during a long iCloud Drive listing stops asking iCloud for more folders right away.
- A file still being saved in the Drive folder waits a few seconds before it is looked at again, instead of being listed again and again.

### Known limitation
- A file you shared on its own from iCloud Drive and then edit on the PC becomes a new document in iCloud (see README, Limitations).

## [1.0.22] - 2026-11-22

### Fixed
- Screen readers name the fields of the sign-in window: Apple ID, password and the verification code.

## [1.0.21] - 2026-11-22

### Fixed
- **Stopping to sync an iCloud Drive folder keeps your unsent work.** Choosing *Move to Recycle Bin* for folders you no longer sync only recycles files that are exactly what iCloud has; anything new or changed on this PC (not in iCloud yet) stays, and you are told.
- **A file on this PC where iCloud has a folder of the same name** no longer sends that folder's files in iCloud to Recently Deleted; nothing below it is decided until you sort it out.
- **Notes: an expired change token** no longer keeps new edits from coming down; Skyfold reads all notes once and goes on as usual.
- **Notes: a picture of a deleted or locked note that was open in a viewer** goes to the Recycle Bin on a later check, once it is closed.
- iCloud Drive: a file still being saved is looked at again a few seconds later instead of at the next hourly check.
- When uploads to iCloud Photos have to wait, the panel says *Downloads are up to date. Uploads to iCloud Photos are waiting.* instead of *Everything is in sync*.
- Settings opens tall enough for its longest page, so nothing needs scrolling (as far as the screen allows); the download-limit hint sits under *Download speed limit* again, and *Automatic (recommended)* is no longer cut off.
- README: new screenshots (Settings with the upload send buffer) and a note on upload speeds.

## [1.0.20] - 2026-11-21

### Changed
- **Photos and videos go up to iCloud Photos two at a time**, each on its own connection, like new iCloud Drive files. Each file stays locked against changes while it is sent, and every decision about it is still made in one place.
- **A photo iCloud refused is sent again.** Before (also in 1.0.19), a refusal from iCloud (busy, or this file refused) could be taken for a lost reply, and the photo was never sent again. Now only a registration whose answer really got lost counts as maybe sent. A broken connection stops the other uploads and they continue on the next check; Stop pressed while a video is read sends nothing; one video under two names (hard links) goes once.
- **When uploads to iCloud Photos have to wait** (iCloud busy, no access, the upload service missing for the account, a connection that couldn't be made), photos from your iPhone still come down in the same check, and the waiting files go up on the next check without being marked as sent.
- The panel no longer stays at "Checking for changes…" after everything has finished: when iCloud Drive and Photos ended at the same moment, an older status could arrive after the newer one.
- **Upload send buffer is automatic now** (Settings → Upload send buffer; *Fixed 8 MB* stays available). Windows sizes it while sending. Measured on a 200 Mbit/s line, 3–3.7 GB videos one after another: automatic 13.1 MB/s, fixed 8 MB 11.0 and 3.5 MB/s. The log records the speed of every upload over 1 MB with the buffer it used.

## [1.0.19] - 2026-11-21

### Changed
- **Faster uploads to iCloud Drive.** Each upload now gets an 8 MB send buffer. On a 200 Mbit/s fibre line, one upload went from a steady ~0.5 MB/s to about 1.4–7 MB/s, and several files together reached ~10 MB/s; Apple's servers vary. **New files go up three at a time**, each on its own connection, so several files together go faster. A file that replaces one in iCloud still goes alone, after them. An upload no longer holds up Photos and Notes while it runs.
- **iCloud Drive uploads big files without loading them into memory.** A large video or archive is sent from disk piece by piece; before, the whole file (and a second copy while sending) had to fit in RAM.
- **Uploads show their progress** (MB sent, speed, time left) for iCloud Drive and iCloud Photos, like downloads. *Stop* stops a big upload in the middle; it is sent again from the start next time.
- **Renaming or moving a Notes folder on your iPhone or Mac moves its pages too.** The first check after updating reads all notes once to catch renames made before.
- iCloud Drive: deleting thousands of files or folders at once is counted much faster before the question appears, and a quick check of an unchanged folder no longer rewrites the whole Drive cache file.
- iCloud Drive: after an upload, the version iCloud confirms is remembered, so the next check doesn't download the file again to look at it.

### Fixed
- **A file being uploaded can't be changed meanwhile.** Other programs can read it, not change it (as for Photos uploads), so iCloud never gets a mix of two versions. A file replacing one in iCloud is locked *before* the old version goes to Recently Deleted: a file still open for writing in another program never costs the iCloud copy.
- **An upload counts only when iCloud confirms it.** Skyfold checks that iCloud received the whole file and named the new document; an unconfirmed upload is tried again, and the PC file is never taken for deleted in iCloud. *Stop* pressed while iCloud is answering keeps the file from being registered; uploads iCloud had already confirmed are still written down.
- A lost connection during uploads stops the other uploads too; files that replace ones in iCloud then wait for the next check.
- When the reply to a replaced file got lost, the next check compares the file in iCloud with the one on the PC instead of making a `(PC conflict …)` copy of identical contents.
- Photos: an upload iCloud reports with a different size is not registered and is tried again later.
- When a replacement was interrupted and another device saved a different version of the same size meanwhile, both are kept; before, the PC edit could go to the Recycle Bin.
- A file saved under another name in iCloud (the name was taken meanwhile), or a check that doesn't list a just-uploaded file yet, no longer counts as the file being synced or deleted.

## [1.0.18] - 2026-11-20

### Fixed
- **iCloud Drive: a retried replacement no longer removes a newer save from another device.** If replacing a file in iCloud was interrupted (connection lost, PC restarted) and a Mac or iPhone saved a new version meanwhile, the retry moved that new version to Recently Deleted too. Now both are kept: iCloud's version keeps the name, the PC version becomes a `(PC conflict …)` copy.
- **iCloud Drive: deleting a folder on the PC keeps iCloud items you never saw on it.** Items iCloud marks as hidden (and names Windows can't show) stay in iCloud; only the synced files go to Recently Deleted.
- **iCloud Drive: a deletion or a new folder that iCloud refuses is no longer taken as done.** It is reported and tried again; before, a refused new folder could even be removed from the PC.
- **iCloud Drive: downloading new files needs less free space.** A new file was counted twice when checking the free space.
- **Notes: pictures of a locked or deleted note go to the Recycle Bin with its page.** Before, the page went, but its pictures stayed readable in `_assets`. Pictures you changed or added yourself stay where they are.
- **Notes: editing a note no longer piles up copies of its pictures.** The copies an earlier export made go to the Recycle Bin once the new page is in place.
- **Notes: a note that couldn't be downloaded is shown in the status** instead of "Everything is in sync".
- Bosnian Notes.html shows "21 bilješka" (not "1 bilješka") for 21, 31, 101 … notes and folders.
- *Check for updates* no longer says "You have the latest version" when a newer release is published but its download isn't complete yet.
- Screen readers name every control in Settings, and say which folder each *Change…* button is for.
- README: the iCloud Drive "more than half" question applies from 5 items up (as the app always did).

## [1.0.17] - 2026-11-20

### Added
- **OLED theme.** Settings → General → Theme → *OLED* (Bosnian: *OLED (crna)*): a pure black background with Apple's dark system greys for cards and Apple's blue accent. System, Light and Dark look exactly as before. The note pages and Notes.html get it too: their theme switch now cycles light / dark / OLED and remembers the choice; pages follow the app's theme until you pick one there. Pages Skyfold wrote are restyled in place once; pages you changed yourself are left as they are.

## [1.0.16] - 2026-11-19

### Changed
- **Photos upload is now one-way.** A photo or video sent from the photos folder stays an ordinary file on this PC: deleting or renaming it on the PC doesn't touch it in iCloud, deleting it on the iPhone doesn't touch the PC file, and it isn't downloaded back as a second copy. (In 1.0.15 a sent file became a synced photo; several code reviews kept finding rare cases where that link could delete the wrong file, so it is gone.) Photos already sent with 1.0.15 and matched stay synced as before (if iCloud loses such a photo, its PC file is kept rather than put in the Recycle Bin); ones still waiting for iCloud become one-way. When an older photos folder is used by 1.0.16 for the first time, the photos and videos already in it that Skyfold doesn't sync are never sent (in 1.0.15 another Apple ID could have used the same folder).
- **A photos folder belongs to one Apple ID.** It records which one in a hidden `.skyfold-account` file. Signing in with another Apple ID asks you to choose a photos folder for it, as iCloud Drive and Notes already did. A folder of another Apple ID, a folder inside one, or another Skyfold library that appears at the photos folder's path (e.g. another disk with the same drive letter) stops the sync with a message instead of downloading, sending or deleting anything there. A Skyfold photos folder of another account inside the chosen one is left alone. Photos that were in a photos folder of an earlier version when it was chosen again are never sent.
- **Moved photos are followed by their Windows file id.** A photo you move, rename or edit and rename stays the same photo; before, an edit plus a rename looked like a deletion and deleted it in iCloud. Swapping two photos' names, or putting a new file at a photo's old name, is followed too, and a photo an editor saved by replacing the file stays that photo; a photo moved over another one (Explorer's Replace) is followed and only the overwritten one counts as deleted. One case can't be told apart: an editor that replaces the file and a rename before Skyfold's next check look like a deletion (as in every version; the deletion limit and iCloud's Recently Deleted still apply). A backup of the photos folder copied back in its place looks like the same library: photos missing from it count as deleted. Comparing contents is only needed for photos moved by copying; a photo followed that way (it could be someone's own copy) is kept on the PC, not put in the Recycle Bin, when it is deleted in iCloud.
- A photo sent from this PC whose reply from iCloud got lost: any photo in iCloud with exactly those bytes is left one-way (never managed or deleted from this PC).
- Choosing a new photos folder now says that files in it are synced both ways (deleting one there deletes it in iCloud too).

### Fixed
- Photos upload:
  - Signing in with another Apple ID turns the upload off and asks for a photos folder, also when the other account's database is found at start (e.g. a portable copy).
  - Files the upload dealt with (sent, a copy iCloud already had, a photo kept on this PC after it was deleted in iCloud, the video of a Live Photo) are recognised by Windows file id, so also after a rename or an edit: they aren't sent again unless their contents changed, and are never taken for a moved photo, adopted by a new download or put in the Recycle Bin for a photo deleted in iCloud.
  - The video part of a Live Photo stays on this PC; a separately imported copy of that video is sent.
  - A file whose contents change while Skyfold watches waits the two minutes from then, also when the copy kept the camera's old date, after a rename and across a restart.
  - A photo copied in from the iPhone that iCloud already has isn't downloaded a second time.
- Notes: a note page you made read-only no longer stops the Notes sync; the new look leaves it as it is, and a change made in iCloud still replaces it without an error (a hard-linked archive of it elsewhere stays read-only).
- **Sync now** from the tray menu while syncing is paused runs once through everything (Photos, iCloud Drive, Notes, Safari bookmarks) and syncing stays paused (**Resume** ends the pause); answering a Photos question while paused still only touches Photos. Safari bookmarks: **Sync now** refreshes the bookmarks file right away; before, it waited for the 6-hour interval unless a full library check ran. (The Edge extension picks it up on its next update or on *Update now*.)
- Update checks: GitHub's secondary rate limit (403 or 429 with an explanation in the reply) also uses the releases page and says how long to wait.
- Sign-in: the saved session is replaced in one step, so a crash or power cut while it is being saved no longer leaves a cut-off file that would mean signing in (and maybe entering a two-factor code) again.
- iCloud Drive: the "iCloud Drive is ready" notice now comes after the first full sync without errors or unread folders and counts every file of the first sync, also when a quick check (after a change on the PC) ran before it.

## [1.0.15] - 2026-11-19

### Added
- Photos: new setting "Upload photos and videos I add to this folder to iCloud Photos" (off by default). Photos and videos you put into the photos folder go up to iCloud Photos. A file is sent only when it has been unchanged for two minutes, is held open read-only while it is sent, and nothing is sent while a photo is missing from the folder (it could be a moved or renamed one). Album folders, hidden and system files, cloud placeholders and Skyfold's own files are never sent. An uploaded file becomes a synced one (deleting it later deletes it in iCloud too) only after Skyfold has seen it in iCloud and compared it byte for byte; a photo iCloud already had stays an ordinary file on this PC. A Live Photo copied from an iPhone (IMG_1234.HEIC + IMG_1234.MOV) sends its photo; the video part stays on this PC instead of becoming a separate video. A refused upload is tried again an hour later and shown in the panel once; one whose answer got lost is not sent twice.

### Changed
- Note pages have the same look and colors as Notes.html (light and dark). Pages already exported are restyled in place once, without downloading anything again; a page you changed yourself is left as it is.
- Update checks: when GitHub limits checks from your internet connection (60 an hour, shared by everyone on the same connection), Skyfold says how many minutes to wait instead of "HTTPError (403)", and finds the latest version through the releases page instead, which isn't limited that way.

## [1.0.14] - 2026-11-18

### Changed
- The panel's recent activity says what happened to each file and where: "Deleted in iCloud · iCloud Drive/Downloads", "Downloaded", "Uploaded", and for a rename the new name with the old one. Before, only the file name was shown (the rest was in the tooltip).

## [1.0.13] - 2026-11-18

### Added
- Browser extension "Skyfold – Safari Bookmarks" for Firefox, Chrome and Edge (in `extension/`): keeps a "Safari Bookmarks" folder the same as your Safari bookmarks, by itself (every 30 minutes and on "Update now"), without importing files (Edge Add-ons; Firefox and Chrome to follow). It gets them from Skyfold on this PC (native messaging), never from the internet, and only touches its own folder. Skyfold sets up the link for the current Windows user while Safari bookmarks are turned on, and removes it when they're turned off.

### Fixed
- Safari bookmarks: Safari's Reading List (a folder titled "com.apple.ReadingList") was included; Safari's own folders now carry the names Safari shows (Favorites, Bookmarks Menu) instead of BookmarksBar / BookmarksMenu.

## [1.0.12] - 2026-11-18

### Added
- Safari bookmarks for Firefox and Chrome (Settings → Notes → Safari bookmarks). Skyfold reads your Safari bookmarks from iCloud - read-only, nothing changes there - and keeps "Safari Bookmarks.html" up to date (every 6 hours and on Sync now). Import it in Firefox or Chrome (Bookmarks → Import): your folders and bookmarks appear in a "Safari Bookmarks" folder, in Safari's order. Deleted items, tab groups and the Reading List are left out. A file of that name that Skyfold didn't write is never replaced.

### Fixed
- iCloud Drive: a file moved out of the Drive folder (so Skyfold asked about deleting it) and then put back under another name no longer ends up twice in iCloud when the next check was a quick one; a full read now recognises the move.
- Notes: an older drawing whose full image is a TIFF is converted to PNG, so the browser shows it; a scanned document whose full PDF can't be downloaded shows its preview instead of nothing; when two attachments share one full image that can't be fetched, each keeps its own preview; an attachment's identifiers are read the way pyicloud reads them, so no image is ever filed under another attachment.
- Notes: nothing is written when a note's attachment folder leads outside the Notes folder (a link or junction).
- iCloud Drive: a PC deletion is only confirmed for files Windows can identify; without a file identity it waits.

### Security
- Updates (Codex review of 1.0.10/1.0.11):
  - an update is installed only when GitHub's build attestation proves the zip was built by Skyfold's own release workflow for that very version (Sigstore-signed; release builds now create it);
  - only the release's own files are used, every redirect is checked (GitHub hosts only, at most 5), and no stored login (.netrc) is ever sent;
  - each download gets its own folder, nothing already there is touched, and a cancelled or failed download leaves nothing behind;
  - the installer waits until Skyfold has really closed, and if the new version doesn't start it puts the previous one back (the old files are never deleted before the new version runs);
  - the update window can try again when the installer couldn't be started.

## [1.0.11] - 2026-11-18

### Fixed
- Notes: sharper and more complete images (Codex's image-quality investigation, all 15 open cases):
  - a photo's full image is used also when it is kept on the attachment itself, or when the media record lists a thumbnail first, and for every note that shares it;
  - previews are compared by their real size in pixels, not by file size, and a preview that can't be fetched no longer breaks the choice;
  - an iPhone (HEIC) photo keeps a preview the browser can show until its full-size JPEG is made;
  - a full image that can't be downloaded falls back to its preview, so the note is still exported;
  - scanned documents are shown as their full PDF (pages as images, the original linked), older drawings with their full bitmap, and a full drawing in a format browsers can't show (TIFF) is converted to PNG;
  - an attachment known under two names gets the same file under both.
  Notes that still show a small preview, a scan or an older drawing are exported again once after the update; all others stay as they are.

## [1.0.10] - 2026-11-18

### Added
- Updates from inside the app. Skyfold checks GitHub once a day for a newer version (only "Skyfold/<version>" is sent; it can be turned off in Settings → General), tells you once per version and shows a row in the panel. The update window shows what's new; *Update now* downloads the release, checks it against its published SHA-256, closes Skyfold, replaces only the program (Skyfold.exe and _internal; settings in "data" are never touched), puts the old version back if anything fails, and starts Skyfold again. Settings → About has *Check for updates*.

### Fixed
- A deletion question with both an iCloud and a PC part: answering one part no longer made the other part's button stop working in the window still open (each part has its own version now).
- iCloud Drive: approving "Delete on this PC too" no longer recycles a different file put at the same path meanwhile (for example a copy put back from a backup); Skyfold checks it is still the very file it asked about, and asks again otherwise.
- iCloud Drive: a file renamed only in letter case (report.txt to REPORT.TXT) after iCloud deleted it was left waiting forever, even after editing it. Edited, it now goes up under its new name.
- iCloud Drive: saving a file while a deletion question waits no longer triggers two full reads of iCloud Drive each time.
- Start-up: a leftover database rollback journal that can't be set aside stops the start instead of being taken into a database (it is never mixed with your sync data).

## [1.0.9] - 2026-11-18

### Changed
- Notes.html has a new look, designed by Codex to match the app: the Skyfold name and logo, folders in a left column (a Folders button on a narrow screen), notes grouped in cards per folder with subfolders under their parent, search, light and dark theme. An existing Notes.html gets the new look once after the update, without downloading notes again.

## [1.0.8] - 2026-11-18

### Fixed
- Sign-in: pressing Enter after the password (or after typing the code) cancelled the sign-in, so Apple sent a code to the phone but the window for it never came. Locking the fields while Apple answers moved the keyboard focus to Cancel, which then took the same Enter. Enter now never presses Cancel; Cancel works with a click, or with Tab and Space for keyboard users.
- Sign-in (with Codex): one sign-in attempt per window. Clicking Sign in again shows the same window, with its code step if Apple already sent a code; Close or Esc only hide it at every step, Cancel discards the attempt. When Apple answers, the window comes back to the front with its taskbar button flashing. Submitting twice is ignored, and a failure while opening or finishing sign-in is shown in the window and logged.
- Sign-in: when Apple refuses to even start a sign-in (after several attempts in a short time), the window says so and to wait about 15 minutes, instead of "wrong Apple ID or password". The "Sign-in required" card in the panel can be clicked and highlights like the other rows.
- Sign-in: when Apple's check whether the session may read iCloud data fails with a server error, Skyfold no longer keeps retrying as if signed in: it renews the session quietly, and if that doesn't help it asks you to sign in again.

### Added
- A packaged self-test (`--selftest-ui`) that signs in against a pretend Apple with real key presses, and `tools_signin_demo.py` to try the sign-in window by hand without contacting Apple.

## [1.0.7] - 2026-11-17

### Fixed
- Notes: Notes.html written by Skyfold 1.0.5 or earlier was never updated again after the upgrade to 1.0.6 (those versions kept no record that it was theirs), so notes moved to another folder kept showing in their old place. An index in that older format that lists this setup's own notes is taken back; the old page goes to the Recycle Bin.

### Changed
- Notes: iPhone (HEIC) photos are shown at full size: the original is turned into a JPEG with Windows' own HEIF decoder (the "HEIF Image Extensions" most PCs have). Without it, or on any problem, the small preview stays as before.
- Notes: pen drawings show the whole drawing: the full image (often narrow and tall, or wide and low) is used whenever its longer side is longer than the small square preview's. Only formats a browser can show are used.
- Notes: once after the update, only notes whose photos or drawings are still shown as small previews are exported again; other notes are left as they are.

## [1.0.6] - 2026-11-16 (public beta)

### Changed
- New look (redesign branch): flat, calm surfaces in light and dark mode; the tray panel has a quiet brand mark, a grouped service list with icon tiles, a segmented iCloud storage bar with a legend (Photos / Drive / Other; click for details), a one-line activity list, and a footer with one primary button plus icon buttons (Open iCloud.com and Pause moved into the "More" menu; when paused, the primary button is "Resume"). Dialogs use the same tokens. No change to what is synced or deleted.

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
- Twenty-fifth follow-up review (tests in tests/test_review27_codex_probes.py, tests/test_review27_codex_ui_probes.py and tests/test_review27_fixes.py):
  - Album folders: an album entry counts as a second name of the photo only when both files' identities are known and equal. On a drive that can't tell files apart (no file IDs), a picture saved at an album path is never removed.
  - iCloud Drive: a replaced version on its way to the Recycle Bin is left alone by quick syncs too (the next full sync finishes the move), and also while Skyfold can't tell which file is at that name; it is never uploaded as a new file.
  - Photos, iCloud Drive and Notes: a record of a private file is dropped only when the file is really missing. A file that couldn't be looked at (access denied, a disk hiccup) keeps its record and is left alone until it can.
  - A marker file from an earlier version of the app (.isync, .galerija-sync) is never renamed or changed; in a folder Skyfold already tracks, Skyfold adds its own marker next to it.
  - Dark theme: selected text and the knob of a switch that is on use the dark text colour on the light blue accent, so they are readable. The iCloud storage bar shows a focus frame when reached with Tab. Switching between light and dark updates switches in windows that are already open.
- Twenty-sixth follow-up review (tests in tests/test_review28_codex_probes.py, tests/test_review28_codex_ui_probes.py and tests/test_review28_fixes.py):
  - Sync markers (Photos, iCloud Drive, Notes) are only ever created as new files. A marker that can't be looked at (access denied, a drive that isn't ready) stops the sync with a message instead of being taken for a missing one; a file at that name is never written over, and a Drive folder of another Apple ID is never taken over that way.
  - Settings, session and sync database taken over from an earlier installation are only ever put in place as new files: existing current ones are never replaced, also when they couldn't be looked at.
  - iCloud Drive: a folder deletion you approved while a sync was still running is carried out only when every item in it is provably the same as when you were asked (same item, same version tag). An item without a version tag or identity proves nothing: the deletion waits and you are asked again.
  - iCloud Drive: a replaced version on its way to the Recycle Bin is held in quick syncs even when the folder listing misses it; a folder created at that name releases it; another download never clears its record. A file that can't be looked at when an approved deletion is carried out is kept and asked about again, never forgotten.
  - Photos: a file or folder that can't be read during a scan is no evidence of a deletion: missing photos are decided only when the whole folder could be read, and "is the photo back?" counts an unreadable answer as back. Album entries and folders keep their records while they can't be looked at.
  - Notes: pages and private files keep their records while they can't be looked at; they are removed once they can be.
  - Switching between light and dark also redraws icons in open windows (Settings, Find My, deletion review) and the folder check message; only Skyfold's own widgets are touched.
- Twenty-seventh follow-up review (tests in tests/test_review29_codex_probes.py and tests/test_review29_fixes.py):
  - The sync database is taken over from an earlier installation together with its journal, as one consistent copy, and only when this PC has none of its files; a leftover journal is never put next to another database. The session folder of the earlier app name (iSync) is taken over again (only when there is none yet), so the upgrade needs no new sign-in.
  - Settings and the Drive cache are saved through private temp files: a file of yours named settings.tmp or drive_cache.tmp is never touched.
  - iCloud Drive: a folder deleted on one side that holds items Skyfold never syncs (a .tmp draft, an Office lock file, …) is kept with them; only the synced files follow the deletion. An empty version tag from iCloud counts as no tag: the content is compared before anything is deleted, and content seen without a tag is never reused. A folder deletion you approved during a sync also checks that the folder itself is the same one; files without a tag are proven unchanged by their content, so you aren't asked again and again.
  - Notes: PDF page images and full-size drawings are only ever written as new files (another free name when one is taken); a drawing's small preview is removed only when that same export made it. A page that can't be read is kept and tried again, never forgotten; Notes.html is claimed only once Skyfold has really put its own there.
  - The small images of the light/dark styles live in a folder of Skyfold's own, never under fixed names in the shared temp folder.
  - Photos: a file that can't be read only holds missing photos of the same name (a folder that can't be read still holds all of them), and the activity list says why they are kept for now.
  - iCloud Drive: a replaced version on its way to the Recycle Bin stays known when its folder is renamed or moved to follow iCloud, and whatever the letter case of its name. Files and paths that can't be looked at during approved deletions, moves and interrupted downloads are left as they are and decided later, never forgotten or shown as conflict copies.
  - Find My: devices loaded with Refresh after switching between light and dark use the new colours.
  - Sync markers: a folder or link at the marker's name stops the sync; the old marker names are only looked at when the current marker is missing.
- Twenty-eighth follow-up review (tests in tests/test_review30_codex_probes.py and tests/test_review30_fixes.py):
  - iCloud Drive: a folder deleted on one side that stays on the other only for items Skyfold never syncs (desktop.ini, an Office lock file, a .tmp draft, also an empty ignored folder) is no longer created again where it was deleted. A synced file added inside it later brings it back as usual; the activity list says why it stays.
  - The sync database of an earlier installation is copied and the earlier file is left exactly as it is (another program may still have written to it). A journal left here without its database is set aside under a new name. If the database can't be taken over (unreadable, kept busy for more than 30 seconds), Skyfold says so at start and keeps the earlier file.
  - Notes: a drawing's small preview is removed only when this very export downloaded it into the attachments folder (by file identity), never because of its creation time. The first Notes.html is claimed only when exactly its bytes are found there, also after a crash.
  - iCloud Drive: a file that can't be read in a folder deleted in iCloud is decided later, not uploaded again; a failed folder move restores the exact records that were there before; content of an item without a version tag is fetched once per listing within a pass.
  - Photos: a folder that can't be read is named in the activity list once (not on every pass), with what to do about it.
  - Sign-in: a sign-out mark that can't be looked at counts as there.
- Twenty-ninth follow-up review (tests in tests/test_review31_codex_probes.py and tests/test_review31_fixes.py):
  - iCloud Drive: files of a deleted folder that wait for your confirmation keep their records until they are really deleted (only the folder's own record is let go), so a later pass never takes them for new files and uploads or downloads them again. This also covers folders that come back because other files in them were changed.
  - iCloud Drive: a folder kept on this PC only for never-synced items is recognised in any letter case; a subfolder that is gone from it is struck from its record; a folder outside the chosen folders keeps its record; a change inside a kept folder is looked at by a full pass.
  - Start: a database journal left without its database is set aside (as a pair, under a new name) before anything opens the database, also when there is no earlier installation. If it can't be renamed, a copy is kept. The earlier database is read without being changed at all (no checkpoint, its journal stays). When it can't be taken over, or even looked at, Skyfold asks before it starts with a new database; No closes it, and it is tried again at the next start.
  - Notes (review 31): an export records its downloads through its own view of the iCloud client (the shared client is never changed), together with the folders they went into: a replaced drawing preview in the note's own attachment folder is removed, anything else stays. Drawing data collected during an export belongs to that export's thread.
- Thirtieth follow-up review (tests in tests/test_review32_codex_probes.py and tests/test_review32_fixes.py):
  - iCloud Drive: your OK for a deletion counts only for the side the question was about; if things changed so that the deletion would now happen on the other side, you are asked again.
  - iCloud Drive: a rename or move on this PC is carried over to iCloud only when the content is proven the same (every file's fingerprint matches); a look-alike folder or file with the same names, sizes or times is synced as new, and the deletion is asked about as usual.
  - iCloud Drive: files waiting for your OK keep their records when iCloud replaces their folder with a file, and when the folder's name changes only in letter case on this PC. A folder made on this PC after the question is never deleted with the approved folder. A kept subfolder that can't be read is not taken for gone; an empty folder someone made at a rename's new name is not removed; an interrupted rename that can't be told apart is left alone until it can.
  - Start: the earlier database is copied into a private folder and opened there (its journal, also a crashed program's rollback journal, is applied to the copy, which is checked before it is used); nothing is created or changed next to the original. A database file that can't be looked at for any reason is reported. If a journal left without its database can neither be renamed nor copied, Skyfold doesn't start, so it is never overwritten.
- Thirty-first follow-up review (tests in tests/test_review33_codex_probes.py and tests/test_review33_fixes.py):
  - iCloud Drive: a deletion you were asked about is never done unanswered, also when a later (or quicker) pass sees fewer items than the limit. Your OK counts only for the question you saw: its side, and in iCloud the very items and versions it showed (a new item at the same name is asked about again). An answer given in a window that is out of date is ignored and you are asked again. An OK for an item that couldn't be deleted yet stays valid for it.
  - iCloud Drive: a PC rename is carried over to iCloud only when iCloud's folder still holds exactly what was synced; anything added or changed there stays where it is (the PC folder goes up under its new name). A file whose own name differs only in letter case from a synced file is not taken for it (both are left alone until it is clear). An interrupted rename that finds another file at the new name keeps both. Checking a large folder for a move stops as soon as the sync is cancelled.
  - Start: a rollback journal left without its database is set aside too; when the database can't be looked at while a journal is there, Skyfold doesn't start. The earlier database is copied under a read lock whenever a program may have it open (the copy is never older than what was saved), and a closed database is copied without creating any file next to it.
- Thirty-second follow-up review (tests in tests/test_review34_codex_probes.py and tests/test_review34_fixes.py):
  - Every deletion question has a version: an answer from a window showing an older version (the files changed meanwhile) is ignored, both "delete" and "bring back", and you are asked again. This holds for Photos and iCloud Drive.
  - A deletion you were asked about never happens unanswered when fewer are left (one was restored or edited): for Photos too, and in iCloud Drive also when a folder's files are listed one by one later. An OK for a folder covers its files.
  - An empty folder counts toward the limit as one item, next to the files of the others (it used to be left out when files were deleted with it).
  - Photos: an OK for "delete in iCloud" never deletes on this PC.
  - iCloud Drive: a folder made where a synced file was is never deleted with an approved folder.
- Thirty-third follow-up review (tests in tests/test_review35_codex_probes.py and tests/test_review35_fixes.py):
  - iCloud Drive: deletions are only ever decided by a full read. A quick check (after a change on this PC) that finds something deleted hands over to a full read, so the limit for asking you always counts every deletion, in all folders.
  - iCloud Drive: files you were asked about one by one, later seen as their folder (once the last never-synced file in it is gone), are asked about again as that folder, never deleted unanswered.
- Thirty-fourth follow-up review (tests in tests/test_review36_codex_probes.py and tests/test_review36_fixes.py):
  - Photos: a photo renamed (or renamed and moved) in Explorer is recognised by its content and followed, never taken for a deletion in iCloud; a Live Photo's video stays with it. The content is compared with the fingerprint taken when it was downloaded (rows from before 1.0.6: with iCloud's copy), so another file of the same size is never taken for it.

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
