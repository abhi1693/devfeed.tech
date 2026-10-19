import uuid
from datetime import UTC, datetime

import pytest
from devfeed_core.models import Article, ArticleOrigin, Source
from devfeed_core.schemas import ArticleOut

NOW = datetime(2026, 10, 19, tzinfo=UTC)


def source(name="Publisher", **overrides):
    values = {
        "id": uuid.uuid4(),
        "name": name,
        "slug": name.lower().replace(" ", "-"),
        "source_type": "publisher",
        "approval_status": "approved",
        "website_url": "https://publisher.example/",
        "feed_url": "https://publisher.example/rss",
    }
    return Source(**{**values, **overrides})


def article(*sources, **overrides):
    values = {
        "id": uuid.uuid4(),
        "slug": "overview-1",
        "canonical_url": "https://publisher.example/article",
        "title": "Original publisher headline",
        "ai_title": "DevFeed overview headline",
        "summary": "Original description",
        "metadata_source_type": "publisher",
        "author": "Editorial Team",
        "content_type": "article",
        "feed_at": NOW,
        "discovered_at": NOW,
        "tags": [],
        "topic_links": [],
        "origins": [
            ArticleOrigin(
                source=current,
                source_id=current.id,
                original_url="https://publisher.example/article",
                source_metadata={"author": "Editorial Team"},
            )
            for current in sources
        ],
    }
    return Article(**{**values, **overrides})


def test_publisher_identity_does_not_depend_on_alphabetical_source_order():
    publisher = source("Z Publisher")
    aggregator = source(
        "A Community", source_type="aggregator", website_url="https://community.example/"
    )
    result = ArticleOut.from_article(article(publisher, aggregator))
    assert result.sources[0].id == aggregator.id
    assert result.publisher and result.publisher.id == publisher.id


@pytest.mark.parametrize("public", [True, False])
def test_original_headline_and_editorial_title_have_distinct_provenance(public):
    current = article(source())
    result = ArticleOut.from_article(current, public=public)
    assert result.original_title == current.title
    assert result.title == (current.ai_title if public else current.title)
    assert result.author == "Editorial Team"
    assert result.authors == []


def test_original_headline_is_available_for_original_publisher_metadata():
    result = ArticleOut.from_article(article(metadata_source_type="publisher"))
    assert result.original_title == "Original publisher headline"


@pytest.mark.parametrize("metadata_source_type", [None, "aggregator", "page"])
def test_submission_headline_is_not_declared_the_original_headline(metadata_source_type):
    result = ArticleOut.from_article(article(metadata_source_type=metadata_source_type))
    assert result.original_title is None
    assert result.title == "DevFeed overview headline"


def test_page_provenance_does_not_prove_that_a_curator_headline_was_replaced():
    result = ArticleOut.from_article(
        article(source(), title="Curator submission headline", metadata_source_type="page")
    )
    assert result.original_title is None


def test_retained_publisher_entry_can_verify_the_original_headline_after_page_enrichment():
    current = article(source(), metadata_source_type="page")
    current.origins[0].source_metadata["title"] = current.title
    assert ArticleOut.from_article(current).original_title == current.title


@pytest.mark.parametrize(
    "publisher",
    [
        source(source_type="aggregator"),
        source(approval_status="pending"),
        source(website_url="https://unrelated.example/"),
    ],
)
def test_only_trusted_matching_publisher_entries_can_verify_the_original_headline(publisher):
    current = article(publisher, metadata_source_type="page")
    current.origins[0].source_metadata["title"] = current.title
    assert ArticleOut.from_article(current).original_title is None


@pytest.mark.parametrize(
    "sources",
    [
        [],
        [source(source_type="aggregator")],
        [source(website_url="https://other.example/")],
        [source(website_url="https://publisher.example.evil.example/")],
        [source(website_url="http://127.0.0.1/")],
        [source("First"), source("Second")],
    ],
)
def test_missing_unrelated_aggregator_and_ambiguous_publishers_remain_unknown(sources):
    assert ArticleOut.from_article(article(*sources)).publisher is None


@pytest.mark.parametrize("public", [True, False])
def test_unapproved_sources_never_become_trusted_publishers(public):
    current = source(approval_status="pending")
    result = ArticleOut.from_article(article(current), public=public)
    assert result.publisher is None
    assert len(result.sources) == (0 if public else 1)


def test_publisher_identity_is_independent_of_headline_provenance():
    publisher = source()
    result = ArticleOut.from_article(article(publisher, metadata_source_type="aggregator"))
    assert result.publisher and result.publisher.id == publisher.id
    assert result.original_title is None


@pytest.mark.parametrize("website_url", [None, "https://www.publisher.example/"])
def test_publisher_uses_website_or_feed_host_without_www(website_url):
    publisher = source(website_url=website_url)
    result = ArticleOut.from_article(article(publisher))
    assert result.publisher and result.publisher.id == publisher.id


@pytest.mark.parametrize("name", ["Jane Writer", "Editorial Team", "Developers Digest", None])
def test_untyped_author_names_are_never_guessed_as_person_or_organization(name):
    result = ArticleOut.from_article(article(source(), author=name))
    assert result.author == name
    assert result.authors == []
