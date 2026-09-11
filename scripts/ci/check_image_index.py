"""Require exactly the supported Linux ARM64 runtime platform in an OCI index."""

import json
import sys
from pathlib import Path


def check_index(document: dict) -> None:
    platforms = [
        (entry["platform"]["os"], entry["platform"]["architecture"])
        for entry in document["manifests"]
        # BuildKit adds attestation manifests with unknown/unknown platforms.
        if entry.get("annotations", {}).get("vnd.docker.reference.type") != "attestation-manifest"
    ]
    if sorted(platforms) != [("linux", "arm64")]:
        raise ValueError(f"Unexpected runtime platforms: {platforms}")


if __name__ == "__main__":
    check_index(json.loads(Path(sys.argv[1]).read_text()))
