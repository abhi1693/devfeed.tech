"""Bounded logo normalization. Only sanitized SVG reaches the image renderer."""

import hashlib
import io
import math
import re
import xml.etree.ElementTree as ET

from devfeed_core.feeds.fetcher import FeedError, fetch_topic_logo
from devfeed_core.logos import LOGO_SIZES, LOGO_VERSION
from PIL import Image, ImageOps

from devfeed_aggregator import image_storage

SVG_NS = "http://www.w3.org/2000/svg"
SVG_TAGS = set(
    [
        "svg",
        "g",
        "defs",
        "path",
        "rect",
        "circle",
        "ellipse",
        "line",
        "polyline",
        "polygon",
        "text",
        "tspan",
        "title",
        "desc",
        "style",
        "linearGradient",
        "radialGradient",
        "stop",
        "clipPath",
        "mask",
        "pattern",
        "symbol",
        "use",
        "marker",
    ]
)


def sanitize_svg(body: bytes) -> bytes:
    try:
        text = body.decode("utf-8-sig")
        # Reject DTD/entities before parsing (including UTF-16/32 disguised input).
        if "\x00" in text or re.search(r"<!\s*(DOCTYPE|ENTITY)", text, re.I):
            raise ValueError("XML declarations")
        root = ET.fromstring(text)
        if root.tag not in {"svg", f"{{{SVG_NS}}}svg"}:
            raise ValueError("Not SVG")
        nodes = list(root.iter())
        if len(nodes) > 10_000:
            raise ValueError("Too many SVG elements")
        for node in nodes:
            tag = node.tag.removeprefix(f"{{{SVG_NS}}}")
            if tag not in SVG_TAGS:
                raise ValueError("Unsupported SVG element")
            for key, value in node.attrib.items():
                name = key.rsplit("}", 1)[-1].lower()
                if name.startswith("on") or name == "base":
                    raise ValueError("Active SVG attribute")
                if name in {"href", "src"} and not re.fullmatch(r"#[\w.-]+", value):
                    raise ValueError("External SVG reference")
                check_svg_css(value)
            if tag == "style":
                check_svg_css(node.text or "")
        viewbox = root.get("viewBox")
        if viewbox:
            box = [float(v) for v in re.split(r"[\s,]+", viewbox.strip())]
            if len(box) != 4 or not all(math.isfinite(v) for v in box):
                raise ValueError("Invalid viewBox")
            width, height = box[2:]
        else:
            width, height = [
                float(root.attrib[key].removesuffix("px")) for key in ("width", "height")
            ]
            root.set("viewBox", f"0 0 {width} {height}")
        if (
            not all(math.isfinite(v) for v in (width, height))
            or width <= 0
            or height <= 0
            or max(width / height, height / width) > 100
        ):
            raise ValueError("Invalid SVG dimensions")
        scale = max(LOGO_SIZES) / max(width, height)
        root.set("width", str(width * scale))
        root.set("height", str(height * scale))
        ET.register_namespace("", SVG_NS)
        return ET.tostring(root, encoding="utf-8")
    except (ValueError, KeyError, UnicodeError, ET.ParseError) as exc:
        raise FeedError("Unsupported or unsafe SVG logo", reason="invalid_topic_logo") from exc


def check_svg_css(value: str):
    # Disallow escapes and at-rules so CSS cannot hide network/file references.
    if "\\" in value or "@" in value or "/*" in value:
        raise ValueError("Unsupported SVG CSS")
    for match in re.finditer(r"url\s*\((.*?)\)", value, re.I | re.S):
        if not re.fullmatch(r"[\s\"']*#[\w.-]+[\s\"']*", match[1]):
            raise ValueError("External SVG CSS reference")


def store_logo_original(client, source: str) -> dict:
    result = fetch_topic_logo(source)
    body = result.body
    if (result.content_type or "").split(";", 1)[0] == "image/svg+xml" or body.lstrip().startswith(
        (b"<", b"\xef\xbb\xbf")
    ):
        from cairosvg import svg2png

        try:
            sanitized = sanitize_svg(body)
            root = ET.fromstring(sanitized)
            # CSS can override root attributes. Bound Cairo's output surface
            # explicitly using the aspect ratio validated by the sanitizer.
            dimensions = {
                f"output_{axis}": max(1, min(max(LOGO_SIZES), round(float(root.attrib[axis]))))
                for axis in ("width", "height")
            }
            body = svg2png(bytestring=sanitized, unsafe=False, **dimensions)
        except FeedError:
            raise
        except Exception as exc:
            raise FeedError("SVG logo conversion failed", reason="invalid_topic_logo") from exc
    image_storage.inspect_image(body)
    # One normalized PNG master is displayable before variants are generated.
    with Image.open(io.BytesIO(body)) as opened:
        master = ImageOps.exif_transpose(opened).convert("RGBA")
        master.thumbnail((max(LOGO_SIZES), max(LOGO_SIZES)), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        master.save(output, format="PNG", optimize=True)
        body, mime, extension = output.getvalue(), "image/png", "png"
    digest = hashlib.sha256(body).hexdigest()
    key = f"originals/topic-logos/{LOGO_VERSION}/{digest}.{extension}"
    if not image_storage.exists(client, key):
        image_storage.upload(client, key, body, mime)
    return {
        "source_url": source,
        "hash": digest,
        "original_key": key,
        "version": LOGO_VERSION,
        "variants": [],
    }


def store_logo_variant(client, asset: dict, size: int) -> dict:
    key = f"topic-logos/{LOGO_VERSION}/{asset['hash']}/{size}.webp"
    if not image_storage.exists(client, key):
        # Fit every source to the same canvas fraction, including small rasters.
        # Otherwise the visible logo would shrink when a larger variant is selected.
        url = image_storage.signed_transform_url(
            asset["original_key"], size, options=f"rs:fit:{size}:{size}:1/f:png"
        )
        body = image_storage.transform_image(url)
        image_storage.inspect_image(body)
        with Image.open(io.BytesIO(body)) as image:
            if image.width > size or image.height > size:
                raise FeedError("Oversized transformed logo", reason="image_transform")
            canvas = Image.new("RGBA", (size, size))
            canvas.alpha_composite(
                image.convert("RGBA"), ((size - image.width) // 2, (size - image.height) // 2)
            )
            output = io.BytesIO()
            canvas.save(output, format="WEBP", lossless=True, method=6)
        image_storage.upload(client, key, output.getvalue(), "image/webp")
    return {"key": key, "width": size}
