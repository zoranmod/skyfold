**Skyfold 1.0.31 (public beta): a Photos window, as on an iPad.**

> On 1.0.10 or later? Skyfold offers this update by itself: Settings → About → *Check for updates* → *Update now*.

▶ **[Watch Skyfold in 48 seconds](https://zoranmod.github.io/skyfold/#video)** (the photos, files and notes in it are made up).

### New: the Photos window
Click **Photos** in the tray panel (or *Photos* in the tray menu):
- Your synced photos and videos edge to edge, the newest at the bottom, with **Years**, **Months** and **All Photos**. The sidebar has Recently added, Favourites, Videos, Live Photos, Screenshots and your albums, and *Search* finds names, months, years, albums and kinds.
- **The viewer** shows a photo large, with a film strip and its details (when it was taken, its size, its albums and where it is on your PC). Videos and Live Photos play in your usual player.
- **A heart here is a heart on your iPhone**, and the other way round.
- **Delete** asks first, then moves the files to the Recycle Bin, so iCloud gets them in Recently Deleted at the next sync. Only the files Skyfold synced for that photo go; a Live Photo goes as a pair.
- Smooth to use: the mouse wheel glides, `Ctrl` + wheel makes the photos larger or smaller, the mouse's Back and Forward buttons go in and out, and maximizing keeps your place.
- It reads the files already on your PC, so it opens at once and works offline. The pictures are Windows' own previews, so it takes no extra disk space.

After this update Skyfold reads your photo library once in full (nothing is downloaded again), to learn each photo's favourite, date added and size.

### Also better
- **The tray panel keeps its height** when a sync starts or ends.
- **Replacing a file in iCloud Drive is safer:** the new contents reach iCloud before the old version is moved to Recently Deleted.
- **A lost connection no longer puts photos aside for a day;** Skyfold tries again in a few minutes.
- **A photo you hide on your iPhone stays on this PC**, and an incomplete answer from Apple is never read as a deletion.
- Locking a note deletes its copy on this PC outright, and notes or folders called "Con", "Aux" and the like no longer fail.

Full list: [CHANGELOG](https://github.com/zoranmod/skyfold/blob/main/CHANGELOG.md).

### Download
Download `Skyfold-windows.zip` below, unzip it anywhere and run `Skyfold.exe`. To update by hand, quit Skyfold and replace `Skyfold.exe` and the `_internal` folder with the ones from the zip; keep the `data` folder (your settings).

To check the download, compare it with `Skyfold-windows.zip.sha256`, or run `gh attestation verify Skyfold-windows.zip --repo zoranmod/skyfold`.

Windows SmartScreen may warn about a new, unsigned app: choose *More info → Run anyway*.

---
**Na bosanskom:** Skyfold 1.0.31 donosi prozor Slike, kao na iPadu: tvoje slike i videi po godinama i mjesecima, albumi, omiljene, pregled slike i pretraga. Srce koje ovdje staviš vidi se i na iPhoneu, a brisanje ide u Koš i u „Nedavno obrisano“ u iCloudu. Prozor radi i bez interneta i ne zauzima dodatni prostor na disku. Uz to: panel više ne mijenja visinu pri sinhronizaciji, zamjena fajla u iCloud Driveu je sigurnija, a prekid interneta više ne odlaže slike za cijeli dan.
