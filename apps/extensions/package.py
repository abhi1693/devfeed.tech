"""Package the built Chrome extension for Web Store upload."""

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parent / "dist"
source = root / "chrome"
version = json.loads((source / "manifest.json").read_text())["version"]
destination = root / f"devfeed-new-tab-{version}.zip"
with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
    for name in ("manifest.json", "newtab.html", "newtab.js", "newtab.css", "icon.png"):
        archive.write(source / name, name)
print(destination)
