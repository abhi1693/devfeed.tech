import io
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from devfeed_aggregator import image_storage
from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FeedError, FetchResult
from devfeed_core.managed_images import image_variants
from PIL import Image


def picture(width=800, format="PNG"):
    output = io.BytesIO()
    Image.new("RGB", (width, 40), "#abcdef").save(output, format=format)
    return output.getvalue()


def test_original_is_validated_content_addressed_and_uploaded_once(monkeypatch):
    uploads = []
    body = picture()
    monkeypatch.setattr(image_storage, "_fetch", lambda *a, **kw: FetchResult(200, body, a[0]))
    monkeypatch.setattr(image_storage, "exists", lambda client, key: bool(uploads))
    monkeypatch.setattr(image_storage, "upload", lambda *args: uploads.append(args))
    first = image_storage.store_original(None, "https://publisher.test/image.png")
    second = image_storage.store_original(None, "https://other.test/duplicate.png")
    assert first["hash"] == second["hash"]
    assert first["source_url"] != second["source_url"]
    assert len(uploads) == 1
    assert first["source_width"] == 800
    assert image_storage.variant_widths(first) == [320, 640, 800]
    assert image_storage.variant_widths({"source_width": 100}) == [100]


@pytest.mark.parametrize("body", [b"<html>denied</html>", b"<svg></svg>", b"GIF89a", b""])
def test_rejects_invalid_images_before_upload(body):
    with pytest.raises(FeedError, match="invalid image"):
        image_storage.inspect_image(body)


def test_signed_transform_uses_only_configured_original_origin(monkeypatch):
    monkeypatch.setenv("DEVFEED_IMAGE_PUBLIC_URL", "https://images.test")
    monkeypatch.setenv("DEVFEED_IMGPROXY_URL", "http://imgproxy:8080")
    monkeypatch.setenv("DEVFEED_IMGPROXY_KEY", "ab" * 32)
    monkeypatch.setenv("DEVFEED_IMGPROXY_SALT", "cd" * 32)
    get_settings.cache_clear()
    first = image_storage.signed_transform_url("originals/hash", 320)
    assert first.startswith("http://imgproxy:8080/")
    assert "/rs:fit:320:0:0/q:78/f:webp/" in first
    assert first != image_storage.signed_transform_url("originals/hash", 640)


def test_reader_uses_managed_images_only_for_matching_source_and_enabled_storage(monkeypatch):
    monkeypatch.setenv("DEVFEED_IMAGE_STORAGE_ENABLED", "true")
    monkeypatch.setenv("DEVFEED_IMAGE_PUBLIC_URL", "https://images.test")
    get_settings.cache_clear()
    article = SimpleNamespace(
        image_url="https://publisher.test/a",
        managed_image={
            "source_url": "https://publisher.test/a",
            "version": "v1",
            "variants": [{"key": "thumbnails/v1/hash/320.webp", "width": 320}],
        },
    )
    assert image_variants(article) == [
        {"url": "https://images.test/thumbnails/v1/hash/320.webp", "width": 320}
    ]
    article.image_url = "https://publisher.test/b"
    assert image_variants(article) == []
    article.image_url = "https://publisher.test/a"
    monkeypatch.setenv("DEVFEED_IMAGE_STORAGE_ENABLED", "false")
    get_settings.cache_clear()
    assert image_variants(article) == []


def test_proxy_response_must_be_valid_webp_at_requested_width(monkeypatch):
    monkeypatch.setattr(image_storage, "exists", lambda *a: False)
    monkeypatch.setattr(
        image_storage, "signed_transform_url", lambda *a: "http://imgproxy:8080/test"
    )
    monkeypatch.setattr(image_storage, "upload", lambda *a: pytest.fail("Invalid image uploaded"))
    response = SimpleNamespace(status=200, iter_stream=lambda: iter([picture(640)]))
    pool = SimpleNamespace(stream=lambda *a, **kw: nullcontext(response))
    monkeypatch.setattr(image_storage.httpcore, "ConnectionPool", lambda: nullcontext(pool))
    with pytest.raises(FeedError):
        image_storage.store_variant(None, {"hash": "hash", "original_key": "originals/hash"}, 320)
