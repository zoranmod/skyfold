**Skyfold 1.0.12 (public beta): Safari bookmarks for Firefox and Chrome, and safer updates.**

> On 1.0.10 or 1.0.11? Skyfold offers this update by itself: Settings → About → *Check for updates* → *Update now*.

### What's new
- **Safari bookmarks in Firefox, Chrome and Edge.** Turn it on in *Settings → Notes → Safari bookmarks*. Skyfold reads your Safari bookmarks from iCloud and keeps `Safari Bookmarks.html` up to date (every 6 hours and on *Sync now*). Import it in your browser and your bookmarks appear in a *Safari Bookmarks* folder, with your folders in Safari's order. Read-only: nothing changes in iCloud. It's one-way for now (Safari → browser).

### Safer updates
- From this version on, Skyfold installs an update only when **GitHub's build attestation** proves the download was built by Skyfold's own release workflow for that version (signed through Sigstore). A file put into a release by hand is refused.
- If the new version doesn't start, the previous one is put back automatically. Skyfold also waits until it has really closed before replacing anything, and a cancelled download leaves nothing behind.

### Fixed
- iCloud Drive: a file moved out of the Drive folder and put back under another name, while a deletion question was waiting, could end up twice in iCloud.
- Notes: older drawings stored as TIFF are converted to PNG so the browser shows them; a scanned document whose PDF can't be downloaded shows its preview; each attachment keeps its own preview when a shared full image fails.

Full list: [CHANGELOG](https://github.com/zoranmod/skyfold/blob/main/CHANGELOG.md).

### Download
`Skyfold-windows.zip` below; unzip anywhere and run `Skyfold.exe` (or replace your existing Skyfold folder; settings and sync state are kept). Check it with `Skyfold-windows.zip.sha256`, or with `gh attestation verify Skyfold-windows.zip --repo zoranmod/skyfold`. Windows SmartScreen may warn about a new, unsigned app: *More info → Run anyway*.

---
**Na bosanskom:** Skyfold 1.0.12 donosi Safari oznake za Firefox i Chrome (Postavke → Bilješke → Safari oznake): Skyfold drži ažuran fajl koji uvezeš u preglednik, samo čita iz iClouda. Ažuriranja su sigurnija: instaliraju se samo ako GitHub potvrdi da ih je napravio Skyfoldov build, a ako nova verzija ne krene, vraća se stara.
