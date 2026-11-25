# Skyfold promo video

A short motion-design video of what Skyfold does, for the website. It is not a screen recording:
every frame is drawn on an HTML canvas by `draw(t)` from made-up data (painted landscapes, a
fictional budget and garden note), so nothing personal can ever appear in it and every render is
the same.

| File | What it is |
| --- | --- |
| `timeline.mjs` | Scenes, cuts in musical bars (120 BPM: a beat is exactly 15 frames), click moments, easing |
| `draw.mjs` | `draw(t)`: the backdrop, the title column, the card of each scene, the phase rail |
| `music.mjs` | The music, synthesized in code (pads, bass, an FM arpeggio, drums, sound effects on the clicks) |
| `render.mjs` | Local server + headless Chromium (DevTools protocol, no npm packages) + FFmpeg |
| `check.mjs` | Checks the MP4s: decode, size, length, faststart, under 15 MB |
| `timeline.test.mjs` | Cuts on beats, events inside their scenes, the music's length and level |

## Scenes

| Scene | 16:9 | 9:16 | Shows |
| --- | --- | --- | --- |
| hook | 0–6 s | 0–4 s | iPhone, iCloud, a Windows PC; the Skyfold mark links them |
| tray | 6–11 s | 4–7 s | The tray icon opens the panel; counts fill in, *Everything is in sync* |
| photos | 11–17 s | 7–11 s | Photos fly into `iCloud Photos\2026\11` in File Explorer |
| gallery | 17–25 s | 11–17 s | The Photos window: the wheel glides, a photo opens, the heart reaches the iPhone |
| drive | 25–31 s | 17–21 s | Ctrl+S on the PC goes up to the iPhone; a new file comes back down |
| notes | 31–37 s | 21–25 s | Search finds "garden"; the note with its checklist and table |
| safe | 37–42 s | 25–28 s | Recently Deleted, the Recycle Bin, a conflict keeps both copies |
| end | 42–48 s | 28–32 s | Logo, name, the website |

## Make it

Needs Node 22+, a headless Chromium (Playwright's cached `chrome-headless-shell` is found by itself,
or set `PROMO_CHROMIUM`) and FFmpeg with libx264 (`winget install Gyan.FFmpeg`, or `PROMO_FFMPEG`).
The fonts are Windows' own Segoe UI Variable and Cascadia Mono, so render on Windows 11.

```
node render.mjs --preview           # a local page with play, seek and 16:9 / 9:16
node render.mjs --frames            # control pictures of every scene (seconds, not minutes)
node render.mjs --frames --at=560   # just those frames
node render.mjs                     # both formats, silent and with music, into out/
node check.mjs
node --test timeline.test.mjs
```

The page may load only its own files: anything else fails the render. The videos are not kept in
Git (`out/` is ignored); the website gets its copies at release time.
