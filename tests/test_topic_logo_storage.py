import io
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_aggregator import image_storage_base, logo_storage
from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FeedError, FetchResult
from devfeed_core.logos import (
    LOGO_SIZES,
    SOURCE_LOGO_SIZES,
    logo_current,
    logo_url,
    logo_variants,
)
from devfeed_core.object_storage import R2Storage
from PIL import Image


def picture(width=200, height=100):
    output = io.BytesIO()
    Image.new("RGBA", (width, height), (255, 0, 0, 255)).save(output, "PNG")
    return output.getvalue()


@pytest.fixture
def storage():
    objects = {}
    client = Mock(spec=R2Storage)
    client.exists.side_effect = objects.__contains__
    client.put.side_effect = lambda key, body, **kwargs: objects.__setitem__(key, body)
    return client, objects


def test_svg_normalization_preserves_geometry_and_local_references():
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100">'
        b'<defs><linearGradient id="g"><stop stop-color="red"/></linearGradient></defs>'
        b'<path fill="url(#g)" d="M0 0h200v100H0z"/></svg>'
    )
    root = ET.fromstring(logo_storage.sanitize_svg(svg))
    assert float(root.get("width")) == 96
    assert float(root.get("height")) == 48
    assert root.get("viewBox") == "0 0 200 100"


@pytest.mark.parametrize(
    "content",
    [
        '<!DOCTYPE svg [<!ENTITY x "test">]><svg width="1" height="1"/>',
        '<svg width="1" height="1" onload="alert(1)"/>',
        '<svg width="1" height="1"><script/></svg>',
        '<svg width="1" height="1"><foreignObject/></svg>',
        '<svg width="1" height="1"><image href="https://example.com/a"/></svg>',
        '<svg width="1" height="1"><use href="file:///etc/passwd"/></svg>',
        '<svg width="1" height="1"><path fill="url(https://example.com/a)"/></svg>',
        '<svg width="1" height="1"><style>@import "https://example.com/a";</style></svg>',
        '<svg width="1" height="1"><style>path{fill:u/**/rl(https://example.com/a)}</style></svg>',
        '<svg width="nan" height="1"/>',
        '<svg width="inf" height="1"/>',
        '<svg viewBox="0 0 0 1"/>',
        '<svg viewBox="0 0 1000 1"/>',
    ],
)
def test_svg_rejects_active_external_and_invalid_input(content):
    with pytest.raises(FeedError, match="unsafe SVG"):
        logo_storage.sanitize_svg(content.encode())


def test_normalized_master_is_bounded_and_deduplicated(monkeypatch, storage):
    client, uploaded = storage
    monkeypatch.setattr(
        logo_storage, "fetch_topic_logo", lambda *a, **kw: FetchResult(200, picture(), a[0])
    )
    first = logo_storage.LogoOriginalStore(client).store("https://one.test/logo.png")
    second = logo_storage.LogoOriginalStore(client).store("https://two.test/logo.png")
    assert first["hash"] == second["hash"]
    assert len(uploaded) == 1
    assert Image.open(io.BytesIO(next(iter(uploaded.values())))).size == (96, 48)
    assert client.put.call_count == 1


@pytest.mark.parametrize("bitmap_format", ["png", "bmp"])
def test_favicon_ico_is_normalized_without_expanding_article_image_policy(
    monkeypatch, storage, bitmap_format
):
    client, uploaded = storage
    output = io.BytesIO()
    Image.new("RGBA", (64, 64), (20, 90, 160, 255)).save(
        output, "ICO", sizes=[(16, 16), (32, 32), (64, 64)], bitmap_format=bitmap_format
    )
    body = output.getvalue()
    monkeypatch.setattr(logo_storage, "fetch_topic_logo", lambda url: FetchResult(200, body, url))
    asset = logo_storage.LogoOriginalStore(client).store("https://publisher.test/favicon.ico")
    with Image.open(io.BytesIO(uploaded[asset["original_key"]])) as master:
        assert master.format == "PNG"
        assert master.size == (64, 64)
        assert master.getpixel((32, 32)) == (20, 90, 160, 255)
    with pytest.raises(FeedError, match="Unsupported or invalid image"):
        image_storage_base.ImageStore(client).inspect(body)


def test_variants_preserve_transparency_aspect_ratio_and_skip_existing(monkeypatch, storage):
    client, uploaded = storage
    variants = logo_storage.LogoVariantStore(client, sizes=SOURCE_LOGO_SIZES)
    monkeypatch.setattr(
        image_storage_base.ImgproxyTransform, "url", lambda self, key, width, **kw: str(width)
    )
    monkeypatch.setattr(
        image_storage_base.ImgproxyTransform,
        "fetch",
        lambda self, url: picture(int(url), int(url) // 2),
    )
    asset = {"hash": "abc", "original_key": "originals/topic-logos/v1/abc.svg"}
    assert LOGO_SIZES == (32, 64, 96)
    assert logo_storage.LogoVariantStore(client).sizes(asset) == list(LOGO_SIZES)
    assert variants.sizes(asset) == list(SOURCE_LOGO_SIZES)
    for size in variants.sizes(asset):
        variant = variants.store(asset, size)
        image = Image.open(io.BytesIO(uploaded[variant["key"]])).convert("RGBA")
        assert image.size == (size, size)
        assert image.getpixel((0, 0))[3] == 0
        assert image.getpixel((size // 2, size // 2)) == (255, 0, 0, 255)
    monkeypatch.setattr(
        image_storage_base.ImgproxyTransform,
        "fetch",
        lambda self, *a: pytest.fail("Repeated transform"),
    )
    variants.store(asset, 32)
    assert len(uploaded) == 4
    for call in client.put.call_args_list:
        assert call.kwargs == {
            "content_type": "image/webp",
            "cache_control": "public, max-age=31536000, immutable",
        }


def test_public_urls_never_expose_upstream_urls_and_keep_last_completed_logo(monkeypatch):
    monkeypatch.setenv("DEVFEED_IMAGE_PUBLIC_URL", "https://images.test")
    get_settings.cache_clear()
    topic = SimpleNamespace(logo_url="https://remote.test/logo.svg", managed_logo={})
    assert logo_url(topic) is None
    topic.managed_logo = {
        "source_url": topic.logo_url,
        "version": "v1",
        "variants": [{"key": f"logos/{size}.webp", "width": size} for size in LOGO_SIZES],
    }
    assert logo_current(topic)
    assert logo_url(topic, 96) == "https://images.test/logos/96.webp"
    assert [v["width"] for v in logo_variants(topic)] == list(LOGO_SIZES)
    topic.logo_url = "https://remote.test/replacement.svg"
    assert not logo_current(topic)
    assert logo_url(topic) == "https://images.test/logos/64.webp"
    topic.logo_url = None
    assert logo_url(topic) is None


@pytest.mark.integration
@pytest.mark.parametrize("kind", ["svg", "small-raster"])
def test_actual_imgproxy_normalizes_logos(monkeypatch, kind, storage):
    """Contract check against the pinned imgproxy provisioned by integration CI."""
    import os
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    endpoint = os.environ.get("DEVFEED_TEST_IMGPROXY_URL")
    if not endpoint:
        pytest.skip("Set DEVFEED_TEST_IMGPROXY_URL for the imgproxy contract test")
    svg = logo_storage.sanitize_svg(
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100">'
        b'<path fill="#1255ff" d="M0 0h200v100H0z"/></svg>'
    )

    if kind == "small-raster":
        output = io.BytesIO()
        Image.new("RGBA", (16, 8), (18, 85, 255, 255)).save(output, "PNG")
        svg = output.getvalue()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml" if kind == "svg" else "image/png")
            self.end_headers()
            self.wfile.write(svg)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("DEVFEED_IMAGE_PUBLIC_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("DEVFEED_IMGPROXY_URL", endpoint)
    monkeypatch.setenv("DEVFEED_IMGPROXY_KEY", "ab" * 32)
    monkeypatch.setenv("DEVFEED_IMGPROXY_SALT", "cd" * 32)
    get_settings.cache_clear()
    client, uploaded = storage
    try:
        for size in LOGO_SIZES:
            result = logo_storage.LogoVariantStore(client).store(
                {"hash": "svg-contract", "original_key": "originals/logo.svg"}, size
            )
            image = Image.open(io.BytesIO(uploaded[result["key"]])).convert("RGBA")
            assert image.size == (size, size)
            assert image.getpixel((0, 0))[3] == 0
            assert image.getpixel((size // 2, size // 2)) == (18, 85, 255, 255)
            # Visible geometry fills the same fraction at every resolution.
            assert image.getpixel((size // 8, size // 2))[3] == 255
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("extension", ["svg", "png"])
def test_saved_original_is_used_when_variants_are_absent(monkeypatch, extension):
    monkeypatch.setenv("DEVFEED_IMAGE_PUBLIC_URL", "https://images.test")
    get_settings.cache_clear()
    key = f"originals/topic-logos/v1/hash.{extension}"
    topic = SimpleNamespace(logo_url="https://remote.test/logo", managed_logo={"original_key": key})
    assert logo_variants(topic) == []
    assert logo_url(topic) == f"https://images.test/{key}"
    topic.managed_logo["variants"] = [{"key": "logos/32.webp", "width": 32}]
    assert logo_url(topic) == "https://images.test/logos/32.webp"
    topic.logo_url = None
    assert logo_url(topic) is None


@pytest.mark.parametrize("css_kind", ["none", "inline", "stylesheet"])
@pytest.mark.parametrize("width,height", [(200, 100), (100, 200), (10, 10), (100, 1)])
def test_svg_is_converted_before_original_upload(monkeypatch, css_kind, width, height, storage):
    # CSS deliberately contradicts the validated viewBox aspect ratio and size.
    style = 'style="width:200px;height:200px"' if css_kind == "inline" else ""
    stylesheet = (
        "<style>svg { width:200px !important; height:200px !important; }</style>"
        if css_kind == "stylesheet"
        else ""
    )
    body = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" {style}>'
        f'{stylesheet}<path fill="red" d="M0 0h{width}v{height}H0z"/></svg>'
    ).encode()
    expected = tuple(max(1, round(96 * axis / max(width, height))) for axis in (width, height))
    inspect = image_storage_base.ImageStore.inspect
    inspected = []

    def inspect_rendered(store, data):
        # Check the real renderer output BEFORE thumbnailing can hide an allocation.
        with Image.open(io.BytesIO(data)) as rendered:
            inspected.append(rendered.size)
            assert rendered.size == expected
        return inspect(store, data)

    monkeypatch.setattr(image_storage_base.ImageStore, "inspect", inspect_rendered)
    client, uploaded = storage
    monkeypatch.setattr(
        logo_storage,
        "fetch_topic_logo",
        lambda url: FetchResult(200, body, url, content_type="image/svg+xml"),
    )
    asset = logo_storage.LogoOriginalStore(client).store("https://remote.test/logo.svg")
    assert len(uploaded) == 1
    key, png = next(iter(uploaded.items()))
    mime = client.put.call_args.kwargs["content_type"]
    assert key == asset["original_key"] and key.endswith(".png")
    assert mime == "image/png"
    assert png.startswith(b"\x89PNG")
    assert asset["variants"] == []
    image = Image.open(io.BytesIO(png))
    assert inspected == [expected]
    assert image.size == expected
    red, green, blue, alpha = image.getpixel((expected[0] // 2, expected[1] // 2))
    assert (red, green, blue) == (255, 0, 0)
    # A subpixel-height shape is antialiased in its minimum one-pixel canvas.
    assert alpha >= 240 if min(expected) == 1 else alpha == 255


def test_changed_publisher_artwork_gets_new_immutable_identity(monkeypatch, storage):
    client, uploaded = storage
    body = picture()
    monkeypatch.setattr(logo_storage, "fetch_topic_logo", lambda url: FetchResult(200, body, url))
    first = logo_storage.LogoOriginalStore(client).store("https://publisher.test/logo.png")
    first_bytes = uploaded[first["original_key"]]
    image = Image.new("RGBA", (200, 100), (0, 0, 255, 128))
    output = io.BytesIO()
    image.save(output, "PNG")
    body = output.getvalue()
    second = logo_storage.LogoOriginalStore(client).store("https://publisher.test/logo.png")
    assert second["hash"] != first["hash"]
    assert second["original_key"] != first["original_key"]
    assert uploaded[first["original_key"]] == first_bytes
    assert len(uploaded) == 2


def test_ingested_publisher_logo_uploads_with_immutable_cache_headers(monkeypatch):
    from botocore.exceptions import ClientError

    monkeypatch.setenv("DEVFEED_IMAGE_STORAGE_BUCKET", "test-images")
    get_settings.cache_clear()
    monkeypatch.setattr(
        logo_storage, "fetch_topic_logo", lambda url: FetchResult(200, picture(), url)
    )
    client = Mock()
    client.head_object.side_effect = ClientError(
        {"ResponseMetadata": {"HTTPStatusCode": 404}}, "HeadObject"
    )
    asset = logo_storage.LogoOriginalStore(R2Storage("test-images", client=client)).store(
        "https://publisher.test/logo.png"
    )
    uploaded = client.put_object.call_args.kwargs
    assert uploaded["Key"] == asset["original_key"]
    assert uploaded["CacheControl"] == "public, max-age=31536000, immutable"
    assert uploaded["ContentType"] == "image/png"
