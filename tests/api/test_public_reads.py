"""Public read routes preserve identity, publication, and request deadline contracts."""

import uuid
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from devfeed_api import (
    cache,
    dependencies,
    feed,
    latest,
    ordered_feed,
    search,
    sources,
    taxonomy,
    topics,
)
from devfeed_api.latest import diverse_ids
from devfeed_core.analysis import AnalysisResult
from devfeed_core.article_lifecycle import derive_publication_readiness
from devfeed_core.config import get_settings
from devfeed_core.models import Tag, Topic, TopicRelation
from devfeed_core.schemas import ArticleOut, FeedPage, SourcePublicOut, TagPublicOut
from devfeed_core.topics import TopicOut
from devfeed_user_api import dependencies as user_dependencies
from devfeed_user_api import profile
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.admin.articles.test_article_board_policy_unit import history
from tests.support.article_readiness import processing_case as processing_case
from tests.support.reader_context import stories as stories
from tests.support.source_readiness import NOW, configuration, source


def public_topic(**changes):
    return Topic(
        **{
            "id": uuid.uuid4(),
            "name": "Angular",
            "slug": "angular",
            "kind": "framework",
            "status": "active",
            "aliases": ["Angular framework"],
            "keywords": [],
            "description": "An application framework.",
            "ai_description": None,
            "website_url": "https://angular.dev",
            "logo_url": None,
            "managed_logo": {},
            "facts": [],
            **changes,
        }
    )


@contextmanager
def public_api(monkeypatch):
    session = Mock(spec=Session)
    settings = configuration(cache_enabled=False)
    monkeypatch.setattr(cache, "get_settings", lambda: settings)
    app = FastAPI()
    for module in (feed, sources, topics, taxonomy):
        app.include_router(module.router)
    app.dependency_overrides[dependencies.get_session] = lambda: session
    with TestClient(app) as client:
        yield NS(client=client, session=session)


def publisher(**changes):
    return source(
        approval="approved",
        slug="engineering-publisher",
        created_at=NOW - timedelta(days=3),
        **changes,
    )


@pytest.mark.parametrize("lookup", ["slug", "id"])
def test_source_detail_serializes_public_fields_without_internal_review_receipts(
    monkeypatch, lookup
):
    current = publisher()
    current.processing_request = {
        "kind": "upstream_retry",
        "id": str(uuid.uuid4()),
        "target": "profile",
        "requested_at": NOW.isoformat(),
        "required": ["profile", "branding"],
    }
    current.enabled = False
    with public_api(monkeypatch) as api:
        api.session.get.return_value = api.session.scalar.return_value = current
        identifier = str(current.id) if lookup == "id" else current.slug
        response = api.client.get(f"/v1/sources/{identifier}")
        assert response.status_code == 200
        output = response.json()
        assert output["id"] == str(current.id) and output["name"] == current.name
        assert set(output) == set(SourcePublicOut.model_fields)
        assert "processing_request" not in output and "reviewed_by" not in output


@pytest.mark.parametrize("approval", ["pending", "rejected", "missing"])
def test_nonpublic_source_is_not_disclosed_even_when_identity_lookup_returns_a_record(approval):
    session = Mock(spec=Session)
    current = publisher()
    current.approval_status, current.enabled = (
        ("pending" if approval == "pending" else "rejected"),
        False,
    )
    session.get.return_value = None if approval == "missing" else current
    with pytest.raises(HTTPException) as missing:
        sources.source_detail(str(current.id), session)
    assert missing.value.status_code == 404


def test_overlong_source_identity_fails_before_any_read():
    session = Mock(spec=Session)
    with pytest.raises(HTTPException) as missing:
        sources.source_detail("x" * 201, session)
    assert missing.value.status_code == 404 and session.mock_calls == []


@pytest.mark.parametrize("module", [topics, taxonomy])
def test_malformed_stable_taxonomy_identity_does_not_fall_back_to_slug_read(module):
    session = Mock(spec=Session)
    handler = topics.topic if module is topics else taxonomy.tag
    with pytest.raises(HTTPException) as missing:
        handler("not-a-uuid", session, by_id=True)
    assert missing.value.status_code == 404
    session.scalar.assert_not_called()


@pytest.mark.parametrize("lookup", ["slug", "id"])
@pytest.mark.parametrize("kind", ["topic", "tag"])
def test_taxonomy_detail_uses_public_schema_for_slug_and_stable_id(monkeypatch, lookup, kind):
    current = (
        public_topic()
        if kind == "topic"
        else Tag(
            id=uuid.uuid4(),
            name="Angular routing",
            slug="angular-routing",
            aliases=["Router"],
            auto_link_topic=True,
            topic_match_status="matched",
            topic_match_revision=4,
            topic_id=uuid.uuid4(),
        )
    )
    with public_api(monkeypatch) as api:
        api.session.scalar.return_value = current
        identifier = str(current.id) if lookup == "id" else current.slug
        response = api.client.get(
            f"/v1/{'topics' if kind == 'topic' else 'tags'}/{identifier}",
            params={"by_id": lookup == "id"},
        )
        assert response.status_code == 200
        assert set(response.json()) == set(
            TopicOut.model_fields if kind == "topic" else TagPublicOut.model_fields
        )
        assert response.json()["id"] == str(current.id)
        assert "topic_match_revision" not in response.json()


def test_relation_response_keeps_approved_evidence_and_public_peer_shape(monkeypatch):
    focal, peer = (
        public_topic(),
        public_topic(name="TypeScript", slug="typescript", kind="language"),
    )
    link = TopicRelation(
        topic_id=focal.id,
        related_topic_id=peer.id,
        relation="uses_language",
        evidence_url="https://angular.dev/guide/components",
    )
    with public_api(monkeypatch) as api:
        api.session.scalar.return_value = focal
        api.session.execute.return_value.all.return_value = [(link, peer)]
        response = api.client.get(f"/v1/topics/{focal.slug}/relations")
        assert response.status_code == 200
        [row] = response.json()
        assert row["relation"] == "uses_language" and row["evidence_url"] == link.evidence_url
        assert row["topic"]["id"] == str(peer.id)
        assert set(row["topic"]) == set(TopicOut.model_fields)


@pytest.mark.parametrize("filtered", [False, True])
def test_feed_facets_sort_available_types_and_serialize_approved_sources(monkeypatch, filtered):
    monkeypatch.setattr(get_settings(), "image_public_url", "https://images.example.test")
    current = publisher(
        logo_url="https://publisher.example/logo.svg",
        managed_logo={
            "original_key": "sources/original.webp",
            "variants": [
                {"width": 96, "key": "sources/96.webp"},
                {"width": 32, "key": "sources/32.webp"},
                {"width": 64, "key": "sources/64.webp"},
            ],
        },
        review_note="Private editorial explanation",
        submitted_by={"email": "private@example.test"},
    )
    with public_api(monkeypatch) as api:
        if filtered:
            api.session.execute.return_value.one.return_value = (["tutorial", "news"], [current.id])
            api.session.scalars.return_value.all.return_value = [current]
        else:
            api.session.scalars.side_effect = [
                NS(all=lambda: ["tutorial", "news"]),
                NS(all=lambda: [current]),
            ]
        params = (
            {
                "q": "routing",
                "topic": "angular",
                "tag": "router",
                "content_type": "tutorial",
                "source_id": str(current.id),
                "languages": "en",
            }
            if filtered
            else {"content_type": "tutorial", "source_id": str(current.id), "languages": "en"}
        )
        response = api.client.get("/v1/feed/options", params=params)
        assert response.status_code == 200
        assert response.json()["content_types"] == ["news", "tutorial"]
        [output] = response.json()["sources"]
        assert output == SourcePublicOut.model_validate(current).model_dump(mode="json")
        assert set(output) == set(SourcePublicOut.model_fields)
        assert output["logo_url"] == "https://images.example.test/sources/64.webp"
        assert [item["width"] for item in output["logo_variants"]] == [32, 64, 96]
        assert "Private editorial explanation" not in response.text
        assert "private@example.test" not in response.text


@pytest.mark.parametrize("sort", ["oldest", "most_liked"])
def test_ordered_feed_dispatch_preserves_explicit_filters_and_uses_separate_page_service(
    monkeypatch, sort
):
    read = Mock(return_value=FeedPage(items=[], next_cursor=None))
    monkeypatch.setattr(ordered_feed, "ordered_page", read)
    with public_api(monkeypatch) as api:
        response = api.client.get(
            "/v1/feed",
            params={
                "sort": sort,
                "limit": 4,
                "languages": "en",
                "exclude_tag": "ads",
                "content_types": "tutorial",
            },
        )
        assert response.status_code == 200 and response.json() == {"items": [], "next_cursor": None}
        assert read.call_args.args[0] is api.session
        assert read.call_args.args[2]["languages"] == ["en"]
        assert read.call_args.args[2]["exclude_tag"] == ["ads"]
        assert read.call_args.args[3:] == (sort, 4, None)
        api.session.scalars.assert_not_called()


def test_diverse_browsing_dispatch_preserves_only_broad_feed_filters(monkeypatch):
    read = Mock(return_value=FeedPage(items=[], next_cursor=None))
    monkeypatch.setattr(latest, "latest_page", read)
    with public_api(monkeypatch) as api:
        response = api.client.get(
            "/v1/feed",
            params={
                "diverse": "true",
                "limit": 3,
                "content_type": "tutorial",
                "exclude_source": str(uuid.uuid4()),
            },
        )
        assert response.status_code == 200
        assert read.call_args.args[2]["content_type"] == "tutorial"
        assert len(read.call_args.args[2]["exclude_source"]) == 1
        assert read.call_args.args[3:] == (3, None)


def test_feed_page_cursor_refers_to_last_returned_item_and_hides_lookahead(
    monkeypatch, processing_case
):
    stories = []
    for index in range(2):
        current, facts = history(processing_case, f"public-page-{index}")
        link = current.topic_links[0]
        link.origin, link.evidence = "ai", current.summary
        facts["analysis"].result = AnalysisResult(
            developer_relevance="relevant",
            page_kind="article",
            outcome="ready",
            language=current.language,
            content_type=current.content_type,
            content_format=current.content_format,
            topics=[
                {
                    "topic_id": link.topic_id,
                    "role": link.role,
                    "relevance": link.relevance,
                    "evidence": link.evidence,
                }
            ],
            tags=[],
            ai_title=current.ai_title,
            title_evidence=None,
            ai_summary=current.ai_summary,
            ai_description=current.ai_description,
            reasons=[],
        ).model_dump(mode="json")
        assert derive_publication_readiness(current, **facts).stage == "ready"
        current.publication_status = "published"
        current.published_to_feed_at = current.feed_at = NOW - timedelta(seconds=30 * (index + 1))
        current.slug = f"public-page-{index}"
        current.origins[0].source.slug = "engineering-publisher"
        current.origins[0].source_metadata = {}
        stories.append(current)
    with public_api(monkeypatch) as api:
        api.session.scalars.return_value.all.return_value = stories
        response = api.client.get(
            "/v1/feed",
            params={
                "limit": 1,
                "topic": "angular",
                "source_id": str(stories[0].origins[0].source_id),
                "exclude_tag": "ads",
                "content_type": "tutorial",
                "exclude_source": str(uuid.uuid4()),
            },
        )
        assert response.status_code == 200
        output = response.json()
        assert [item["id"] for item in output["items"]] == [str(stories[0].id)]
        assert feed.decode_cursor(output["next_cursor"]) == (stories[0].feed_at, stories[0].id)
        assert "classification_provenance" not in output["items"][0]


def test_invalid_feed_cursor_is_rejected_before_article_read(monkeypatch):
    with public_api(monkeypatch) as api:
        assert api.client.get("/v1/feed", params={"cursor": "malformed"}).status_code == 422
        api.session.scalars.assert_not_called()


@pytest.mark.parametrize("sort", ["name", "articles"])
def test_topic_directory_serializes_active_public_catalog_for_valid_discovery_controls(
    monkeypatch, sort
):
    current = public_topic()
    with public_api(monkeypatch) as api:
        api.session.scalars.return_value.all.return_value = [current]
        response = api.client.get(
            "/v1/topics",
            params={
                "sort": sort,
                "has_articles": "true",
                "languages": "en",
                "q": " Angular ",
                "limit": 1,
                "offset": 0,
            },
        )
        assert response.status_code == 200
        [output] = response.json()
        assert output["id"] == str(current.id) and output["name"] == current.name
        assert set(output) == set(TopicOut.model_fields)
        api.session.commit.assert_not_called()


def test_source_directory_keeps_public_shape_under_language_and_type_controls(monkeypatch):
    current = publisher()
    with public_api(monkeypatch) as api:
        api.session.scalars.return_value.all.return_value = [current]
        response = api.client.get(
            "/v1/sources",
            params={
                "source_type": "publisher",
                "enabled": "true",
                "has_articles": "true",
                "languages": "en",
                "q": current.name,
                "limit": 1,
                "offset": 0,
            },
        )
        assert response.status_code == 200
        assert set(response.json()[0]) == set(SourcePublicOut.model_fields)
        assert response.json()[0]["id"] == str(current.id)


@pytest.mark.parametrize("letter,name", [("A", "Angular routing"), ("#", "#engineering")])
def test_tag_directory_uses_only_public_label_fields_for_letter_browsing(monkeypatch, letter, name):
    current = Tag(
        id=uuid.uuid4(),
        name=name,
        slug="angular-routing" if letter == "A" else "engineering",
        aliases=[],
        auto_link_topic=True,
        topic_match_status="pending",
    )
    with public_api(monkeypatch) as api:
        api.session.scalars.return_value.all.return_value = [current]
        response = api.client.get(
            "/v1/tags", params={"letter": letter, "q": name, "limit": 1, "offset": 0}
        )
        assert response.status_code == 200
        assert response.json() == [
            {"id": str(current.id), "name": name, "slug": current.slug, "aliases": []}
        ]


@pytest.mark.parametrize("kind", ["topics", "tags"])
@pytest.mark.parametrize("by_id", [False, True])
def test_valid_but_unavailable_public_taxonomy_identity_returns_not_found(monkeypatch, kind, by_id):
    identity = str(uuid.uuid4()) if by_id else "removed-framework"
    with public_api(monkeypatch) as api:
        api.session.scalar.return_value = None
        response = api.client.get(f"/v1/{kind}/{identity}", params={"by_id": by_id})
        assert response.status_code == 404
        assert response.json() == {
            "detail": "Topic not found" if kind == "topics" else "Tag not found"
        }
        api.session.scalar.assert_called_once()
        api.session.commit.assert_not_called()


def test_new_recency_band_can_avoid_previous_publisher_without_reordering_that_publishers_stories():
    first, alternative = uuid.uuid4(), uuid.uuid4()
    rows = [
        SimpleNamespace(id=uuid.uuid4(), feed_at=NOW - timedelta(hours=index)) for index in range(8)
    ]
    rows.extend(
        SimpleNamespace(id=uuid.uuid4(), feed_at=NOW - timedelta(hours=index))
        for index in (26, 27, 28)
    )
    publishers = {row.id: first for row in rows}
    publishers[rows[9].id] = alternative
    ordered = diverse_ids(rows, publishers)
    assert ordered == [str(row.id) for row in rows[:8]] + [
        str(rows[index].id) for index in (9, 8, 10)
    ]
    assert [value for value in ordered if publishers[uuid.UUID(value)] == first] == [
        str(row.id) for row in rows if publishers[row.id] == first
    ]


@pytest.fixture
def public_reads(monkeypatch):
    settings, session = configuration(cache_enabled=False), Mock(spec=Session)
    monkeypatch.setattr(cache, "get_settings", lambda: settings)
    app = FastAPI()
    for router in (feed.router, search.router, profile.public_router):
        app.include_router(router)
    app.dependency_overrides[dependencies.get_session] = lambda: session
    app.dependency_overrides[user_dependencies.get_session] = lambda: session
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, session=session)


def test_public_article_slug_lookup_returns_the_same_typed_story_without_private_receipts(
    public_reads, stories
):
    current = stories[0]
    public_reads.session.scalar.return_value = current
    response = public_reads.client.get(f"/v1/articles/{current.slug}")
    assert response.status_code == 200, response.text
    output = response.json()
    assert output["id"] == str(current.id) and output["slug"] == current.slug
    assert set(output) == set(ArticleOut.model_fields)
    assert "editorial_revision" not in output and "analysis_input_hash" not in output
    public_reads.session.scalar.assert_called_once()
    public_reads.session.commit.assert_not_called()


def test_whitespace_public_profile_identity_is_not_a_database_or_cache_lookup(public_reads):
    response = public_reads.client.get("/v1/user/profiles/%20")
    assert response.status_code == 404 and response.json() == {"detail": "Profile not found"}
    assert public_reads.session.mock_calls == []


@pytest.mark.parametrize("elapsed", [3.0, 3.25])
def test_index_consuming_the_search_deadline_never_starts_database_hydration(
    public_reads, monkeypatch, elapsed
):
    clock = SimpleNamespace(now=0.0)
    # Patch this module's clock reference, preserving the event loop/HTTP clocks.
    monkeypatch.setattr(search, "time", SimpleNamespace(monotonic=lambda: clock.now))
    engine = Mock()

    def index_search(*_, **__):
        clock.now = elapsed
        return {"articles": {"hits": [], "found": 0}}

    engine.search.side_effect = index_search
    monkeypatch.setattr(search, "Typesense", Mock(return_value=engine))
    monkeypatch.setattr(search, "click_tokens", Mock(return_value=None))
    hydrate = Mock()
    monkeypatch.setattr(search, "public_records", hydrate)
    response = public_reads.client.get("/v1/search", params={"q": "Angular", "section": "articles"})
    assert response.status_code == 503
    assert response.json() == {"detail": "Search is temporarily unavailable. Please try again."}
    engine.search.assert_called_once()
    hydrate.assert_not_called()
    assert public_reads.session.mock_calls == []
