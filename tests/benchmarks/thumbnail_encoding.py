"""Compare fixed-size WebP quality settings through the deployed imgproxy encoder.

Inputs stay local: run imgproxy with a read-only fixture mount and local:/// sources.
No asset storage, database mutations, or ingestion-limit changes are performed.
"""

import argparse
import base64
import hashlib
import io
import json
import time
from pathlib import Path
from urllib.request import urlopen

from PIL import Image, ImageChops, ImageStat


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="http://127.0.0.1:18825")
    parser.add_argument("--output", type=Path, default=Path("reports/thumbnail-encoding"))
    parser.add_argument("--images", nargs="+", default=["photo.jpg", "screenshot.png", "text.png"])
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []
    for name in args.images:
        digest = hashlib.sha256((args.output / name).read_bytes()).hexdigest()
        encoded = base64.urlsafe_b64encode(f"local:///{name}".encode()).decode().rstrip("=")
        for width in [320, 640, 960]:
            for quality in [78, 74, 70, 66]:
                started = time.perf_counter()
                with urlopen(
                    f"{args.origin}/insecure/rs:fit:{width}:0:0/q:{quality}/f:webp/{encoded}",
                    timeout=30,
                ) as response:
                    body = response.read()
                elapsed = (time.perf_counter() - started) * 1000
                image = Image.open(io.BytesIO(body)).convert("RGB")
                with Image.open(args.output / name) as source:
                    reference = source.convert("RGB").resize(image.size, Image.Resampling.LANCZOS)
                rmse = sum(
                    x * x for x in ImageStat.Stat(ImageChops.difference(image, reference)).rms
                )
                output = f"{Path(name).stem}-{width}-q{quality}.webp"
                (args.output / output).write_bytes(body)
                rows.append(
                    dict(
                        image=name,
                        input_sha256=digest,
                        width=image.width,
                        height=image.height,
                        quality=quality,
                        bytes=len(body),
                        encode_ms=elapsed,
                        mse=rmse / 3,
                        output=output,
                    )
                )
    (args.output / "encoding.json").write_text(json.dumps(rows, indent=2) + "\n")
    for name in args.images:
        before = sum(row["bytes"] for row in rows if row["image"] == name and row["quality"] == 78)
        for quality in [74, 70, 66]:
            after = sum(
                row["bytes"] for row in rows if row["image"] == name and row["quality"] == quality
            )
            print(f"{name} q{quality}: {before} -> {after} bytes ({1 - after / before:.1%} saved)")


if __name__ == "__main__":
    main()
