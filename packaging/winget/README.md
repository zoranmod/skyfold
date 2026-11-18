# winget manifests

Manifests for the [Windows Package Manager community repository](https://github.com/microsoft/winget-pkgs).
They are submitted there as a pull request (by the maintainer); this folder only keeps a copy.

Check locally before submitting:

```powershell
winget validate --manifest packaging\winget\manifests\z\zoranmod\Skyfold\1.0.11
```

For a new release: copy the version folder, change `PackageVersion`, `ReleaseDate`, `InstallerUrl`,
`InstallerSha256` (from the release's `Skyfold-windows.zip.sha256`, upper case) and `ReleaseNotesUrl`.
