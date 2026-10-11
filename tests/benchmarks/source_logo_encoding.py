"""Benchmark saved publisher logos with real normalization and local imgproxy.

Mount reports/source-logos at /images in imgproxy, local filesystem root /images,
insecure URLs allowed, port 18825. Save PNG inputs in reports/source-logos/inputs.
Run: uv run python tests/benchmarks/source_logo_encoding.py
Downloaded artwork and reports remain local ignored artifacts.
"""

import hashlib
import io
import json
from pathlib import Path
from unittest.mock import patch

import httpx
from devfeed_aggregator import image_storage, logo_storage
from devfeed_core.feeds.fetcher import FetchResult
from devfeed_core.logos import SOURCE_LOGO_SIZES
from PIL import Image

ROOT = Path("reports/source-logos")


def upload(client, key, body, mime):
    path = ROOT / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def transform(key, width, *, options=None):
    return f"http://127.0.0.1:18825/insecure/{options}/plain/local:///{key}"


def main():
    results = []
    for path in sorted((ROOT / "inputs").glob("*.png")):
        body = path.read_bytes()
        metadata = (
            json.loads(path.with_suffix(".json").read_text())
            if path.with_suffix(".json").exists()
            else {}
        )
        with (
            patch.object(
                logo_storage, "fetch_topic_logo", return_value=FetchResult(200, body, str(path))
            ),
            patch.object(
                image_storage, "exists", side_effect=lambda client, key: (ROOT / key).exists()
            ),
            patch.object(image_storage, "upload", side_effect=upload),
            patch.object(image_storage, "signed_transform_url", side_effect=transform),
            patch.object(
                image_storage,
                "transform_image",
                side_effect=lambda url: httpx.get(url).raise_for_status().content,
            ),
        ):
            asset = logo_storage.store_logo_original(None, str(path))
            variants = [
                logo_storage.store_logo_variant(None, asset, size) for size in SOURCE_LOGO_SIZES
            ]
        results.append(
            {
                "input": str(path.relative_to(ROOT)),
                "source_url": metadata.get("source_url"),
                "original_cache_control": metadata.get("cache_control"),
                "sha256": hashlib.sha256(body).hexdigest(),
                "dimensions": list(Image.open(io.BytesIO(body)).size),
                "original_bytes": len(body),
                "variants": [{**v, "bytes": (ROOT / v["key"]).stat().st_size} for v in variants],
            }
        )
    (ROOT / "encoding.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
