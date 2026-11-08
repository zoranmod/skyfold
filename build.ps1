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

# PyInstaller wipes dist\Skyfold on every build: park the user's data folder (settings) meanwhile.
$data = "dist\Skyfold\data"
$parked = Join-Path $work "data-backup"
if (Test-Path $data) {
    if (Test-Path $parked) { Remove-Item -Recurse -Force $parked }
    Move-Item $data $parked
}

& $py -m PyInstaller --noconfirm --clean --windowed --onedir --name Skyfold `
    --icon "$PSScriptRoot\skyfold.ico" --collect-submodules pyicloud --collect-data pyicloud `
    --collect-data fido2 --collect-data certifi --collect-data tzdata `
    --exclude-module PIL --exclude-module pytest `
    --distpath "$PSScriptRoot\dist" --workpath (Join-Path $work "build") --specpath $work "$PSScriptRoot\run.py"
$ok = $LASTEXITCODE -eq 0
if (Test-Path $parked) {
    New-Item -ItemType Directory -Force "dist\Skyfold" | Out-Null
    Move-Item $parked $data
}
if (-not $ok) { throw "PyInstaller failed." }

# Trim Qt parts a tray app never uses.
$qt = "dist\Skyfold\_internal\PySide6"
$drop = @("opengl32sw.dll", "Qt6Quick.dll", "Qt6Qml.dll", "Qt6QmlModels.dll", "Qt6QmlMeta.dll",
          "Qt6QmlWorkerScript.dll", "Qt6VirtualKeyboard.dll", "translations",
          "plugins\platforminputcontexts")  # Qt6Pdf stays: notes render PDF pages to images
foreach ($d in $drop) {
    if (Test-Path "$qt\$d") { Remove-Item -Recurse -Force "$qt\$d" }
}
$mb = (Get-ChildItem dist\Skyfold -Recurse | Measure-Object Length -Sum).Sum / 1MB
"Done: dist\Skyfold ({0:N0} MB)" -f $mb
