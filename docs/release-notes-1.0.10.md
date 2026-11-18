**Skyfold 1.0.10 (public beta): updates from inside the app, and safer deletion questions.**

> **Updating from 1.0.9 or earlier:** download this version once by hand (below). From 1.0.10 on, Skyfold tells you when a new version is out and updates itself with one click.

### What's new
- **Updates from inside the app.** Skyfold checks once a day whether a newer version is out (only "Skyfold/<version>" is sent to GitHub; you can turn it off in Settings → General). It tells you once per version and shows a row in the panel. The update window shows what's new; **Update now** downloads the release, checks it against its published SHA-256, closes Skyfold, replaces only the program (your settings and synced files are never touched), puts the old version back if anything fails, and starts Skyfold again. Settings → About has **Check for updates**.

### Fixed
- In a deletion question with both an iCloud and a PC part, answering one part no longer made the other part's button stop working.
- iCloud Drive: approving "Delete on this PC too" no longer recycles a different file that was put at the same place meanwhile (for example a copy restored from a backup). Skyfold checks it is still the very file it asked about, and asks again otherwise.
- iCloud Drive: a file renamed only in letter case after iCloud deleted it no longer waits forever; once you edit it, it goes up under its new name.
- iCloud Drive: saving a file while a deletion question waits no longer causes two full reads of iCloud Drive each time.
- Start-up: a leftover database journal that can't be set aside now stops the start instead of being mixed into your sync data.

Full list: [CHANGELOG](https://github.com/zoranmod/skyfold/blob/main/CHANGELOG.md).

### Download
`Skyfold-windows.zip` below; unzip anywhere and run `Skyfold.exe` (or replace your existing Skyfold folder; settings and sync state are kept). Check it with `Skyfold-windows.zip.sha256`. Windows SmartScreen may warn about a new, unsigned app: *More info → Run anyway*.

---
**Na bosanskom:** Skyfold 1.0.10 se od sada ažurira iz same aplikacije: javi kad izađe nova verzija, pokaže šta je novo i ažurira se jednim klikom (postavke i fajlovi ostaju netaknuti). Ovu verziju, ako imaš 1.0.9 ili stariju, skini još jednom ručno. Uz to, sigurnija su pitanja prije brisanja na iCloud Driveu.
