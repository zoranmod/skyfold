**Skyfold 1.0.23 (public beta): safer folder changes and Notes fixes.**

> On 1.0.10 or later? Skyfold offers this update by itself: Settings → About → *Check for updates* → *Update now*.

### Fixed
- **Settings: changing your mind no longer deletes a Drive folder.** If saving failed and you then chose to keep syncing a folder you had first set to recycle, Skyfold could still recycle it and delete it in iCloud. The cleanup now follows only the choice finally saved.
- **Recycling a folder you stop syncing checks the contents**, not only size and time, so an edited file stays on the PC.
- **Two iCloud Drive files whose names differ only in capital letters** (Windows can hold only one) no longer let a deleted PC folder take the other one with it in iCloud.
- **Notes:** pictures from a failed export go away when the note is locked or deleted; a PDF that couldn't be downloaded is tried again; a note deleted while Apple reset its change token is removed from the PC at the next check.
- The panel says uploads to iCloud Photos are waiting while a refused photo waits for its next try; *Stop* ends a long iCloud Drive listing right away; a file still being saved waits a few seconds before it is looked at again.

### Known limitation
- A file you shared on its own from iCloud Drive and then edit on the PC becomes a new document in iCloud; the people you shared it with may lose access. Apple's web interface has no way to update the document in place. Edit such files on an Apple device, or share the folder instead.

Full list: [CHANGELOG](https://github.com/zoranmod/skyfold/blob/main/CHANGELOG.md).

### Download
Download `Skyfold-windows.zip` below, unzip it anywhere and run `Skyfold.exe`. To update by hand, quit Skyfold and replace `Skyfold.exe` and the `_internal` folder with the ones from the zip; keep the `data` folder (your settings).

To check the download, compare it with `Skyfold-windows.zip.sha256`, or run `gh attestation verify Skyfold-windows.zip --repo zoranmod/skyfold`.

Windows SmartScreen may warn about a new, unsigned app: choose *More info → Run anyway*.

---
**Na bosanskom:** Skyfold 1.0.23 popravlja tri slučaja u kojima je fajl mogao nepotrebno završiti u Korpi ili u iCloudovom „Nedavno obrisano“: predomišljanje u Postavkama, fajl izmijenjen uz istu veličinu i vrijeme, i dva fajla čija se imena razlikuju samo po velikim i malim slovima. Notes bolje čisti slike i ponovo pokušava PDF-ove. Poznato ograničenje: pojedinačno podijeljen Drive fajl izmijenjen na računaru postaje novi dokument u iCloudu.
