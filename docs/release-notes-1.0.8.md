**Skyfold 1.0.8 (public beta): sign-in that works.**

### What's fixed
- **The two-factor code window now always comes.** Pressing Enter after the password could silently cancel the sign-in: Apple sent the code to your phone, but there was nowhere to type it. Enter now never presses Cancel.
- **One sign-in window per attempt.** Closing it (X or Esc) only hides it; clicking Sign in again brings back the same window, with the code field if Apple already sent the code. Only Cancel stops a sign-in.
- **Clear messages.** When Apple temporarily refuses sign-ins after several attempts, Skyfold says to wait about 15 minutes instead of claiming a wrong password. Any failure is shown in the window.
- **No endless retries** when Apple's iCloud access check fails on Apple's side: Skyfold renews the session quietly and asks you to sign in only if that doesn't help.
- The "Sign-in required" card in the panel can be clicked and highlights on hover.

Full list: [CHANGELOG](https://github.com/zoranmod/skyfold/blob/main/CHANGELOG.md).

### Download
`Skyfold-windows.zip` below; unzip anywhere and run `Skyfold.exe` (or replace your existing Skyfold folder; settings and sync state are kept). Check it with `Skyfold-windows.zip.sha256`. Windows SmartScreen may warn about a new, unsigned app: *More info → Run anyway*.

---
**Na bosanskom:** Skyfold 1.0.8 popravlja prijavu: prozor za kod (2FA) se sada uvijek pojavi, Enter više ne otkazuje prijavu, a zatvaranje prozora ga samo sakrije dok Apple ne odgovori.
