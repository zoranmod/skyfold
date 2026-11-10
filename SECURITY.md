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
| Settings (incl. Apple ID email) | `data\settings.json` next to `Skyfold.exe` | |

**Sign out** removes the session. The sync database, the Drive listing, the log and the Apple ID in `settings.json` stay, so a later sign-in with the same Apple ID continues where it stopped. To remove everything, quit Skyfold and delete `%LOCALAPPDATA%\Skyfold` and the `data` folder next to `Skyfold.exe`.

Skyfold connects only to Apple's iCloud services (through [pyicloud](https://github.com/timlaing/pyicloud)). It has no server, telemetry, analytics or update check.

## In scope

- Leaks of the session, password or personal data (to disk, logs, other hosts)
- Ways for another local user or a malicious file in a synced folder to make Skyfold delete, overwrite or upload data it shouldn't
- Problems in the release build or GitHub Actions workflow

Out of scope: anyone who already runs code as your Windows user can read the session, as with a signed-in browser.
