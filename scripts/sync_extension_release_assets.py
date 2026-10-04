"""Ensure a GitHub release has the exact Chrome and Edge extension packages."""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from zipfile import ZipFile


def run(*command: str) -> str:
    return subprocess.check_output(command, text=True).strip()


def archive_contents(path: Path) -> dict[str, bytes]:
    with ZipFile(path) as archive:
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise SystemExit(f"Extension archive contains duplicate paths: {path.name}")
        return {name: archive.read(name) for name in sorted(archive.namelist())}


def release_info(tag: str) -> dict:
    return json.loads(
        run(
            "gh",
            "release",
            "view",
            tag,
            "--json",
            "assets,isDraft",
        )
    )


def require_draft(release: dict) -> None:
    if not release["isDraft"]:
        raise SystemExit("Signing prepares draft releases only; published releases are immutable.")


def prepare_draft(tag: str) -> None:
    try:
        release = release_info(tag)
    except subprocess.CalledProcessError:
        # create never overwrites an existing release. Permission, network or
        # existing-release failures remain fatal rather than becoming uploads.
        run(
            "gh", "release", "create", tag, "--draft", "--verify-tag", "--title", tag, "--notes", ""
        )
        release = release_info(tag)
    require_draft(release)


def packages() -> list[Path]:
    return [
        Path(f"apps/extensions/dist/devfeed-{browser}-extension-{_version()}.zip")
        for browser in ("chrome", "edge")
    ]


def sync_packages(tag: str, *, draft_only: bool = False) -> None:
    release = release_info(tag)
    if draft_only:
        require_draft(release)
    existing = {asset["name"] for asset in release["assets"]}
    missing: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="devfeed-release-assets-") as temporary:
        directory = Path(temporary)
        for package in packages():
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
            # ZIP timestamps can differ across builds. After checking every
            # payload, sign/submit the exact bytes already attached to the tag.
            shutil.copyfile(published, package)

        if missing:
            if draft_only:
                require_draft(release_info(tag))
            run("gh", "release", "upload", tag, *(str(path) for path in missing))
            for package in missing:
                run("gh", "release", "download", tag, "--pattern", package.name, "--dir", temporary)
                if package.read_bytes() != (directory / package.name).read_bytes():
                    raise SystemExit(f"Uploaded release asset failed verification: {package.name}")

    print(f"Verified Chrome and Edge extension assets on GitHub release {tag}.")


def attach_attestation(tag: str, bundle: Path) -> None:
    require_draft(release_info(tag))
    repository = os.environ["GITHUB_REPOSITORY"]
    source_sha = run("git", "rev-parse", "HEAD")
    for package in packages():
        run(
            "gh",
            "attestation",
            "verify",
            str(package),
            "--bundle",
            str(bundle),
            "--repo",
            repository,
            "--signer-workflow",
            f"{repository}/.github/workflows/extension-release.yml",
            "--source-digest",
            source_sha,
            "--source-ref",
            f"refs/tags/{tag}",
            "--deny-self-hosted-runners",
        )
    # Each retry has its own signed bundle. Never overwrite an earlier proof.
    run_id = int(os.environ["GITHUB_RUN_ID"])
    attempt = int(os.environ["GITHUB_RUN_ATTEMPT"])
    name = f"devfeed-extensions-{_version()}-{run_id}-{attempt}.sigstore.json"
    with tempfile.TemporaryDirectory(prefix="devfeed-release-attestation-") as temporary:
        signed = Path(temporary) / name
        shutil.copyfile(bundle, signed)
        # Export the original, already verified signed in-toto envelope. Keep
        # its Sigstore bundle alongside it for certificate/transparency proof.
        envelope = json.loads(bundle.read_text())["dsseEnvelope"]
        statement = json.loads(base64.b64decode(envelope["payload"], validate=True))
        if (
            envelope["payloadType"] != "application/vnd.in-toto+json"
            or statement["_type"] != "https://in-toto.io/Statement/v1"
            or statement["predicateType"] != "https://slsa.dev/provenance/v1"
        ):
            raise SystemExit("Expected signed SLSA v1 build provenance.")
        provenance = Path(temporary) / name.replace(".sigstore.json", ".intoto.jsonl")
        provenance.write_text(json.dumps(envelope, separators=(",", ":")) + "\n")
        require_draft(release_info(tag))
        run("gh", "release", "upload", tag, str(signed), str(provenance))
        downloaded = Path(temporary) / "downloaded"
        downloaded.mkdir()
        for proof in (signed, provenance):
            run("gh", "release", "download", tag, "--pattern", proof.name, "--dir", str(downloaded))
            if proof.read_bytes() != (downloaded / proof.name).read_bytes():
                raise SystemExit("Uploaded release provenance failed byte verification.")
    print(f"Verified and attached extension build provenance to draft release {tag}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare-draft", action="store_true")
    mode.add_argument("--attach-attestation", type=Path)
    parser.add_argument("--draft-only", action="store_true")
    args = parser.parse_args()
    tag = os.environ["RELEASE_TAG"]
    if args.prepare_draft:
        prepare_draft(tag)
    elif args.attach_attestation:
        attach_attestation(tag, args.attach_attestation)
    else:
        sync_packages(tag, draft_only=args.draft_only)


def _version() -> str:
    manifest = json.loads(Path("apps/extensions/chrome/manifest.json").read_text())
    return manifest["version"]


if __name__ == "__main__":
    main()
