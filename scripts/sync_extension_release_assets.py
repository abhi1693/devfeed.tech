"""Ensure a GitHub release has the exact Chrome and Edge extension packages."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from zipfile import ZipFile


def run(*command: str) -> str:
    return subprocess.check_output(command, text=True).strip()


def archive_contents(path: Path) -> dict[str, bytes]:
    with ZipFile(path) as archive:
        return {name: archive.read(name) for name in sorted(archive.namelist())}


def main() -> None:
    tag = os.environ["RELEASE_TAG"]
    version = _version()
    release = json.loads(
        run(
            "gh",
            "release",
            "view",
            tag,
            "--json",
            "assets,body",
            "--jq",
            "{assets: .assets, body: .body}",
        )
    )
    existing = {asset["name"] for asset in release["assets"]}
    packages = [
        Path(f"apps/extensions/dist/devfeed-{browser}-extension-{version}.zip")
        for browser in ("chrome", "edge")
    ]
    missing: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="devfeed-release-assets-") as temporary:
        directory = Path(temporary)
        for package in packages:
            if not package.is_file():
                raise SystemExit(f"Built extension package is missing: {package}")
            if package.name not in existing:
                missing.append(package)
                continue
            run("gh", "release", "download", tag, "--pattern", package.name, "--dir", temporary)
            published = directory / package.name
            if not published.is_file() or archive_contents(package) != archive_contents(published):
                raise SystemExit(
                    f"Release asset {package.name} differs from the package built from {tag}; "
                    "release assets are immutable, so create a corrected application release."
                )

        if missing:
            run("gh", "release", "upload", tag, *(str(path) for path in missing))
            for package in missing:
                run("gh", "release", "download", tag, "--pattern", package.name, "--dir", temporary)
                if archive_contents(package) != archive_contents(directory / package.name):
                    raise SystemExit(f"Uploaded release asset failed verification: {package.name}")

    append_store_checklist(tag, release.get("body") or "", version)
    print(f"Verified Chrome and Edge extension assets on GitHub release {tag}.")


def _version() -> str:
    manifest = json.loads(Path("apps/extensions/chrome/manifest.json").read_text())
    return manifest["version"]


def append_store_checklist(tag: str, body: str, version: str) -> None:
    marker = "<!-- devfeed-extension-store-status -->"
    if marker in body:
        return
    section = (
        "## Browser extension store status\n\n"
        f"- Chrome Web Store: automatic submission pending (version `{version}`). "
        "<!-- devfeed-store-chrome -->\n"
        f"- Microsoft Edge Add-ons: automatic submission pending (version `{version}`). "
        "<!-- devfeed-store-edge -->\n"
        "Store approval and publication timing are controlled by each store.\n"
        f"{marker}"
    )
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".md") as notes:
        notes.write(f"{body.rstrip()}\n\n{section}\n")
        notes.flush()
        run("gh", "release", "edit", tag, "--notes-file", notes.name)


if __name__ == "__main__":
    main()
