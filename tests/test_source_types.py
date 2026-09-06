import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from devfeed_aggregator import tasks
from devfeed_api.feed import decode_cursor, encode_cursor
from devfeed_cli.main import run
from devfeed_core import services
from devfeed_core.feeds.parser import ParsedFeed, parse_feed
from devfeed_core.models import Article, ArticleOrigin, Source
from devfeed_core.schemas import ArticleOut, SourceCreate, SourcePatch
from devfeed_core.source_types import SourceType
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

NOW = datetime(2026, 9, 6, tzinfo=UTC)
SUBMITTED = datetime(2026, 9, 5, 8, tzinfo=UTC)
# Deliberately no real source hostname or name: the selected semantics drive behavior.
DISCOVERY_FEED = b"""<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
<channel><title>Community links</title><language>en</language>
<item><guid isPermaLink="false">submission-17</guid><title>Rust memory</title>
<link>https://publisher.example/rust?utm_source=community</link>
<description>Article URL: https://publisher.example/rust Comments URL:
https://community.example/item/17 Points: 42 # Comments: 8</description>
<comments>https://community.example/item/17</comments><author>someone-sharing</author>
<pubDate>Sat, 05 Sep 2026 08:00:00 GMT</pubDate><category>rust</category>
<media:thumbnail url="https://community.example/thumbnail.jpg"/></item>
<item><guid>native-1</guid><title>Ask: your setup?</title>
<link>https://community.example/discussions/native-1</link><description>Comments</description>
</item></channel></rss>"""


@pytest.mark.parametrize("source_type", list(SourceType))
def test_source_type_is_explicit_and_survives_validation(source_type):
    source = SourceCreate(
        name="Example", feed_url="https://example.com/rss", source_type=source_type
    )
    assert source.source_type == source_type
    assert source.model_dump(mode="json")["source_type"] == source_type.value


@pytest.mark.parametrize("extra", [{}, {"source_type": None}, {"source_type": "hackernews"}])
def test_missing_null_and_unknown_source_types_are_rejected(extra):
    with pytest.raises(ValidationError):
        SourceCreate.model_validate(
            {"name": "Example", "feed_url": "https://example.com/rss", **extra}
        )


def test_source_type_has_no_model_or_database_default():
    column = Source.__table__.c.source_type
    assert not column.nullable
    assert column.default is None and column.server_default is None
    assert SourceCreate.model_fields["source_type"].is_required()


def test_type_cannot_be_silently_changed_without_reprocessing():
    with pytest.raises(ValidationError):
        SourcePatch.model_validate({"source_type": "aggregator"})


@pytest.mark.parametrize("action,target", [("add", "https://example.com/rss"), ("import", "-")])
@pytest.mark.parametrize("flags", [[], ["--type", "unknown"]])
def test_cli_requires_a_known_type_before_preflight(action, target, flags, monkeypatch):
    monkeypatch.setattr(services, "validate_source", lambda *_: pytest.fail("Fetched a source"))
    with pytest.raises(SystemExit) as caught:
        run(["sources", action, target, *flags])
    assert caught.value.code == 2


def test_aggregator_preserves_submission_evidence_without_polluting_article_metadata():
    parsed = parse_feed(
        DISCOVERY_FEED, "https://community.example/rss", NOW, source_type="aggregator"
    )
    assert (parsed.seen, parsed.skipped) == (2, 0)
    entry = parsed.entries[0]
    assert entry.canonical_url == "https://publisher.example/rust"
    assert entry.source_type == SourceType.AGGREGATOR
    assert entry.summary == ""
    assert entry.author is entry.published_at is entry.image_url is entry.language is None
    assert entry.feed_at == SUBMITTED
    assert entry.tags == ["rust"]
    metadata = entry.source_metadata
    assert metadata["submitter"] == "someone-sharing"
    assert metadata["submitted_at"] == SUBMITTED.isoformat()
    assert metadata["discussion_url"] == "https://community.example/item/17"
    assert "Points: 42" in metadata["description"]
    assert metadata["tags"] == ["rust"]
    assert "author" not in metadata and "published_at" not in metadata
    assert metadata["updated_at"] is None
    # Native discussions remain eligible; hostname equality is not a publisher signal.
    assert parsed.entries[1].canonical_url == "https://community.example/discussions/native-1"
    assert parsed.entries[1].summary == ""


def test_publisher_uses_publication_metadata(rss_bytes):
    entry = parse_feed(rss_bytes, "https://any.example/rss", NOW, source_type="publisher").entries[
        0
    ]
    assert entry.author == "Example Author"
    assert entry.published_at == datetime(2026, 9, 4, 8, tzinfo=UTC)
    assert entry.source_metadata["author"] == entry.author
    assert "submitter" not in entry.source_metadata
    assert entry.summary == "A practical PostgreSQL tutorial."
    assert entry.image_url == "https://example.com/cover.jpg"
    assert entry.language is None
    assert entry.source_metadata["language"] == "en"


@pytest.mark.parametrize("source_type", list(SourceType))
def test_updated_only_is_not_a_publication_or_submission_date(source_type):
    body = b"""<feed xmlns="http://www.w3.org/2005/Atom"><title>Example</title>
    <entry><id>urn:test:1</id><title>Changed</title><link href="/changed"/>
    <updated>2026-09-05T08:00:00Z</updated></entry></feed>"""
    entry = parse_feed(body, "https://example.com/feed", NOW, source_type=source_type).entries[0]
    assert entry.published_at is None
    assert entry.feed_at == SUBMITTED
    assert entry.source_metadata["updated_at"] == SUBMITTED.isoformat()
    date_key = "published_at" if source_type == SourceType.PUBLISHER else "submitted_at"
    assert entry.source_metadata[date_key] is None


def test_unsafe_discussion_link_is_ignored_without_losing_entry():
    body = DISCOVERY_FEED.replace(
        b"<comments>https://community.example/item/17", b"<comments>http://127.0.0.1/"
    )
    entries = parse_feed(
        body, "https://community.example/rss", NOW, source_type="aggregator"
    ).entries
    assert len(entries) == 2
    assert entries[0].source_metadata["discussion_url"] is None


def test_cursor_does_not_depend_on_unknown_or_corrected_publication_date():
    article = Article(id=uuid.uuid4(), feed_at=NOW, published_at=None)
    cursor = encode_cursor(article)
    assert decode_cursor(cursor) == (NOW, article.id)
    article.published_at = datetime(2014, 2, 4, tzinfo=UTC)
    assert encode_cursor(article) == cursor


def test_article_response_exposes_submission_evidence_separately():
    entry = parse_feed(
        DISCOVERY_FEED, "https://community.example/rss", NOW, source_type="aggregator"
    ).entries[0]
    source = Source(id=uuid.uuid4(), name="Community", source_type="aggregator")
    article = Article(
        id=uuid.uuid4(),
        canonical_url=entry.canonical_url,
        title=entry.title,
        summary="",
        content_type="article",
        feed_at=entry.feed_at,
        discovered_at=NOW,
        origins=[
            ArticleOrigin(
                source=source,
                source_id=source.id,
                original_url=entry.original_url,
                source_metadata=entry.source_metadata,
            )
        ],
    )
    payload = ArticleOut.from_article(article).model_dump(mode="json")
    assert payload["published_at"] is None and payload["author"] is None
    assert payload["sources"][0]["source_type"] == "aggregator"
    assert payload["origins"][0]["source_metadata"]["submitter"] == "someone-sharing"
    assert payload["origins"][0]["source_metadata"]["submitted_at"] == SUBMITTED.isoformat()


@pytest.mark.parametrize("source_type", list(SourceType))
def test_persistence_separates_metadata_and_limits_publisher_promotion(
    source_type, rss_bytes, monkeypatch
):
    entry = parse_feed(rss_bytes, "https://example.com/rss", NOW, source_type=source_type).entries[
        0
    ]
    article_id, source_id = uuid.uuid4(), uuid.uuid4()
    image_requests = []
    article_requests = []
    monkeypatch.setattr(
        tasks,
        "request_image",
        lambda session, identifier, **kwargs: image_requests.append((identifier, kwargs)),
    )
    monkeypatch.setattr(
        tasks,
        "request_article_enrichment",
        lambda session, identifier, **kwargs: article_requests.append((identifier, kwargs)),
    )
    # Simulate an existing URL, but no origin for this source. No database access.
    values = iter([None, None, article_id, None])
    scalar_statements, writes = [], []

    def scalar(statement):
        scalar_statements.append(statement)
        return next(values)

    session = SimpleNamespace(
        scalar=scalar, scalars=lambda _: SimpleNamespace(all=lambda: []), execute=writes.append
    )
    assert tasks.store_entries(session, source_id, ParsedFeed([entry], 1, 0)) == 0
    expected = [(article_id, {"automatic": True})]
    assert image_requests == (expected if source_type == SourceType.PUBLISHER else [])
    assert article_requests == (expected if source_type == SourceType.AGGREGATOR else [])
    article_values = scalar_statements[1].compile(dialect=postgresql.dialect()).params
    assert article_values["metadata_source_type"] == source_type
    assert article_values["published_at"] == entry.published_at
    assert article_values["summary"] == entry.summary
    promotions = [statement for statement in writes if statement.is_update]
    assert len(promotions) == int(source_type == SourceType.PUBLISHER)
    if promotions:
        compiled = promotions[0].compile(dialect=postgresql.dialect())
        assert "articles.metadata_source_type IN" in str(compiled)
        assert "image_url=coalesce(articles.image_url," in str(compiled)
        assert [SourceType.AGGREGATOR, "page"] in compiled.params.values()
        assert "feed_at" not in compiled.params and "canonical_url" not in compiled.params
    origin_values = writes[-1].compile(dialect=postgresql.dialect()).params
    assert origin_values["source_id"] == source_id
    assert origin_values["article_id"] == article_id
    assert origin_values["source_metadata"] == entry.source_metadata


def test_duplicate_submission_with_conflicting_type_does_not_queue_a_job(monkeypatch):
    body = services.ValidatedSource(
        "Example", "https://example.com/rss", SourceType.AGGREGATOR, True, 1800
    )
    responses = iter([None, SimpleNamespace(source_type="publisher")])
    session = SimpleNamespace(scalar=lambda *_: next(responses))
    monkeypatch.setattr(services, "request_ingestion", lambda *_: pytest.fail("Queued a job"))
    with pytest.raises(services.OperationConflict, match="different source type"):
        services.submit_source(session, body)
