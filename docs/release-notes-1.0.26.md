**Skyfold 1.0.26 (public beta): iCloud storage in Apple's numbers.**

> On 1.0.10 or later? Skyfold offers this update by itself: Settings → About → *Check for updates* → *Update now*.

### Fixed
- **iCloud storage shows your plan's real size.** A 200 GB plan read "214.7 GB", because Apple counts a gigabyte as 1024 MB for iCloud storage and the panel counted 1000. The total, the used amount and the Photos / Drive / Other parts now use Apple's units, so the panel shows the same numbers as iPhone Settings.

Full list: [CHANGELOG](https://github.com/zoranmod/skyfold/blob/main/CHANGELOG.md).

### Download
Download `Skyfold-windows.zip` below, unzip it anywhere and run `Skyfold.exe`. To update by hand, quit Skyfold and replace `Skyfold.exe` and the `_internal` folder with the ones from the zip; keep the `data` folder (your settings).

To check the download, compare it with `Skyfold-windows.zip.sha256`, or run `gh attestation verify Skyfold-windows.zip --repo zoranmod/skyfold`.

Windows SmartScreen may warn about a new, unsigned app: choose *More info → Run anyway*.

---
**Na bosanskom:** Skyfold 1.0.26 prikazuje iCloud prostor u Appleovim jedinicama. Plan od 200 GB je ranije pisao „214,7 GB“ jer Apple za iCloud računa 1 GB kao 1024 MB, a panel je računao 1000. Sada ukupno, zauzeto i dijelovi Fotografije / Drive / Ostalo pokazuju iste brojeve kao postavke na iPhoneu.
