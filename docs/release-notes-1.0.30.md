**Skyfold 1.0.30 (public beta): fewer false virus alarms.**

> On 1.0.10 or later? Skyfold offers this update by itself: Settings → About → *Check for updates* → *Update now*.

### Changed
- **Fewer false virus alarms.** `Skyfold.exe` now starts with its own small starter program, compiled from PyInstaller's source when the release is built, instead of the ready-made one that countless other programs (malware included) share. Antivirus programs that judge by resemblance, like Windows Defender's `!ml` detections, had flagged Skyfold because of it.
- **Start with Windows is written only when you switch it on.** Skyfold no longer rewrites Windows' autostart entry by itself at every start; antivirus programs treat a program that keeps re-adding itself there as suspicious. If the entry points at an older copy or was removed, *Settings → Start with Windows* shows it off; switch it on to point Windows at this copy.

If your antivirus still flags Skyfold, check the download (below) and please report it in an issue.

Full list: [CHANGELOG](https://github.com/zoranmod/skyfold/blob/main/CHANGELOG.md).

### Download
Download `Skyfold-windows.zip` below, unzip it anywhere and run `Skyfold.exe`. To update by hand, quit Skyfold and replace `Skyfold.exe` and the `_internal` folder with the ones from the zip; keep the `data` folder (your settings).

To check the download, compare it with `Skyfold-windows.zip.sha256`, or run `gh attestation verify Skyfold-windows.zip --repo zoranmod/skyfold`.

Windows SmartScreen may warn about a new, unsigned app: choose *More info → Run anyway*.

---
**Na bosanskom:** Skyfold 1.0.30 smanjuje lažne uzbune antivirusa. `Skyfold.exe` sada ima vlastiti početni dio programa, napravljen iz izvornog koda pri svakom izdanju, umjesto gotovog koji dijele hiljade drugih programa. Skyfold više ne prepisuje sam svoj unos za pokretanje s Windowsom; upiše ga samo kad ti uključiš *Start with Windows* u postavkama.
