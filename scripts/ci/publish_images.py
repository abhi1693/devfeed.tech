"""Promote verified digests: moving branch tags, write-once version tags."""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
IMAGE = re.compile(r"ghcr[.]io/[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)+\Z")


def publication_tag(ref_type: str, ref_name: str, version: str) -> tuple[str, bool]:
    if ref_type == "tag":
        if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", ref_name) or ref_name != f"v{version}":
            raise ValueError("Release tag must match the application's vMAJOR.MINOR.PATCH version")
        return ref_name, True
    if ref_type != "branch" or not ref_name:
        raise ValueError("Only branch and version-tag refs can be published")
    if ref_name == "master":
        return "master", False
    slug = re.sub(r"[^a-z0-9_.-]+", "-", ref_name.lower()).strip("-.")[:90] or "branch"
    # Sanitization alone would collide for feature/foo and feature-foo.
    suffix = hashlib.sha256(ref_name.encode()).hexdigest()[:8]
    return f"branch-{slug}-{suffix}", False


def inspect_digest(reference: str) -> str | None:
    result = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", reference, "--format", "{{json .Manifest}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        # Only an explicit missing manifest is absence; auth/network errors block promotion.
        missing = rf"(?:ERROR: )?{re.escape(reference)}: (?:not found|manifest unknown)"
        if re.fullmatch(missing, result.stderr.strip()):
            return None
        raise RuntimeError(
            f"Cannot establish registry state for {reference}: {result.stderr.strip()}"
        )
    digest = json.loads(result.stdout)["digest"]
    if not DIGEST.fullmatch(digest):
        raise ValueError(f"Registry returned an invalid digest for {reference}")
    return digest


def promote(images: dict[str, str], tag: str, immutable: bool) -> dict[str, str]:
    if set(images) != {"backend", "admin-api", "admin"}:
        raise ValueError("A complete set of all three verified images is required")
    plan = []
    # Check the entire image set before changing any release tag.
    for component, reference in images.items():
        image, digest = reference.rsplit("@", 1)
        if not DIGEST.fullmatch(digest) or not IMAGE.fullmatch(image):
            raise ValueError(f"Invalid image reference for {component}")
        target = f"{image}:{tag}"
        existing = inspect_digest(target)
        if immutable and existing is not None and existing != digest:
            raise ValueError(f"Released tag {target} already exists with a different digest")
        plan.append((reference, target, digest, existing))
    for reference, target, digest, existing in plan:
        if existing != digest:
            # Copy the original index bytes; never rebuild or rewrite its annotations.
            subprocess.run(
                ["docker", "buildx", "imagetools", "create", "--tag", target, reference], check=True
            )
        if inspect_digest(target) != digest:
            raise ValueError(f"Published digest does not match the verified image: {target}")
    return {
        component: f"{reference.rsplit('@', 1)[0]}:{tag}" for component, reference in images.items()
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--ref-type", required=True)
    parser.add_argument("--ref-name", required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    tag, immutable = publication_tag(args.ref_type, args.ref_name, manifest["version"])
    manifest["published_tags"] = promote(manifest["images"], tag, immutable)
    manifest["immutable_release"] = immutable
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n")
