"""Untrusted logos are rejected before rendering or publication to object storage."""

from unittest.mock import Mock

import pytest
from devfeed_aggregator import logo_storage
from devfeed_core.feeds.fetcher import FeedError, FetchResult
from test_topic_logo_storage import picture
from test_topic_logo_storage import storage as storage


@pytest.mark.parametrize(
    "body",
    [
        b"<html><body>Not a logo</body></html>",
        b'<svg viewBox="0 0 10"/>',
        b'<svg viewBox="0 0 nan 10"/>',
        b'<svg width="10" height="10">' + b"<rect/>" * 10000 + b"</svg>",
    ],
)
def test_svg_type_geometry_and_element_budget_are_validated_before_any_renderer(body):
    with pytest.raises(FeedError) as raised:
        logo_storage.sanitize_svg(body)
    assert raised.value.reason == "invalid_topic_logo"


@pytest.mark.parametrize("failure", ["unsafe-input", "renderer-failure"])
def test_logo_import_cannot_upload_unsafe_or_failed_svg_conversions(monkeypatch, storage, failure):
    import cairosvg

    client, uploaded = storage
    body = (
        b'<svg width="10" height="10"'
        + (b' onload="alert(1)"' if failure == "unsafe-input" else b"")
        + b"/>"
    )
    monkeypatch.setattr(
        logo_storage,
        "fetch_topic_logo",
        lambda url: FetchResult(200, body, url, content_type="image/svg+xml"),
    )
    render = Mock(side_effect=RuntimeError("private renderer path"))
    monkeypatch.setattr(cairosvg, "svg2png", render)
    with pytest.raises(FeedError) as raised:
        logo_storage.LogoOriginalStore(client).store("https://publisher.example/logo.svg")
    assert raised.value.reason == "invalid_topic_logo" and "private" not in str(raised.value)
    assert render.call_count == int(failure == "renderer-failure")
    assert uploaded == {}
    client.put.assert_not_called()


def test_oversized_transformed_logo_is_not_uploaded_to_a_smaller_variant(monkeypatch, storage):
    client, uploaded = storage
    transform = Mock()
    transform.fetch.return_value = picture(65, 32)
    variants = logo_storage.LogoVariantStore(client, transform=transform)
    original = {
        "hash": "a" * 64,
        "original_key": "originals/topic-logos/v1/" + "a" * 64 + ".png",
        "version": "v1",
        "source_url": "https://publisher.example/logo.png",
        "variants": [],
    }
    with pytest.raises(FeedError) as raised:
        variants.store(original, 64)
    assert raised.value.reason == "image_transform"
    assert uploaded == {}
    client.put.assert_not_called()
