# Security policy

Skyfold holds a signed-in iCloud session on your PC, so security reports are taken seriously.

## Supported versions

Only the [latest release](https://github.com/zoranmod/skyfold/releases/latest) gets fixes.

## Reporting a vulnerability

**Please don't open a public issue.** Use GitHub's private reporting instead:
**Security tab → Report a vulnerability** ([direct link](https://github.com/zoranmod/skyfold/security/advisories/new)).

Include what you found, how to reproduce it, and which version you used. Never include your Apple ID password, two-factor codes or files from your `session` folder.

You can expect a first reply within 7 days. Once a fix is released, you'll be credited in the release notes unless you prefer not to be.

## What Skyfold stores, and where

| What | Where | Notes |
|---|---|---|
| Apple ID password | nowhere | used only during sign-in |
| Apple session cookies / tokens | `%LOCALAPPDATA%\Skyfold\session` | plain files, protected by your Windows account; valid about two months; deleted by **Sign out** |
| Sync database | `%LOCALAPPDATA%\Skyfold\state.sqlite3` (plus `-wal`/`-shm`) | file paths, iCloud item IDs, the recent activity list, a fingerprint (hash) of the Apple ID |
| iCloud Drive listing | `%LOCALAPPDATA%\Skyfold\drive_cache.json` | names of all files and folders in iCloud Drive |
| Log | `%LOCALAPPDATA%\Skyfold\skyfold.log` (and `.log.1`, `.log.2`) | file and folder names, errors; the Apple ID, cookies, tokens and people's names are filtered out; no password |
| Crash log | `%LOCALAPPDATA%\Skyfold\crash.log` | only if Skyfold ever crashes: the parts of the program that were running (function names); no note text, file contents or password |
| Account picture | `%LOCALAPPDATA%\Skyfold\account-….png` | the photo on your own card in Contacts, small; deleted by **Sign out** |
| Safari bookmarks (when turned on) | `%LOCALAPPDATA%\Skyfold\safari-bookmarks.json` | titles and addresses, for the browser extension; deleted by **Sign out** |
| Settings (incl. Apple ID email) | `data\settings.json` next to `Skyfold.exe` (or `%LOCALAPPDATA%\Skyfold\data` when that folder can't be written; *Settings → About* shows which) | |

**Sign out** removes the session, the account picture and the bookmarks file. The sync database, the Drive listing, the log and the Apple ID in `settings.json` stay, so a later sign-in with the same Apple ID continues where it stopped. To remove everything, quit Skyfold and delete `%LOCALAPPDATA%\Skyfold` and the `data` folder next to `Skyfold.exe`.

Skyfold connects to Apple's iCloud services (through [pyicloud](https://github.com/timlaing/pyicloud)) and, once a day unless turned off, to GitHub's public API to look for a newer Skyfold release. It has no server, telemetry or analytics. An update is only installed when you click *Update now*: the release zip from this repository is checked against its published SHA-256, and GitHub's build attestation must name this repository's release workflow and tag, before anything is replaced.

## In scope

- Leaks of the session, password or personal data (to disk, logs, other hosts)
- Ways for another local user or a malicious file in a synced folder to make Skyfold delete, overwrite or upload data it shouldn't
- Problems in the release build or GitHub Actions workflow

Out of scope: anyone who already runs code as your Windows user can read the session, as with a signed-in browser.
