# Builds the portable app into dist\Skyfold (copy that folder anywhere).
# The Python environment and build cache live in %LOCALAPPDATA%\Skyfold-build, so the
# ~800 MB of tooling never ends up in iCloud Drive / OneDrive next to the source.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$work = Join-Path $env:LOCALAPPDATA "Skyfold-build"
$venv = Join-Path $work "venv"
$py = Join-Path $venv "Scripts\python.exe"

if (-not (Test-Path $py)) {
    python -m venv $venv
    & $py -m pip install -r requirements.lock.txt
}
$running = Get-Process Skyfold -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$PSScriptRoot\dist\*" }
if ($running) {
    # Ask the running copy to close cleanly (finishes its current step, closes the database).
    Start-Process "$PSScriptRoot\dist\Skyfold\Skyfold.exe" -ArgumentList "--quit" -Wait
    $running | Wait-Process -Timeout 20 -ErrorAction SilentlyContinue
    if (Get-Process -Id $running.Id -ErrorAction SilentlyContinue) {
        throw "Skyfold is still running. Quit it (tray icon → Quit) and run the build again."
    }
}
& $py tools_make_icon.py
$verFile = Join-Path $work "version-info.txt"
& $py tools_version_info.py $verFile  # name, version and project page shown in the exe's Properties

# PyInstaller wipes dist\Skyfold on every build: park the user's data folder (settings) meanwhile.
$data = "dist\Skyfold\data"
$parked = Join-Path $work "data-backup"
if (Test-Path $data) {
    if (Test-Path $parked) { Remove-Item -Recurse -Force $parked }
    Move-Item $data $parked
}

# A program folder on PATH that ships its own ICU (e.g. Poppler's icuuc.dll) would be packaged
# instead of the one Qt needs and break QtCore/QtPdf. Leave out only such folders; Windows' own
# folders and everything else on PATH stay, so the packaged files are otherwise unchanged.
$savedPath = $env:PATH
$env:PATH = ($env:PATH -split ';' | Where-Object {
    -not $_ -or $_ -like "$env:SystemRoot*" -or
    -not (Get-ChildItem -LiteralPath $_ -Filter 'icu*.dll' -ErrorAction SilentlyContinue | Select-Object -First 1)
}) -join ';'
# Python modules that are found by following imports but never run in the app. Each is only reached
# through code Skyfold doesn't use: building packages (setuptools, pulled in by cffi's compiler
# helpers), Python's interactive help (pydoc), pywin32's MFC dialogs (win32ui with its 5.7 MB
# mfc140u.dll, pulled in by win32com's type-library tools), pydantic's version-1 layer and mypy plugin
# (pyicloud uses version 2), and the pure-Python stand-ins for decimal and datetime (the compiled
# ones are packaged). tests\test_build_trim.py imports the whole app with these blocked.
$exclude = @("PIL", "pytest", "setuptools", "pkg_resources", "_distutils_hack", "pydoc", "pydoc_data",
             "win32ui", "pywin", "pydantic.v1", "pydantic.mypy", "_pydecimal", "_pydatetime")
$excludeArgs = $exclude | ForEach-Object { "--exclude-module"; $_ }
# --noupx: PyInstaller packs the DLLs with UPX by itself when it finds one on PATH, and packed files
# are what antivirus programs distrust most. The files stay as their makers built them.
& $py -m PyInstaller --noconfirm --clean --windowed --onedir --noupx --name Skyfold `
    --icon "$PSScriptRoot\skyfold.ico" --version-file $verFile --collect-submodules pyicloud --collect-data pyicloud `
    --collect-data fido2 --collect-data certifi --collect-data tzdata `
    @excludeArgs `
    --distpath "$PSScriptRoot\dist" --workpath (Join-Path $work "build") --specpath $work "$PSScriptRoot\run.py"
$ok = $LASTEXITCODE -eq 0
$env:PATH = $savedPath
if (Test-Path $parked) {
    New-Item -ItemType Directory -Force "dist\Skyfold" | Out-Null
    Move-Item $parked $data
}
if (-not $ok) { throw "PyInstaller failed." }

# Trim what the app never loads (checked against the DLLs a running Skyfold has loaded).
$in = "dist\Skyfold\_internal"
$drop = @(
    # Qt parts a tray app never uses. Qt6Pdf stays: notes render PDF pages to images.
    "PySide6\opengl32sw.dll", "PySide6\Qt6Quick.dll", "PySide6\Qt6Qml.dll", "PySide6\Qt6QmlModels.dll",
    "PySide6\Qt6QmlMeta.dll", "PySide6\Qt6QmlWorkerScript.dll", "PySide6\Qt6VirtualKeyboard.dll",
    "PySide6\translations", "PySide6\plugins\platforminputcontexts",
    "PySide6\Qt6OpenGL.dll",  # nothing packaged links to it
    # QtNetwork only carries the single-instance pipe (QLocalServer): no TLS, no network status. Without
    # the TLS plug-ins, no OpenSSL copy is picked up from whatever program happens to be on PATH either.
    "PySide6\plugins\tls", "PySide6\plugins\networkinformation", "libcrypto-3-x64.dll", "libssl-3-x64.dll",
    "PySide6\plugins\generic",  # touch input sent over the network (TUIO)
    "PySide6\plugins\platforms\qdirect2d.dll", "PySide6\plugins\platforms\qminimal.dll",  # qwindows is used
    # Second and third copies of Microsoft's C++ runtime. Windows loads a DLL of a given name once:
    # python312.dll brings _internal\VCRUNTIME140*.dll, and shiboken6 (always imported before PySide6)
    # brings shiboken6\MSVCP140.dll, so these copies are never the ones loaded.
    "PySide6\MSVCP140.dll", "PySide6\VCRUNTIME140.dll", "PySide6\VCRUNTIME140_1.dll",
    "shiboken6\VCRUNTIME140.dll", "shiboken6\VCRUNTIME140_1.dll",
    # The C runtime for Windows 7 and 8, copied from some program on PATH. Windows 10 and later (all
    # that Qt 6 runs on) have it built in and keep it up to date.
    "ucrtbase.dll", "api-ms-win-*.dll"
)
foreach ($d in $drop) {
    if (Test-Path "$in\$d") { Remove-Item -Recurse -Force "$in\$d" }
}
$mb = (Get-ChildItem dist\Skyfold -Recurse | Measure-Object Length -Sum).Sum / 1MB
"Done: dist\Skyfold ({0:N0} MB)" -f $mb
