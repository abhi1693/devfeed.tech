"""Explicit edit intent and managed image representations preserve backend contracts."""

import copy
import uuid
from datetime import timedelta
from unittest.mock import Mock

import pytest
from devfeed_core import schemas, services
from devfeed_core.managed_images import ARTICLE_THUMBNAIL_LAYOUT, ARTICLE_THUMBNAIL_WIDTHS
from pydantic import ValidationError
from test_runtime100_published_thumbnail_unit import published
from test_source_readiness_unit import source


def test_explicit_description_clear_is_distinct_from_an_omitted_profile_field():
    current = source(description="An operator-provided publication description.")
    session = Mock()
    session.scalar.return_value = current
    omitted = schemas.SourcePatch()
    cleared = schemas.SourcePatch(description=None)

    assert omitted.model_dump(exclude_unset=True) == {}
    assert cleared.model_dump(exclude_unset=True) == {"description": None}
    assert services.update_source(session, current.id, omitted).description is not None
    assert services.update_source(session, current.id, cleared).description is None
    assert current.feed_url and current.name
    session.add.assert_not_called()


@pytest.mark.parametrize("field", ["name", "slug", "aliases", "auto_link_topic"])
def test_null_tag_identity_and_matching_fields_cannot_silently_mean_erase_or_noop(field):
    with pytest.raises(ValidationError, match="Only topic_id can be null"):
        schemas.TagPatch.model_validate({field: None})
    assert schemas.TagPatch(topic_id=None).model_dump(exclude_unset=True) == {"topic_id": None}


def test_website_only_source_admission_accepts_an_explicit_unresolved_feed_location():
    body = schemas.SourceCreate(
        website_url="https://publisher.example/", feed_url=None, source_type="publisher"
    )
    assert body.website_url == "https://publisher.example/" and body.feed_url is None
    assert body.name is None
    assert body.model_dump(exclude_unset=True) == {
        "website_url": "https://publisher.example/",
        "feed_url": None,
        "source_type": "publisher",
    }


@pytest.mark.parametrize("public", [False, True])
def test_article_dto_uses_the_largest_current_managed_thumbnail_without_rewriting_the_original(
    monkeypatch, public
):
    current = published(monkeypatch, "v2")
    current.slug = "angular-routing"
    current.discovered_at = current.published_to_feed_at - timedelta(hours=1)
    current.feed_at = current.discovered_at
    origin = current.origins[0]
    origin.id, origin.article_id = uuid.uuid4(), current.id
    origin.original_url, origin.entry_key = current.canonical_url, str(current.id)
    origin.created_at = current.discovered_at
    origin.source.slug = "publisher"
    origin.source.created_at = current.discovered_at - timedelta(days=1)
    origin.source_metadata = {"title": current.title, "image_url": current.image_url}
    current.managed_image = {
        **current.managed_image,
        "thumbnail_layout": ARTICLE_THUMBNAIL_LAYOUT,
        "variants": [
            {"key": f"thumbnails/v2/{current.managed_image['hash']}/{width}.webp", "width": width}
            for width in ARTICLE_THUMBNAIL_WIDTHS
        ],
    }
    original = current.image_url
    before = copy.deepcopy(current.managed_image)

    output = schemas.ArticleOut.from_article(current, public=public)

    assert [variant.width for variant in output.image_variants] == list(ARTICLE_THUMBNAIL_WIDTHS)
    assert output.image_url == max(output.image_variants, key=lambda variant: variant.width).url
    assert output.image_url.startswith("https://images.example/")
    assert output.image_url != original
    assert current.image_url == original and current.managed_image == before
    assert current.publication_status == "published"
