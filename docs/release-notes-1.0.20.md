**Skyfold 1.0.20 (public beta): parallel Photos uploads, automatic send buffer.**

> On 1.0.10 or later? Skyfold offers this update by itself: Settings → About → *Check for updates* → *Update now*.

### Changed
- **Photos and videos go up to iCloud Photos two at a time**, each on its own connection, like new iCloud Drive files. Each file stays locked against changes while it is sent.
- **The upload send buffer is automatic now**: Windows sizes it while sending. In tests on a 200 Mbit/s line it was as fast as or faster than the fixed 8 MB of 1.0.19 (13.1 MB/s against 11.0 and 3.5 MB/s). *Fixed 8 MB* stays available in Settings → Upload send buffer. The log notes the speed of every upload over 1 MB.

### Fixed
- **A photo iCloud refused is sent again.** Before (also in 1.0.19), a refusal from iCloud — busy, no access, a connection that couldn't be made, or this one file refused — could be taken for a lost reply, and the photo was never sent again. Now only a registration whose answer really got lost counts as maybe sent.
- **When uploads to iCloud Photos have to wait**, photos from your iPhone still come down in the same check; the waiting files go up next time.
- The panel no longer stays at "Checking for changes…" when iCloud Drive and Photos finish at the same moment.
- A broken connection stops the other uploads too; Stop pressed while a video is being read sends nothing and unlocks it at once; one video under two names (hard links) goes once.

Full list: [CHANGELOG](https://github.com/zoranmod/skyfold/blob/main/CHANGELOG.md).

### Download
Download `Skyfold-windows.zip` below, unzip it anywhere and run `Skyfold.exe`. To update by hand, quit Skyfold and replace `Skyfold.exe` and the `_internal` folder with the ones from the zip; keep the `data` folder (your settings).

To check the download, compare it with `Skyfold-windows.zip.sha256`, or run `gh attestation verify Skyfold-windows.zip --repo zoranmod/skyfold`.

Windows SmartScreen may warn about a new, unsigned app: choose *More info → Run anyway*.

---
**Na bosanskom:** Skyfold 1.0.20 šalje slike i videe u iCloud Photos po dva istovremeno, a buffer za slanje je sada automatski (u testovima jednako brz ili brži od fiksnih 8 MB; fiksno 8 MB ostaje u postavkama). Slika koju iCloud odbije sada se ponovo šalje — ranije je mogla ostati zauvijek preskočena. Kad slanje u Photos mora čekati, slike s iPhonea i dalje stižu na računar u istoj provjeri.
