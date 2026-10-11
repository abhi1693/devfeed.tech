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
        image_storage, "signed_transform_url", lambda *a, **kw: "http://imgproxy:8080/test"
    )
    monkeypatch.setattr(image_storage, "upload", lambda *a: pytest.fail("Invalid image uploaded"))
    response = SimpleNamespace(status=200, iter_stream=lambda: iter([picture(640)]))
    pool = SimpleNamespace(stream=lambda *a, **kw: nullcontext(response))
    monkeypatch.setattr(image_storage.httpcore, "ConnectionPool", lambda: nullcontext(pool))
    with pytest.raises(FeedError):
        image_storage.store_variant(None, {"hash": "hash", "original_key": "originals/hash"}, 320)


@pytest.mark.parametrize("version,quality", [("v1", 78), ("v2", 70)])
def test_thumbnail_encoding_namespace_is_pinned_to_checkpoint(monkeypatch, version, quality):
    urls, uploads = [], []
    monkeypatch.setenv("DEVFEED_IMAGE_PUBLIC_URL", "https://images.test")
    monkeypatch.setenv("DEVFEED_IMGPROXY_URL", "http://imgproxy:8080")
    monkeypatch.setenv("DEVFEED_IMGPROXY_KEY", "ab" * 32)
    monkeypatch.setenv("DEVFEED_IMGPROXY_SALT", "cd" * 32)
    get_settings.cache_clear()
    monkeypatch.setattr(image_storage, "exists", lambda client, key: bool(uploads))

    def transform(url):
        urls.append(url)
        return picture(320, "WEBP")

    monkeypatch.setattr(image_storage, "transform_image", transform)
    monkeypatch.setattr(image_storage, "upload", lambda *args: uploads.append(args))
    asset = {"hash": "hash", "original_key": "originals/hash"}
    if version == "v2":
        asset["thumbnail_version"] = version
    first = image_storage.store_variant(None, asset, 320)
    assert first == image_storage.store_variant(None, asset, 320)
    assert first["key"] == f"thumbnails/{version}/hash/320.webp"
    assert f"/q:{quality}/f:webp/" in urls[0]
    assert len(urls) == len(uploads) == 1


def test_new_original_uses_current_encoding_without_changing_schema(monkeypatch):
    monkeypatch.setattr(image_storage, "_fetch", lambda *a, **kw: FetchResult(200, picture(), a[0]))
    monkeypatch.setattr(image_storage, "exists", lambda *a: True)
    asset = image_storage.store_original(None, "https://publisher.test/a.png")
    assert asset["version"] == "v1"
    assert asset["thumbnail_version"] == "v2"


def test_unknown_encoder_never_writes_into_an_immutable_namespace(monkeypatch):
    monkeypatch.setattr(
        image_storage, "exists", lambda *a: pytest.fail("Unexpected storage access")
    )
    with pytest.raises(FeedError, match="Unknown thumbnail encoding"):
        image_storage.store_variant(None, {"hash": "hash", "thumbnail_version": "v99"}, 320)


@pytest.mark.parametrize("limit", [0, 501])
def test_recompression_backfill_rejects_unbounded_batches(limit):
    from devfeed_core.image_jobs import backfill_thumbnail_encoding

    with pytest.raises(ValueError, match="between 1 and 500"):
        backfill_thumbnail_encoding(None, limit)


def test_recompression_backfill_requires_enabled_storage():
    from devfeed_core.image_jobs import backfill_thumbnail_encoding
    from devfeed_core.services import OperationConflict

    with pytest.raises(OperationConflict, match="Enable image storage"):
        backfill_thumbnail_encoding(None, 1)


def test_v2_generation_leaves_existing_v1_object_bytes_untouched(monkeypatch):
    old_key = "thumbnails/v1/hash/320.webp"
    stored = {old_key: b"existing immutable bytes"}
    monkeypatch.setattr(image_storage, "exists", lambda client, key: key in stored)
    monkeypatch.setattr(
        image_storage, "signed_transform_url", lambda *a, **kw: "http://imgproxy/test"
    )
    monkeypatch.setattr(image_storage, "transform_image", lambda url: picture(320, "WEBP"))
    monkeypatch.setattr(
        image_storage, "upload", lambda client, key, body, mime: stored.__setitem__(key, body)
    )
    asset = {"hash": "hash", "original_key": "originals/hash", "thumbnail_version": "v2"}
    assert image_storage.store_variant(None, asset, 320)["key"] == "thumbnails/v2/hash/320.webp"
    assert stored[old_key] == b"existing immutable bytes"
    assert len(stored) == 2
