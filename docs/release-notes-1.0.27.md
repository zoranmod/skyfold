**Skyfold 1.0.27 (public beta): read your notes inside Skyfold.**

> On 1.0.10 or later? Skyfold offers this update by itself: Settings → About → *Check for updates* → *Update now*.

### Added
- **A Notes window.** Click *Notes* in the panel (or in the tray menu). Your folders are on the left, as in Apple Notes, with their note counts and subfolders you can fold. Your notes are in the middle with their date and first line (newest first, oldest first or by title), and the note itself is on the right, with its checklists, tables, pictures and scans.
- **Search inside your notes.** Search finds words in the titles, folders and the text of your notes, with or without č, ć, đ, š, ž, and marks them in the note. `Ctrl+K` jumps to the search field.
- **Comfortable reading.** Make the text and pictures larger or smaller (`Ctrl +`, `Ctrl -`, `Ctrl 0` or `Ctrl` + mouse wheel) and fill the screen (maximize, or `F11`). The ⋯ menu gives the window its own light, dark or OLED theme and still opens the note in your browser. Attachments open in their usual app.
- **Private by design.** Notes stay read-only and one-way. Locked notes are listed but never shown, and after *Sign out* the window shows nothing until you sign in again. Whatever way the window is closed, the text it read is dropped.

Full list: [CHANGELOG](https://github.com/zoranmod/skyfold/blob/main/CHANGELOG.md).

### Download
Download `Skyfold-windows.zip` below, unzip it anywhere and run `Skyfold.exe`. To update by hand, quit Skyfold and replace `Skyfold.exe` and the `_internal` folder with the ones from the zip; keep the `data` folder (your settings).

To check the download, compare it with `Skyfold-windows.zip.sha256`, or run `gh attestation verify Skyfold-windows.zip --repo zoranmod/skyfold`.

Windows SmartScreen may warn about a new, unsigned app: choose *More info → Run anyway*.

---
**Na bosanskom:** Skyfold 1.0.27 donosi prozor za bilješke. Klikni *Bilješke* u panelu: lijevo su folderi kao u Apple Notesu, u sredini bilješke s datumom i prvim redom, a desno sama bilješka sa slikama, tabelama i skeniranim dokumentima. Pretraga traži i po tekstu bilješki, sa ili bez č, ć, đ, š, ž. Tekst i slike možeš povećati (`Ctrl +` ili `Ctrl` + točkić miša), prozor raširiti na cijeli ekran (`F11`), a u meniju ⋯ izabrati posebnu temu prozora. Zaključane bilješke se nikad ne prikazuju, a kad zatvoriš prozor, pročitani tekst se zaboravlja.
