**Skyfold 1.0.19 (public beta): much faster and safer uploads.**

> On 1.0.10 or later? Skyfold offers this update by itself: Settings → About → *Check for updates* → *Update now*.

### Changed
- **Faster uploads to iCloud Drive.** Each upload gets a bigger send buffer, and new files go up three at a time, each on its own connection. On a 200 Mbit/s fibre line one upload went from a steady ~0.5 MB/s to about 1.4–7 MB/s, and several files together reached ~10 MB/s; Apple's servers vary. A file that replaces one in iCloud still goes alone. Uploads no longer hold up Photos and Notes.
- **Big files don't need memory.** A large video is sent from disk piece by piece instead of being loaded into RAM first.
- **Uploads show their progress** (MB sent, speed, time left) for iCloud Drive and iCloud Photos, like downloads, and *Stop* stops a big upload in the middle.
- **Renaming or moving a Notes folder on your iPhone or Mac moves its pages too.** The first check after updating reads all notes once.
- After an upload, the next check no longer downloads the file again just to look at it.

### Fixed
- A file being uploaded can't be changed meanwhile, so iCloud never gets a mix of two versions. A file replacing one in iCloud is locked before the old version goes to Recently Deleted.
- An upload counts only when iCloud confirms it; otherwise it is tried again, and the PC file is never taken for deleted in iCloud.
- A lost connection during uploads stops the other uploads too. An interrupted replacement never makes a duplicate copy of identical contents, and when another device saved a different version meanwhile, both are kept.
- Deleting thousands of files or folders at once is counted much faster before Skyfold asks.

Full list: [CHANGELOG](https://github.com/zoranmod/skyfold/blob/main/CHANGELOG.md).

### Download
Download `Skyfold-windows.zip` below, unzip it anywhere and run `Skyfold.exe`. To update by hand, quit Skyfold and replace `Skyfold.exe` and the `_internal` folder with the ones from the zip; keep the `data` folder (your settings).

To check the download, compare it with `Skyfold-windows.zip.sha256`, or run `gh attestation verify Skyfold-windows.zip --repo zoranmod/skyfold`.

Windows SmartScreen may warn about a new, unsigned app: choose *More info → Run anyway*.

---
**Na bosanskom:** Skyfold 1.0.19 šalje na iCloud Drive mnogo brže: veći buffer za slanje i tri nova fajla istovremeno (na optičkoj vezi od 200 Mbit/s s ~0,5 MB/s na 1,4–7 MB/s za jedan fajl, do ~10 MB/s za više fajlova). Veliki fajlovi se šalju s diska bez učitavanja u memoriju, a slanje na Drive i u Photos sada pokazuje MB, brzinu i preostalo vrijeme; Stop prekida i veliko slanje. Slanje se računa tek kad ga iCloud potvrdi, fajl se tokom slanja ne može mijenjati, a preimenovan folder s bilješkama premješta i stranice bilješki.
