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
from unittest.mock import Mock, patch

import httpx
from devfeed_aggregator import image_storage_base, logo_storage
from devfeed_core.feeds.fetcher import FetchResult
from devfeed_core.logos import SOURCE_LOGO_SIZES
from PIL import Image

ROOT = Path("reports/source-logos")


def upload(key, body, **policy):
    path = ROOT / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def transform(key, width, *, options=None):
    return f"http://127.0.0.1:18825/insecure/{options}/plain/local:///{key}"


def main():
    results = []
    storage = Mock()
    storage.exists.side_effect = lambda key: (ROOT / key).exists()
    storage.put.side_effect = upload
    originals = logo_storage.LogoOriginalStore(storage)
    variants = logo_storage.LogoVariantStore(storage, sizes=SOURCE_LOGO_SIZES)
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
            patch.object(image_storage_base.ImgproxyTransform, "url", side_effect=transform),
            patch.object(
                image_storage_base.ImgproxyTransform,
                "fetch",
                side_effect=lambda url: httpx.get(url).raise_for_status().content,
            ),
        ):
            asset = originals.store(str(path))
            stored = [variants.store(asset, size) for size in variants.sizes(asset)]
        results.append(
            {
                "input": str(path.relative_to(ROOT)),
                "source_url": metadata.get("source_url"),
                "original_cache_control": metadata.get("cache_control"),
                "sha256": hashlib.sha256(body).hexdigest(),
                "dimensions": list(Image.open(io.BytesIO(body)).size),
                "original_bytes": len(body),
                "variants": [{**v, "bytes": (ROOT / v["key"]).stat().st_size} for v in stored],
            }
        )
    (ROOT / "encoding.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
