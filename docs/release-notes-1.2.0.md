**Skyfold 1.2 (public beta): polished, steadier, safer.**

After the new look of 1.1, this release went through two independent reviews of the whole app. Everything they found is fixed, along with many details you would notice in daily use.

- **The tray panel keeps one size.** It no longer shrinks or jumps when you click *Sync now* or the bell. The syncing ring turns smoothly, *Stop* is a clear red button, and the footer says when Skyfold last checked in a short, readable way.
- **Stop means stop.** A Stop ends the whole round: Notes, bookmarks and iCloud Drive don't start afterwards, and the panel keeps saying *Sync stopped* (or *uploads waiting*) instead of a green "up to date".
- **Notifications in Windows 11's own layout**, with buttons of equal width. *Show in folder* opens the folder of the newest photo with that photo selected, and only appears when something new arrived. A card shows the photos of that sync, never an older one, and is exactly as tall as its text.
- **Zoom you can see:** "− 100% +" in the Notes and Photos windows; click the percentage to go back to the usual size.
- **Sign-in looks like the first-run steps before it**: the same see-through window, the same size.
- **Safer deletions:** the deletion question lists every item it covers, fits small screens, and *Show the list* shows it at once. If iCloud doesn't list every Drive folder, your folder choice is kept instead of widening.
- **Security:** the update window never loads a picture from your PC or a network share, and nothing of your bookmarks is written after you sign out.
- **Smaller things:** counts read naturally in Bosnian and English ("3 slike", "1 photo"); maximized Notes and Photos keep the close button in the title bar and come back maximized; Find My keeps one window; Settings → About shows where your settings really are.

Full list: [CHANGELOG](https://github.com/zoranmod/skyfold/blob/main/CHANGELOG.md).

Download `Skyfold-windows.zip` below, unzip and run `Skyfold.exe`. Already using Skyfold? It updates itself: *What's new* → *Update now*. Your settings and files stay as they are.

To check the download, compare it with `Skyfold-windows.zip.sha256`, or run `gh attestation verify Skyfold-windows.zip --repo zoranmod/skyfold`.

**Na bosanskom:** Skyfold 1.2 je doteran i stabilniji. Panel više ne mijenja visinu, Stop zaustavlja cijeli krug i panel to jasno kaže, obavještenja imaju raspored kao u Windowsu 11, a „Prikaži u folderu“ otvara folder baš te slike. Bilješke i Fotografije imaju „− 100% +“ za veličinu, prijava izgleda kao prvi koraci, pitanje za brisanje pokazuje cijeli spisak i na malim ekranima, a brojevi se čitaju prirodno („3 slike“, „1 dan“). Ispravljene su i sve greške koje su našla dva nezavisna pregleda aplikacije.
