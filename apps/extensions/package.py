"""Package the built Chromium extension for store upload."""

import argparse
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parent / "dist"
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("browser", nargs="?", choices=("chrome", "edge"), default="chrome")
browser = parser.parse_args().browser
source = root / browser
manifest = json.loads((source / "manifest.json").read_text())
version = manifest["version"]
# Keep the stable development ID in the unpacked build, but not in store uploads.
manifest.pop("key", None)
if browser == "edge":
    manifest.pop("update_url", None)
destination = root / f"devfeed-{browser}-extension-{version}.zip"
with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
    archive.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
    for name in ("newtab.html", "newtab.js", "newtab.css", "icon.png"):
        archive.write(source / name, name)
    tool_icons = source / "tool-icons"
    for path in sorted(tool_icons.rglob("*")):
        if path.is_file():
            archive.write(path, path.relative_to(source))
print(destination)
