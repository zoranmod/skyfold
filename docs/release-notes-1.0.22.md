**Skyfold 1.0.22 (public beta): accessible sign-in.**

> On 1.0.10 or later? Skyfold offers this update by itself: Settings → About → *Check for updates* → *Update now*.

### Fixed
- Screen readers now name the fields of the sign-in window: Apple ID, password and the six-digit verification code (the dots in the code field said nothing to a screen reader).

Full list: [CHANGELOG](https://github.com/zoranmod/skyfold/blob/main/CHANGELOG.md).

### Download
Download `Skyfold-windows.zip` below, unzip it anywhere and run `Skyfold.exe`. To update by hand, quit Skyfold and replace `Skyfold.exe` and the `_internal` folder with the ones from the zip; keep the `data` folder (your settings).

To check the download, compare it with `Skyfold-windows.zip.sha256`, or run `gh attestation verify Skyfold-windows.zip --repo zoranmod/skyfold`.

Windows SmartScreen may warn about a new, unsigned app: choose *More info → Run anyway*.

---
**Na bosanskom:** Skyfold 1.0.22 daje imena poljima u prozoru za prijavu (Apple ID, lozinka i šestocifreni verifikacijski kod), pa ih čitači ekrana sada čitaju ispravno.
