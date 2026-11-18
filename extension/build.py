"""Package the Safari Bookmarks extension: dist/extension/<browser>/ (to load unpacked) and a zip each
(to upload to addons.mozilla.org / the Chrome Web Store / Edge Add-ons).

    python extension/build.py
"""
import json
import shutil
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "dist" / "extension"
FILES = ["background.js", "popup.html", "popup.js", "icons/16.png", "icons/32.png", "icons/48.png",
         "icons/128.png"]


def build(browser: str, manifest: str) -> Path:
    folder = OUT / browser
    shutil.rmtree(folder, ignore_errors=True)
    (folder / "icons").mkdir(parents=True)
    for f in FILES:
        shutil.copy2(HERE / f, folder / f)
    data = json.loads((HERE / manifest).read_text(encoding="utf-8"))
    (folder / "manifest.json").write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    archive = OUT / f"skyfold-safari-bookmarks-{browser}-{data['version']}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(folder.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(folder).as_posix())
    return archive


if __name__ == "__main__":
    for b, m in (("firefox", "manifest.firefox.json"), ("chrome", "manifest.chrome.json")):
        print(build(b, m))
    sys.exit(0)
