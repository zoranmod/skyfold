**Skyfold 1.0.21 (public beta): safer folder changes, Notes fixes, roomier Settings.**

> On 1.0.10 or later? Skyfold offers this update by itself: Settings → About → *Check for updates* → *Update now*.

### Fixed
- **Stopping to sync an iCloud Drive folder keeps your unsent work.** *Move to Recycle Bin* for folders you no longer sync only recycles files that are exactly what iCloud has; anything new or changed on this PC stays, and you are told.
- **A file on this PC where iCloud has a folder of the same name** no longer sends that folder's files in iCloud to Recently Deleted.
- **Notes:** an expired change token no longer keeps new edits from coming down, and a picture of a deleted or locked note that was open in a viewer goes to the Recycle Bin once it is closed.
- iCloud Drive: a file still being saved is looked at again a few seconds later instead of at the next hourly check.
- When uploads to iCloud Photos have to wait, the panel says so instead of *Everything is in sync*.
- **Settings opens tall enough for its longest page**, so nothing needs scrolling; the download-limit hint sits under its setting again, and *Automatic (recommended)* is no longer cut off.

Full list: [CHANGELOG](https://github.com/zoranmod/skyfold/blob/main/CHANGELOG.md).

### Download
Download `Skyfold-windows.zip` below, unzip it anywhere and run `Skyfold.exe`. To update by hand, quit Skyfold and replace `Skyfold.exe` and the `_internal` folder with the ones from the zip; keep the `data` folder (your settings).

To check the download, compare it with `Skyfold-windows.zip.sha256`, or run `gh attestation verify Skyfold-windows.zip --repo zoranmod/skyfold`.

Windows SmartScreen may warn about a new, unsigned app: choose *More info → Run anyway*.

---
**Na bosanskom:** Skyfold 1.0.21 pri prestanku sinhronizacije nekog iCloud Drive foldera u Korpu za otpatke šalje samo fajlove koji su tačna kopija onoga u iCloudu; novi i izmijenjeni fajlovi ostaju na računaru. Fajl na računaru s istim imenom kao folder u iCloudu više ne briše taj folder u iCloudu. Notes nastavlja i kad Apple poništi token promjena, a slike obrisanih bilješki koje su bile otvorene odlaze u Korpu kad ih zatvoriš. Prozor Postavki se otvara dovoljno velik da se sve vidi bez skrolanja.
