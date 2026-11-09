# Changelog

All notable changes to Skyfold are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.0.4] - 2026-11-09

Fixes from an independent code review (OpenAI Codex), each with a regression test.

### Fixed
- Photos: an album folder never replaces a file that Skyfold didn't put there.
- Notes: a note never overwrites an existing .html file it didn't write; a full read that suddenly misses most notes no longer removes their pages.
- Notes: drawings and PDFs are only replaced inside the notes' own attachments folder.
- iCloud Drive: a file whose size iCloud doesn't report is treated as "unknown", never as empty.
- iCloud Drive: planning very large deletions is fast (no more comparing every path with every other).
- Sign-in: a sign-in that was cancelled can't complete in the background later.
- Sign-out: session files are removed reliably, and no running request can write them back.
- Find My: the Map button says that it opens Google Maps.

## [1.0.3] - 2026-11-09

(1.0.1 and 1.0.2 were tagged but never released: their automated builds stopped on test-suite problems, now fixed. The app itself is unchanged.)

### Fixed
- iCloud Drive: a file that was only opened on an iPhone or iPad (which changes its iCloud version tag, not its content) is no longer downloaded again.

## [1.0.0] - 2026-11-08

First public release.

### Added
- **iCloud Photos:** downloads the whole library (photos, videos, Live Photo videos) to a folder of your choice, as one folder, by year or by year/month. Deletions are synced in both directions: PC files go to the Recycle Bin, iCloud items to "Recently Deleted".
- **Albums as folders** under `Albums\`, using hard links (no extra disk space).
- **iCloud Drive two-way sync:** full local copy or chosen top-level folders; new, changed, deleted, renamed and moved files and folders in both directions; conflicts keep both versions (`name (PC conflict …)`); Mac packages are left in iCloud.
- **Apple Notes export** to HTML with pictures and attachments, a searchable `Notes.html` index, and deleted notes moved to the Recycle Bin.
- **Find My:** device list with battery and last-seen time, map link, Play sound.
- **iCloud storage** usage in the tray panel.
- **Safety:** confirmation before more deletions than a limit (default 25), protection against missing or unplugged folders, disk-space check that keeps 2 GB free, no-warranty notice before the first sync.
- Sign-in with two-factor authentication (trusted-device or SMS code); the password is never stored. Session and database live in `%LOCALAPPDATA%\Skyfold`.
- Tray panel and settings with Windows light/dark theme, download speed limit, pause, Start with Windows, notifications.
- English and Bosnian.
- Portable build (`Skyfold-windows.zip`) from GitHub Actions, with a packaged self-test (`--selftest`).

[Unreleased]: https://github.com/zoranmod/skyfold/compare/v1.0.4...HEAD
[1.0.4]: https://github.com/zoranmod/skyfold/compare/v1.0.3...v1.0.4
[1.0.3]: https://github.com/zoranmod/skyfold/compare/v1.0.0...v1.0.3
[1.0.0]: https://github.com/zoranmod/skyfold/releases/tag/v1.0.0
