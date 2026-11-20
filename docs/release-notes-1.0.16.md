**Skyfold 1.0.16 (public beta): safer photo sync, one-way uploads.**

> On 1.0.10 or later? Skyfold offers this update by itself: Settings → About → *Check for updates* → *Update now*.

### Changed
- **Uploading from the PC is now one-way.** A photo or video you put in the photos folder goes up to iCloud Photos and stays an ordinary file on your PC. Deleting or renaming it on one side doesn't change the other side, and Skyfold doesn't download it back as a second copy. Photos sent with 1.0.15 that Skyfold had already matched stay synced as before. Photos and videos already in an older photos folder that Skyfold doesn't sync are never sent.
- **One photos folder per Apple ID.** If you sign in with another Apple ID, Skyfold asks you to choose a photos folder for it, as it already did for iCloud Drive and Notes. A folder that belongs to another Apple ID isn't synced. A backup copied back with its marker counts as the same library.
- **Moved photos are recognised by Windows itself.** A photo you move, rename, or edit and then rename stays the same photo. Before, editing a photo and then renaming it could look like a deletion and delete it in iCloud. Swapped names, a photo moved over another one, and photos saved by an editor are followed too. An editor that replaces the file plus a rename before Skyfold's next check can still look like a deletion.
- Choosing a new photos folder now tells you that the new folder syncs both ways.

### Fixed
- **Sync now** from the tray menu while syncing is paused runs once through everything and syncing stays paused; it also refreshes Safari bookmarks right away.
- A note page you made read-only no longer stops the Notes sync.
- The saved sign-in can no longer be damaged by a crash or power cut while it is being saved.
- The "iCloud Drive is ready" notice now also comes when a quick check ran first, and counts every file of the first sync.
- Update checks handle GitHub's secondary rate limit.

Full list: [CHANGELOG](https://github.com/zoranmod/skyfold/blob/main/CHANGELOG.md).

### Download
Download `Skyfold-windows.zip` below, unzip it anywhere and run `Skyfold.exe`. To update by hand, quit Skyfold and replace `Skyfold.exe` and the `_internal` folder with the ones from the zip; keep the `data` folder (your settings).

To check the download, compare it with `Skyfold-windows.zip.sha256`, or run `gh attestation verify Skyfold-windows.zip --repo zoranmod/skyfold`.

Windows SmartScreen may warn about a new, unsigned app: choose *More info → Run anyway*.

---
**Na bosanskom:** Skyfold 1.0.16 slanje slika s računara pretvara u jednosmjerno: poslana slika ostaje običan fajl na računaru, a brisanje na jednoj strani ne briše na drugoj. Folder za slike sada pripada jednom Apple ID-u. Premještene i preimenovane slike Skyfold prati dok Windows zadrži ID fajla, pa izmjena i preimenovanje više ne brišu sliku u iCloudu; ako uređivač zamijeni fajl i preimenuješ ga prije sljedeće provjere, to još može izgledati kao brisanje. „Sync now“ radi i kad je sinhronizacija pauzirana.
