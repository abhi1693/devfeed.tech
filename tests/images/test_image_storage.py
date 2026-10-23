import io
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_aggregator import image_storage, image_storage_base
from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FeedError, FetchResult
from devfeed_core.managed_images import image_variants
from devfeed_core.object_storage import R2ConfigurationError, R2Storage
from PIL import Image


def picture(width=800, format="PNG"):
    output = io.BytesIO()
    Image.new("RGB", (width, 40), "#abcdef").save(output, format=format)
    return output.getvalue()


@pytest.fixture
def storage():
    objects = {}
    client = Mock(spec=R2Storage)
    client.exists.side_effect = objects.__contains__
    client.put.side_effect = lambda key, body, **kwargs: objects.__setitem__(key, body)
    return client, objects


def test_original_is_validated_content_addressed_and_uploaded_once(monkeypatch, storage):
    client, objects = storage
    originals = image_storage.ImageOriginalStore(client)
    body = picture()
    monkeypatch.setattr(image_storage, "_fetch", lambda *a, **kw: FetchResult(200, body, a[0]))
    first = originals.store("https://publisher.test/image.png")
    second = originals.store("https://other.test/duplicate.png")
    assert first["hash"] == second["hash"]
    assert first["source_url"] != second["source_url"]
    assert objects == {first["original_key"]: body}
    client.put.assert_called_once_with(
        first["original_key"],
        body,
        content_type="image/png",
        cache_control="public, max-age=31536000, immutable",
    )
    assert first["source_width"] == 800
    variants = image_storage.ThumbnailVariantStore(client)
    assert variants.sizes(first) == [320, 640, 800]
    assert variants.sizes({"source_width": 100}) == [100]


@pytest.mark.parametrize(
    ("source_width", "expected"),
    [
        (100, [100]),
        (640, [320, 480, 640]),
        (800, [320, 480, 640, 768, 800]),
        (1600, [320, 480, 640, 768, 960]),
    ],
)
def test_article_thumbnails_fill_card_density_gaps_without_upscaling(
    storage, source_width, expected
):
    variants = image_storage.ArticleThumbnailVariantStore(storage[0])
    assert variants.sizes({"source_width": source_width, "thumbnail_version": "v2"}) == expected
    for version in ({}, {"thumbnail_version": "v1"}):
        assert variants.sizes({"source_width": source_width, **version}) == sorted(
            {min(width, source_width) for width in (320, 640, 960)}
        )


@pytest.mark.parametrize("body", [b"<html>denied</html>", b"<svg></svg>", b"GIF89a", b""])
def test_rejects_invalid_images_before_upload(body):
    with pytest.raises(FeedError, match="invalid image"):
        image_storage.ImageOriginalStore(Mock(spec=R2Storage)).inspect(body)


def test_signed_transform_uses_only_configured_original_origin(monkeypatch):
    monkeypatch.setenv("DEVFEED_IMAGE_PUBLIC_URL", "https://images.test")
    monkeypatch.setenv("DEVFEED_IMGPROXY_URL", "http://imgproxy:8080")
    monkeypatch.setenv("DEVFEED_IMGPROXY_KEY", "ab" * 32)
    monkeypatch.setenv("DEVFEED_IMGPROXY_SALT", "cd" * 32)
    get_settings.cache_clear()
    first = image_storage_base.ImgproxyTransform().url("originals/hash", 320)
    assert first.startswith("http://imgproxy:8080/")
    assert "/rs:fit:320:0:0/q:78/f:webp/" in first
    assert first != image_storage_base.ImgproxyTransform().url("originals/hash", 640)


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


def test_proxy_response_must_be_valid_webp_at_requested_width(monkeypatch, storage):
    client, _ = storage
    monkeypatch.setattr(
        image_storage_base.ImgproxyTransform,
        "url",
        lambda self, *a, **kw: "http://imgproxy:8080/test",
    )
    response = SimpleNamespace(status=200, iter_stream=lambda: iter([picture(640)]))
    pool = SimpleNamespace(stream=lambda *a, **kw: nullcontext(response))
    monkeypatch.setattr(image_storage_base.httpcore, "ConnectionPool", lambda: nullcontext(pool))
    with pytest.raises(FeedError):
        image_storage.ThumbnailVariantStore(client).store(
            {"hash": "hash", "original_key": "originals/hash"}, 320
        )
    client.put.assert_not_called()


@pytest.mark.parametrize("version,quality", [("v1", 78), ("v2", 70)])
def test_thumbnail_encoding_namespace_is_pinned_to_checkpoint(
    monkeypatch, version, quality, storage
):
    client, objects = storage
    urls = []
    monkeypatch.setenv("DEVFEED_IMAGE_PUBLIC_URL", "https://images.test")
    monkeypatch.setenv("DEVFEED_IMGPROXY_URL", "http://imgproxy:8080")
    monkeypatch.setenv("DEVFEED_IMGPROXY_KEY", "ab" * 32)
    monkeypatch.setenv("DEVFEED_IMGPROXY_SALT", "cd" * 32)
    get_settings.cache_clear()
    variants = image_storage.ThumbnailVariantStore(client)

    def transform(url):
        urls.append(url)
        return picture(320, "WEBP")

    monkeypatch.setattr(
        image_storage_base.ImgproxyTransform, "fetch", lambda self, url: transform(url)
    )
    asset = {"hash": "hash", "original_key": "originals/hash"}
    if version == "v2":
        asset["thumbnail_version"] = version
    first = variants.store(asset, 320)
    assert first == variants.store(asset, 320)
    assert first["key"] == f"thumbnails/{version}/hash/320.webp"
    assert f"/q:{quality}/f:webp/" in urls[0]
    assert len(urls) == len(objects) == client.put.call_count == 1
    assert client.put.call_args.kwargs == {
        "content_type": "image/webp",
        "cache_control": "public, max-age=31536000, immutable",
    }


def test_new_original_uses_current_encoding_without_changing_schema(monkeypatch, storage):
    client, _ = storage
    monkeypatch.setattr(image_storage, "_fetch", lambda *a, **kw: FetchResult(200, picture(), a[0]))
    client.exists.return_value = True
    client.exists.side_effect = None
    asset = image_storage.ImageOriginalStore(client).store("https://publisher.test/a.png")
    assert asset["version"] == "v1"
    assert asset["thumbnail_version"] == "v2"
    client.put.assert_not_called()


def test_unknown_encoder_never_writes_into_an_immutable_namespace(storage):
    client, _ = storage
    with pytest.raises(FeedError, match="Unknown thumbnail encoding"):
        image_storage.ThumbnailVariantStore(client).store(
            {"hash": "hash", "thumbnail_version": "v99"}, 320
        )
    client.exists.assert_not_called()
    client.put.assert_not_called()


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


def test_v2_generation_leaves_existing_v1_object_bytes_untouched(monkeypatch, storage):
    client, stored = storage
    old_key = "thumbnails/v1/hash/320.webp"
    stored[old_key] = b"existing immutable bytes"
    monkeypatch.setattr(
        image_storage_base.ImgproxyTransform, "url", lambda self, *a, **kw: "http://imgproxy/test"
    )
    monkeypatch.setattr(
        image_storage_base.ImgproxyTransform, "fetch", lambda self, url: picture(320, "WEBP")
    )
    asset = {"hash": "hash", "original_key": "originals/hash", "thumbnail_version": "v2"}
    assert (
        image_storage.ThumbnailVariantStore(client).store(asset, 320)["key"]
        == "thumbnails/v2/hash/320.webp"
    )
    assert stored[old_key] == b"existing immutable bytes"
    assert len(stored) == 2


@pytest.fixture
def worker_storage_config(monkeypatch):
    settings = SimpleNamespace(
        image_public_url="https://images.test",
        imgproxy_url="http://imgproxy:8080",
        imgproxy_key="key",
        imgproxy_salt="salt",
    )
    monkeypatch.setattr(image_storage, "get_settings", lambda: settings)
    return settings


@pytest.mark.parametrize(
    "missing", ["image_public_url", "imgproxy_url", "imgproxy_key", "imgproxy_salt"]
)
def test_worker_requires_public_images_and_transform_configuration(
    monkeypatch, worker_storage_config, missing
):
    setattr(worker_storage_config, missing, None)
    create = Mock()
    monkeypatch.setattr(R2Storage, "from_settings", create)
    with pytest.raises(FeedError, match="configuration is incomplete") as error:
        image_storage.storage_client()
    assert error.value.reason == "image_configuration"
    create.assert_not_called()


def test_worker_uses_shared_storage_factory(monkeypatch, worker_storage_config):
    create = Mock(return_value=Mock(spec=R2Storage))
    monkeypatch.setattr(R2Storage, "from_settings", create)
    assert image_storage.storage_client() is create.return_value
    create.assert_called_once_with(worker_storage_config)


def test_worker_preserves_configuration_error_category(monkeypatch, worker_storage_config):
    monkeypatch.setattr(
        R2Storage, "from_settings", Mock(side_effect=R2ConfigurationError("Missing transport"))
    )
    with pytest.raises(FeedError, match="configuration is incomplete") as error:
        image_storage.storage_client()
    assert error.value.reason == "image_configuration"


@pytest.mark.parametrize("status,retryable", [(404, False), (429, True), (503, True)])
def test_imgproxy_transform_preserves_retry_policy(monkeypatch, status, retryable):
    response = SimpleNamespace(status=status, iter_stream=lambda: iter([]))
    pool = SimpleNamespace(stream=lambda *a, **kw: nullcontext(response))
    monkeypatch.setattr(image_storage_base.httpcore, "ConnectionPool", lambda: nullcontext(pool))
    transform = image_storage_base.ImgproxyTransform(SimpleNamespace(image_max_bytes=8))
    with pytest.raises(FeedError, match="Image transformation failed") as error:
        transform.fetch("http://imgproxy:8080/test")
    assert error.value.reason == "image_transform"
    assert error.value.retryable is retryable


def test_imgproxy_transform_bounds_bytes_and_transport_time(monkeypatch):
    response = SimpleNamespace(status=200, iter_stream=lambda: iter([b"abc", b"def"]))
    stream = Mock(return_value=nullcontext(response))
    pool = SimpleNamespace(stream=stream)
    monkeypatch.setattr(image_storage_base.httpcore, "ConnectionPool", lambda: nullcontext(pool))
    transform = image_storage_base.ImgproxyTransform(SimpleNamespace(image_max_bytes=5))
    with pytest.raises(FeedError, match="Thumbnail exceeds limit") as error:
        transform.fetch("http://imgproxy:8080/test")
    assert error.value.reason == "image_size"
    stream.assert_called_once_with(
        "GET",
        "http://imgproxy:8080/test",
        extensions={"timeout": dict.fromkeys(["connect", "read", "write", "pool"], 15)},
    )
