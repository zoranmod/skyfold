**Skyfold 1.0.15 (public beta): send photos from your PC to iCloud Photos.**

> On 1.0.10 or later? Skyfold offers this update by itself: Settings → About → *Check for updates* → *Update now*.

### Added
- **Upload to iCloud Photos (optional).** Settings → Photos → *Upload photos and videos I add to this folder to iCloud Photos* (off by default). Photos and videos you put into your Skyfold photos folder go up to iCloud Photos and show up on your iPhone.
  - A file is sent only once it has stayed unchanged for two minutes, and it is held read-only while it is sent.
  - Nothing is sent while a photo is missing from the folder, because a "new" file could be a photo you moved or renamed.
  - Album folders, hidden and system files, cloud placeholders and Skyfold's own files are never sent.
  - An uploaded file becomes a synced photo only after Skyfold has seen it in iCloud and compared it byte for byte. After that, deleting it on the PC deletes it in iCloud too. A photo iCloud already had stays an ordinary file on your PC.
  - A Live Photo copied from an iPhone (`IMG_1234.HEIC` + `IMG_1234.MOV`) sends its photo. The video part stays on the PC, so it doesn't appear in iCloud as a separate video.
  - If iCloud refuses an upload, Skyfold tries again an hour later and shows the error in the panel once.

### Changed
- **Note pages match Notes.html.** Exported notes have the same look and colors as the notes index, in light and dark mode. Pages already exported are restyled in place once, with nothing downloaded again. A page you edited yourself is left as it is.
- **Update checks and GitHub's limit.** When GitHub limits update checks from your internet connection, Skyfold says how many minutes to wait instead of showing "HTTPError (403)", and finds the latest version through the releases page.

Full list: [CHANGELOG](https://github.com/zoranmod/skyfold/blob/main/CHANGELOG.md).

### Download
Download `Skyfold-windows.zip` below, unzip it anywhere and run `Skyfold.exe`. You can also replace your existing Skyfold folder; settings and sync state are kept.

To check the download, compare it with `Skyfold-windows.zip.sha256`, or run `gh attestation verify Skyfold-windows.zip --repo zoranmod/skyfold`.

Windows SmartScreen may warn about a new, unsigned app: choose *More info → Run anyway*.

---
**Na bosanskom:** Skyfold 1.0.15 ima novu opciju u postavkama (Photos): slike i videi koje ubaciš u folder sa slikama šalju se u iCloud Photos i pojave se na telefonu. Opcija je po defaultu isključena. Stranice bilješki sada izgledaju kao Notes.html. Kad GitHub ograniči provjere ažuriranja, Skyfold kaže koliko minuta treba čekati.
