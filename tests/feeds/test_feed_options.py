import uuid

import pytest
from devfeed_core import cache
from devfeed_core.config import get_settings
from devfeed_core.models import Article, ArticleOrigin, ArticleTag, ArticleTopic, Source, Tag, Topic
from devfeed_core.schemas import SourcePublicOut
from sqlalchemy import delete, event, inspect
from sqlalchemy.exc import InvalidRequestError

pytestmark = pytest.mark.integration


@pytest.fixture
def cached_options(database, monkeypatch):
    monkeypatch.setattr(get_settings(), "cache_enabled", True)
    cache.close_cache()
    yield
    cache.close_cache()


def source(name, **values):
    return Source(
        name=name,
        feed_url=f"https://example.test/{uuid.uuid4()}/rss",
        source_type="publisher",
        **{"approval_status": "approved", "enabled": True, **values},
    )


def article(session, publisher, **values):
    url = f"https://example.test/{uuid.uuid4()}"
    current = Article(
        canonical_url=url,
        url_hash=uuid.uuid4().hex,
        **{
            "title": "Python engineering",
            "summary": "Python engineering news",
            "language": "en",
            "content_type": "news",
            "publication_status": "published",
            "review_status": "approved",
            **values,
        },
    )
    session.add(current)
    session.flush()
    if publisher is not None:
        session.add(
            ArticleOrigin(
                article_id=current.id,
                source_id=publisher.id,
                original_url=url,
                entry_key=uuid.uuid4().hex,
            )
        )
    return current


def test_options_use_all_visible_records_and_filter_each_facet_independently(client, database):
    with database.begin() as session:
        sources = [
            Source(
                name=name,
                feed_url=f"https://example.com/{name}",
                source_type="publisher",
                approval_status="approved",
                enabled=True,
            )
            for name in ["Python", "Rust", "Empty"]
        ]
        session.add_all(sources)
        session.flush()
        ids = [str(source.id) for source in sources]
        for title, kind, language, source, status in [
            ("Python news", "news", "en", sources[0], "published"),
            ("Python guide", "tutorial", "fr", sources[0], "published"),
            ("Rust release", "release", "en", sources[1], "published"),
            ("Hidden opinion", "opinion", "ja", sources[2], "unpublished"),
        ]:
            url = f"https://example.com/{uuid.uuid4()}"
            article = Article(
                canonical_url=url,
                url_hash=uuid.uuid4().hex,
                title=title,
                summary=title,
                content_type=kind,
                language=language,
                publication_status=status,
                review_status="approved",
            )
            session.add(article)
            session.flush()
            session.add(
                ArticleOrigin(
                    article_id=article.id, source_id=source.id, original_url=url, entry_key=url
                )
            )
    result = client.get("/v1/feed/options").json()
    assert set(result["content_types"]) == {"news", "tutorial", "release"}
    assert {source["id"] for source in result["sources"]} == set(ids[:2])
    result = client.get("/v1/feed/options", params={"q": "Python", "content_type": "news"}).json()
    assert set(result["content_types"]) == {"news", "tutorial"}
    assert [source["id"] for source in result["sources"]] == [ids[0]]
    result = client.get("/v1/feed/options", params={"source_id": ids[1]}).json()
    assert result["content_types"] == ["release"]
    assert len(result["sources"]) == 2
    assert client.get("/v1/feed/options?q=unmatched").json() == {
        "content_types": [],
        "sources": [],
    }
    assert client.get("/v1/feed/options?topic=missing").json() == {
        "content_types": [],
        "sources": [],
    }


def test_language_filter_is_only_a_preference_list_in_api_schema(client):
    paths = client.get("/openapi.json").json()["paths"]
    for path in ("/v1/feed", "/v1/feed/options"):
        names = {parameter["name"] for parameter in paths[path]["get"]["parameters"]}
        assert "language" not in names
        assert "languages" in names


def test_source_facet_uses_only_approved_enabled_origins_with_shared_articles(client, database):
    with database.begin() as session:
        sources = [
            Source(
                name=f"Source {index}",
                feed_url=f"https://example.test/{index}/rss",
                source_type="publisher",
                approval_status="rejected" if index == 1 else "approved",
                enabled=index != 2,
            )
            for index in range(4)
        ]
        session.add_all(sources)
        article = Article(
            title="Shared article",
            canonical_url="https://example.test/shared",
            url_hash=uuid.uuid4().hex,
            language="en",
            content_type="article",
            review_status="approved",
            publication_status="published",
        )
        session.add(article)
        session.flush()
        for index, source in enumerate(sources):
            session.add(
                ArticleOrigin(
                    article_id=article.id,
                    source_id=source.id,
                    entry_key=str(index),
                    original_url=article.canonical_url,
                )
            )
        # Several publisher entries may resolve to one canonical article.
        session.add(
            ArticleOrigin(
                article_id=article.id,
                source_id=sources[0].id,
                entry_key="duplicate-entry",
                original_url=article.canonical_url,
            )
        )
        allowed = [str(sources[index].id) for index in (0, 3)]
    for params in (
        {"languages": "en"},
        {"languages": "en", "content_type": "article"},
        {"languages": "en", "q": "Shared"},
    ):
        response = client.get("/v1/feed/options", params=params)
        assert response.status_code == 200
        assert [item["id"] for item in response.json()["sources"]] == allowed
    assert client.get("/v1/feed/options", params={"languages": "fr"}).json()["sources"] == []


@pytest.mark.parametrize("filtered", [False, True], ids=["browse", "search"])
def test_source_facet_requires_an_eligible_article_from_each_public_source(
    client, database, filtered
):
    with database.begin() as session:
        publishers = {
            "English": source("English"),
            "French": source("French"),
            "Mixed": source("Mixed"),
            "Unpublished": source("Unpublished"),
            "Pending article": source("Pending article"),
            "Rejected article": source("Rejected article"),
            "Pending source": source("Pending source", approval_status="pending"),
            "Rejected source": source("Rejected source", approval_status="rejected"),
            "Disabled": source("Disabled", enabled=False),
            "Empty": source("Empty"),
        }
        session.add_all(publishers.values())
        session.flush()
        for name, publisher in publishers.items():
            if name == "Empty":
                continue
            values = {}
            if name == "French":
                values["language"] = "fr"
            elif name in {"Unpublished", "Mixed", "Pending article", "Rejected article"}:
                values["publication_status"] = "unpublished"
            if name in {"Pending article", "Rejected article"}:
                values["review_status"] = name.split()[0].lower()
            article(session, publisher, **values)
        article(session, publishers["Mixed"], language="fr")
        # Public status alone cannot make an article without any origin visible.
        article(session, None, content_type="opinion")
    common = {"q": "Python"} if filtered else {}
    for languages, expected in (
        (None, ["English", "French", "Mixed"]),
        (["en"], ["English"]),
        (["fr"], ["French", "Mixed"]),
        (["en", "fr"], ["English", "French", "Mixed"]),
        (["ja"], []),
    ):
        params = common | ({"languages": languages} if languages else {})
        response = client.get("/v1/feed/options", params=params)
        assert response.status_code == 200, response.text
        assert [item["name"] for item in response.json()["sources"]] == expected
        assert "opinion" not in response.json()["content_types"]


@pytest.mark.parametrize("topic_identity", ["slug", "uuid"])
def test_source_facet_combines_search_tag_topic_and_language_while_omitting_only_source_selection(
    client, database, topic_identity
):
    with database.begin() as session:
        publishers = {name: source(name) for name in ("Alpha", "Beta", "Other")}
        tag = Tag(name="Python", slug="python")
        topic = Topic(name="Python", slug="python", kind="technology", status="active")
        session.add_all([*publishers.values(), tag, topic])
        session.flush()
        # Each decoy misses exactly one of the requested common predicates.
        for name, kind, language, title, tagged, role in (
            ("Alpha", "news", "en", "Python engineering", True, "primary"),
            ("Alpha", "tutorial", "en", "Python guide", True, "supporting"),
            ("Beta", "news", "en", "Python release", True, "primary"),
            ("Other", "release", "en", "Rust release", True, "primary"),
            ("Other", "release", "en", "Python release", False, "primary"),
            ("Other", "release", "en", "Python release", True, "comparison"),
            ("Other", "release", "fr", "Python release", True, "primary"),
        ):
            current = article(
                session,
                publishers[name],
                content_type=kind,
                language=language,
                title=title,
                summary=title,
            )
            session.add(
                ArticleTopic(
                    article_id=current.id,
                    topic_id=topic.id,
                    role=role,
                    relevance=1,
                    evidence="Explicit test classification",
                )
            )
            if tagged:
                session.add(ArticleTag(article_id=current.id, tag_id=tag.id, origin="source"))
        alpha_id = str(publishers["Alpha"].id)
        topic_filter = topic.slug if topic_identity == "slug" else str(topic.id)
        topic_id = topic.id
    common = {"q": "Python", "tag": ["python"], "topic": topic_filter, "languages": ["en"]}
    response = client.get(
        "/v1/feed/options", params=common | {"content_type": "news", "source_id": alpha_id}
    )
    assert response.status_code == 200, response.text
    assert response.json()["content_types"] == ["news", "tutorial"]
    assert [item["name"] for item in response.json()["sources"]] == ["Alpha", "Beta"]
    result = client.get(
        "/v1/feed/options", params=common | {"content_type": "tutorial", "source_id": alpha_id}
    ).json()
    assert result["content_types"] == ["news", "tutorial"]
    assert [item["name"] for item in result["sources"]] == ["Alpha"]
    with database.begin() as session:
        session.get(Topic, topic_id).status = "proposed"
    assert client.get("/v1/feed/options", params=common).json() == {
        "content_types": [],
        "sources": [],
    }


@pytest.mark.parametrize("filtered", [False, True], ids=["browse", "search"])
def test_source_facet_deduplicates_before_the_stable_name_and_id_limit(client, database, filtered):
    with database.begin() as session:
        # Reverse insertion and tied names expose missing or late ordering/limits.
        publishers = [source("Same name", id=uuid.UUID(int=index)) for index in range(510, 0, -1)]
        session.add_all(publishers)
        session.flush()
        shared = article(session, publishers[0])
        session.add_all(
            ArticleOrigin(
                article_id=shared.id,
                source_id=publisher.id,
                original_url=shared.canonical_url,
                entry_key=f"shared-{index}",
            )
            for index, publisher in enumerate(publishers)
        )
        first_name = source("AAA first", id=uuid.UUID(int=1000))
        session.add(first_name)
        session.flush()
        article(session, first_name)
    params = {"q": "Python"} if filtered else {}
    response = client.get("/v1/feed/options", params=params)
    assert response.status_code == 200, response.text
    expected = [str(uuid.UUID(int=1000)), *[str(uuid.UUID(int=i)) for i in range(1, 500)]]
    assert [item["id"] for item in response.json()["sources"]] == expected
    assert [
        item["id"] for item in client.get("/v1/feed/options", params=params).json()["sources"]
    ] == expected


@pytest.mark.parametrize(
    "change", ["unpublish", "reject", "disable", "reject-source", "remove-origin", "delete"]
)
def test_source_facet_tracks_committed_eligibility_and_removal_changes(
    client, database, cached_options, change
):
    with database.begin() as session:
        publisher = source("Mutable")
        session.add(publisher)
        session.flush()
        current = article(session, publisher)
        source_id, article_id = publisher.id, current.id
    requests = ({"languages": "en"}, {"languages": "en", "q": "Python"})
    for params in requests:
        response = client.get("/v1/feed/options", params=params)
        assert response.headers["x-cache"] == "MISS"
        assert [item["id"] for item in response.json()["sources"]] == [str(source_id)]
        assert client.get("/v1/feed/options", params=params).headers["x-cache"] == "HIT"
    with database.begin() as session:
        if change in {"unpublish", "reject"}:
            current = session.get(Article, article_id)
            current.publication_status = "unpublished"
            if change == "reject":
                current.review_status = "rejected"
        elif change == "disable":
            session.get(Source, source_id).enabled = False
        elif change == "reject-source":
            session.get(Source, source_id).approval_status = "rejected"
        elif change == "remove-origin":
            session.execute(delete(ArticleOrigin).where(ArticleOrigin.article_id == article_id))
        else:
            session.execute(delete(Article).where(Article.id == article_id))
    for params in requests:
        response = client.get("/v1/feed/options", params=params)
        assert response.headers["x-cache"] == "MISS"
        assert response.json()["sources"] == []


def test_source_facet_cache_isolates_supported_filters_and_authorized_requests(
    client, database, cached_options
):
    with database.begin() as session:
        publishers = [source("Alpha"), source("Beta")]
        tag = Tag(name="Alpha", slug="alpha")
        topic = Topic(name="Beta", slug="beta", kind="technology", status="active")
        session.add_all([*publishers, tag, topic])
        session.flush()
        alpha = article(session, publishers[0], title="Alpha engineering", summary="Alpha news")
        beta = article(
            session,
            publishers[1],
            title="Beta engineering",
            summary="Beta tutorials",
            language="fr",
            content_type="tutorial",
        )
        session.add(ArticleTag(article_id=alpha.id, tag_id=tag.id, origin="source"))
        session.add(
            ArticleTopic(
                article_id=beta.id,
                topic_id=topic.id,
                role="primary",
                relevance=1,
                evidence="Explicit test classification",
            )
        )
        alpha_id = str(publishers[0].id)
    for params, expected_sources, expected_types in (
        ({}, ["Alpha", "Beta"], ["news", "tutorial"]),
        ({"languages": ["en"]}, ["Alpha"], ["news"]),
        ({"languages": ["fr"]}, ["Beta"], ["tutorial"]),
        ({"q": "Alpha"}, ["Alpha"], ["news"]),
        ({"q": "Beta"}, ["Beta"], ["tutorial"]),
        ({"tag": ["alpha"]}, ["Alpha"], ["news"]),
        ({"topic": "beta"}, ["Beta"], ["tutorial"]),
        ({"content_type": "news"}, ["Alpha"], ["news", "tutorial"]),
        ({"content_type": "tutorial"}, ["Beta"], ["news", "tutorial"]),
        ({"source_id": alpha_id}, ["Alpha", "Beta"], ["news"]),
        ({"q": "Beta", "languages": ["en"]}, [], []),
        ({"tag": ["alpha"], "topic": "beta"}, [], []),
    ):
        response = client.get("/v1/feed/options", params=params)
        assert response.status_code == 200, response.text
        assert response.headers["x-cache"] == "MISS"
        assert [item["name"] for item in response.json()["sources"]] == expected_sources
        assert response.json()["content_types"] == expected_types
        repeated = client.get("/v1/feed/options", params=params)
        assert repeated.headers["x-cache"] == "HIT"
        assert repeated.json() == response.json()
        private = client.get(
            "/v1/feed/options", params=params, headers={"Authorization": "Bearer reader"}
        )
        assert private.headers["x-cache"] == "BYPASS"
        assert private.headers["x-cache-bypass-reason"] == "authorization"
        assert private.json() == response.json()


@pytest.mark.parametrize("filtered", [False, True], ids=["browse", "search"])
def test_source_facet_loads_public_branding_without_private_columns_or_lazy_reads(
    client, database, monkeypatch, filtered
):
    monkeypatch.setattr(get_settings(), "image_public_url", "https://images.example.test")
    with database.begin() as session:
        publisher = source(
            "Public publisher",
            description="Engineering articles",
            website_url="https://publisher.example.test",
            logo_url="https://publisher.example.test/logo.png",
            image_url="https://publisher.example.test/cover.png",
            language="en",
            managed_logo={
                "original_key": "sources/original.webp",
                "variants": [
                    {"width": 96, "key": "sources/96.webp"},
                    {"width": 32, "key": "sources/32.webp"},
                    {"width": 64, "key": "sources/64.webp"},
                ],
            },
            review_note="Private editorial explanation",
            reviewed_by="private-operator",
            submitted_by={"email": "private@example.test"},
            relevance_assessment={"internal": "private analysis" * 1000},
            polling_state={"internal": "private polling" * 1000},
        )
        session.add(publisher)
        session.flush()
        article(session, publisher)
        expected = SourcePublicOut.model_validate(publisher).model_dump(mode="json")
    loaded = []

    def capture(instance, _context):
        loaded.append(instance)

    event.listen(Source, "load", capture)
    try:
        response = client.get("/v1/feed/options", params={"q": "Python"} if filtered else {})
    finally:
        event.remove(Source, "load", capture)
    assert response.status_code == 200, response.text
    assert response.json()["sources"] == [expected]
    assert expected["logo_url"] == "https://images.example.test/sources/64.webp"
    assert len(loaded) == 1
    assert {
        "review_note",
        "reviewed_by",
        "submitted_by",
        "relevance_assessment",
        "polling_state",
    } <= inspect(loaded[0]).unloaded
    # The request has closed its session; attach the projection so this checks
    # forbidden lazy SQL rather than the unrelated detached-instance guard.
    with database() as session:
        session.add(loaded[0])
        with pytest.raises(InvalidRequestError, match="raiseload"):
            _ = loaded[0].review_note
