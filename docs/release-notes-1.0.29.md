**Skyfold 1.0.29 (public beta): every connection checked, and a privacy-safe connection log.**

> On 1.0.10 or later? Skyfold offers this update by itself: Settings → About → *Check for updates* → *Update now*.

### Security
- **Every request is checked before it is sent.** Skyfold's own requests, and each redirect, may only go to Apple's iCloud servers or GitHub's update servers over HTTPS. Anything else is refused before a byte is sent, and the panel says so. The iCloud sign-in never goes to GitHub. The packaged self-test checks this too.
- **The update window's *What's new* text loads nothing by itself**: no picture from the internet or a network share. Links still open in your browser when you click them.

### Added
- **Connection diagnostics for bug reports** (**Settings → General**). *Record for 30 minutes* writes `connections.log` with only the kind of each request, Apple's status code and how long it took: never a name, a file name, an address, an id, a cookie or a password. It stops by itself after 30 minutes, at sign-out and when Skyfold quits, and *Delete* removes the file.

### Changed
- The privacy section of the README now says that the ordinary log can contain note titles and album names, and which links open in your browser.

Full list: [CHANGELOG](https://github.com/zoranmod/skyfold/blob/main/CHANGELOG.md).

### Download
Download `Skyfold-windows.zip` below, unzip it anywhere and run `Skyfold.exe`. To update by hand, quit Skyfold and replace `Skyfold.exe` and the `_internal` folder with the ones from the zip; keep the `data` folder (your settings).

To check the download, compare it with `Skyfold-windows.zip.sha256`, or run `gh attestation verify Skyfold-windows.zip --repo zoranmod/skyfold`.

Windows SmartScreen may warn about a new, unsigned app: choose *More info → Run anyway*.

---
**Na bosanskom:** Skyfold 1.0.29 provjeri svaki zahtjev prije slanja: smije ići samo Appleovom iCloudu i GitHubu za ažuriranja, preko HTTPS-a, a sve drugo odbije prije nego što ijedan bajt ode. Novo je i snimanje veza za prijavu greške (Postavke → Općenito): 30 minuta, samo vrsta zahtjeva, Appleov odgovor i trajanje, bez imena, fajlova, adresa i lozinke. Prozor „Šta je novo“ više ništa ne učitava sam s interneta.
