**Skyfold 1.0.6 is a public beta.**

### What's new
- **New look**: calm, flat design in light and dark mode; a clearer tray panel with an iCloud storage bar.
- **Safer deletions**: every deletion question is tied to exactly what it showed (side, items and their iCloud versions). An answer from an outdated window is ignored, and you're asked again. Deletions are only decided after a full read, so the limit always counts everything. Empty folders count too.
- **Renames and moves**: a rename on the PC is carried to iCloud only when the content is proven the same. Photos renamed in Explorer are followed by their content and are never taken for a deletion.
- **Upgrades**: the sync database of an earlier installation is copied, never changed or deleted. If it can't be taken over, Skyfold asks before it starts.
- **Hundreds of fixes** from 30+ rounds of independent code review, each with a regression test. Full list: [CHANGELOG](https://github.com/zoranmod/skyfold/blob/main/CHANGELOG.md).

### Please note
- Two-way iCloud Drive sync is new. Skyfold asks before deleting more items than your limit (default 5), and deleted items go to the Recycle Bin or "Recently Deleted". **Keep your own backup of anything important.**
- Windows SmartScreen may warn about a new, unsigned app: *More info → Run anyway*.
- Found a problem? [Open an issue](https://github.com/zoranmod/skyfold/issues).

### Download
`Skyfold-windows.zip` below; unzip anywhere and run `Skyfold.exe`. Check it with `Skyfold-windows.zip.sha256`.

---
**Na bosanskom:** Skyfold 1.0.6 je javna beta. Novi izgled, sigurnija pitanja prije brisanja, praćenje preimenovanih fajlova i fotografija, i stotine popravki iz nezavisnih pregleda koda. Dvosmjerna Drive sinhronizacija je nova; čuvaj rezervnu kopiju važnih stvari.
