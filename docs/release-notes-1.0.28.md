**Skyfold 1.0.28 (public beta): a steadier Notes window.**

> On 1.0.10 or later? Skyfold offers this update by itself: Settings → About → *Check for updates* → *Update now*.

### Fixed
- **Skyfold could crash or freeze with the Notes window open** shortly after it started. When the saved sign-in was restored, the Notes window was closed from Skyfold's background thread, which Qt doesn't allow. It is now closed on the right thread, and only when another Apple ID signs in or you sign out.
- **If Skyfold ever crashes, it now says where.** `crash.log` in `%LOCALAPPDATA%\Skyfold` records which part of the program was running, with no note text, file contents or password. Attach it to a bug report.

### Changed
- **Scanned documents open with all their pages** (up to 60, then *Show more pages* shows the rest, as many as it says). Pages load as you scroll to them, so a long scan still opens quickly.
- **The Notes window comes back where you were.** Close it and open it again while Skyfold runs: the same folder and note, at the same place. Zooming keeps the same text in view.
- **The panel says what happened.** A note moved to another folder shows as *moved to* that folder, a note with a new title as *renamed*, an edited note as *updated*, each on its own line when there are a few. In iCloud Drive, a newer version of a file you had shows as *updated*.

Full list: [CHANGELOG](https://github.com/zoranmod/skyfold/blob/main/CHANGELOG.md).

### Download
Download `Skyfold-windows.zip` below, unzip it anywhere and run `Skyfold.exe`. To update by hand, quit Skyfold and replace `Skyfold.exe` and the `_internal` folder with the ones from the zip; keep the `data` folder (your settings).

To check the download, compare it with `Skyfold-windows.zip.sha256`, or run `gh attestation verify Skyfold-windows.zip --repo zoranmod/skyfold`.

Windows SmartScreen may warn about a new, unsigned app: choose *More info → Run anyway*.

---
**Na bosanskom:** Skyfold 1.0.28 popravlja rušenje i zamrzavanje kad je prozor za bilješke otvoren odmah nakon pokretanja. Ako se Skyfold ikad sruši, `crash.log` pokaže gdje, bez sadržaja bilješki. Skenirani dokumenti se otvaraju sa svim stranicama, prozor za bilješke se vraća na istu bilješku i isto mjesto, a panel piše da je bilješka premještena, preimenovana ili ažurirana, umjesto samo „preuzeto“.
