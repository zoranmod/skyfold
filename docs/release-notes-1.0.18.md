**Skyfold 1.0.18 (public beta): safer iCloud Drive and private Notes pictures.**

> On 1.0.10 or later? Skyfold offers this update by itself: Settings → About → *Check for updates* → *Update now*.

### Fixed
- **iCloud Drive keeps a newer save from another device.** If replacing a file in iCloud was interrupted (connection lost, PC restarted) and a Mac or iPhone saved a new version meanwhile, the retry used to move that new version to Recently Deleted. Now both are kept: iCloud's version keeps the name, the PC version becomes a `(PC conflict …)` copy.
- **Deleting a folder on the PC keeps iCloud items you never saw on it.** Items iCloud marks as hidden stay in iCloud; only the synced files go to Recently Deleted.
- **A deletion or a new folder that iCloud refuses is no longer taken as done.** It is reported and tried again.
- **Pictures of a locked or deleted note go to the Recycle Bin with its page.** Before, they stayed readable in `_assets`. Pictures you changed or added yourself stay.
- **Editing a note no longer piles up copies of its pictures.**
- A note that couldn't be downloaded now shows in the status instead of "Everything is in sync".
- Downloading new Drive files needs less free space.
- *Check for updates* no longer says "You have the latest version" while a newer release is still being published.
- Screen readers name every control in Settings.
- Bosnian Notes.html shows the right count for 21, 31, 101 … notes.

Full list: [CHANGELOG](https://github.com/zoranmod/skyfold/blob/main/CHANGELOG.md).

### Download
Download `Skyfold-windows.zip` below, unzip it anywhere and run `Skyfold.exe`. To update by hand, quit Skyfold and replace `Skyfold.exe` and the `_internal` folder with the ones from the zip; keep the `data` folder (your settings).

To check the download, compare it with `Skyfold-windows.zip.sha256`, or run `gh attestation verify Skyfold-windows.zip --repo zoranmod/skyfold`.

Windows SmartScreen may warn about a new, unsigned app: choose *More info → Run anyway*.

---
**Na bosanskom:** Skyfold 1.0.18 čuva noviju verziju fajla s Maca ili iPhonea kad se zamjena fajla u iCloud Driveu prekine: ostaju obje kopije. Brisanje foldera na računaru više ne briše skrivene stavke u iCloudu. Slike zaključane ili obrisane bilješke idu u Korpu za otpatke zajedno s njenom stranicom, a izmjene bilješke više ne gomilaju kopije slika. Bilješka koja se nije mogla skinuti sada se vidi u statusu.
