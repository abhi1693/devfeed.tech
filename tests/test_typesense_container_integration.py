"""Real Typesense integration coverage for the search projection contract."""

import json
import uuid

import pytest
from devfeed_core.search_engine import SearchRequestError

pytestmark = pytest.mark.integration


def document(identifier, title):
    return {
        "id": str(identifier),
        "title": title,
        "description": title,
        "terms": [title],
        "author": "integration",
        "published_at": 1,
    }


def import_raw(engine, collection, documents):
    payload = b"\n".join(json.dumps(value).encode() for value in documents)
    return engine.request(
        "POST",
        f"/collections/{collection}/documents/import?action=upsert",
        raw=payload,
    )


def test_setup_creates_alias_and_compatible_schema(search_engine):
    engine = search_engine
    physical = engine.physical_collection("articles")

    assert engine.alias_target("articles") == physical
    schema = json.loads(engine.request("GET", f"/collections/{physical}"))
    fields = {field["name"]: field["type"] for field in schema["fields"]}
    assert fields == {
        "title": "string",
        "description": "string",
        "terms": "string[]",
        "author": "string",
        "published_at": "int64",
        "topics": "string[]",
        "sources": "string[]",
        "tags": "string[]",
        "content_type": "string",
    }


def test_typed_article_filters_are_exact_and_composable(search_engine):
    engine = search_engine
    topic_id, source_id, tag_id = (str(uuid.uuid4()) for _ in range(3))
    matching = {
        **document(uuid.uuid4(), "Kubernetes tutorial"),
        "topics": [topic_id],
        "sources": [source_id],
        "tags": [tag_id],
        "content_type": "tutorial",
    }
    excluded = {
        **document(uuid.uuid4(), "Kubernetes tutorial"),
        "topics": [topic_id],
        "sources": [source_id],
        "tags": [str(uuid.uuid4())],
        "content_type": "article",
    }
    engine.sync("articles", [matching, excluded], set())

    result = engine.search(
        "kubernetes",
        ("articles",),
        topics=[topic_id],
        sources=[source_id],
        tags=[tag_id],
        content_types=["tutorial"],
    )
    assert [hit["document"]["id"] for hit in result["articles"]["hits"]] == [matching["id"]]


def test_bulk_upsert_and_delete_by_query_are_real_operations(search_engine):
    engine = search_engine
    first, second = uuid.uuid4(), uuid.uuid4()
    engine.sync(
        "articles",
        [document(first, "Typesense bulk one"), document(second, "Typesense bulk two")],
        set(),
    )
    assert engine.count("articles") == 2

    results = engine.search("bulk two", ("articles",))
    assert results["articles"]["hits"][0]["document"]["id"] == str(second)

    engine.sync("articles", [], {first})
    assert engine.count("articles") == 1


def test_partial_import_failure_returns_per_line_results(search_engine):
    engine = search_engine
    payload = import_raw(
        engine,
        engine.physical_collection("articles"),
        [
            document(uuid.uuid4(), "valid document"),
            {**document(uuid.uuid4(), "invalid document"), "published_at": "not-an-int"},
        ],
    )
    results = [json.loads(line) for line in payload.splitlines()]
    assert len(results) == 2
    assert sum(result["success"] is True for result in results) == 1
    assert sum(result["success"] is False for result in results) == 1


def test_retry_behavior_recovers_against_real_typesense(search_engine, monkeypatch):
    engine = search_engine
    real_request = engine.request
    calls = 0
    monkeypatch.setattr("devfeed_core.search_engine.time.sleep", lambda _: None)

    def flaky_request(method, path, **kwargs):
        nonlocal calls
        if method == "POST" and "/documents/import" in path and calls == 0:
            calls += 1
            raise SearchRequestError("not ready", reason="http_503", status=503)
        calls += 1
        return real_request(method, path, **kwargs)

    monkeypatch.setattr(engine, "request", flaky_request)
    engine.sync("articles", [document(uuid.uuid4(), "retry succeeds")], set())
    assert calls == 2
    assert engine.count("articles") == 1


def test_alias_switching_changes_the_live_collection_without_deleting_old_one(search_engine):
    engine = search_engine
    version = "v2"
    physical = engine.physical_collection("articles", version)
    engine.create_collection("articles", version=version, allowed=())
    try:
        identifier = uuid.uuid4()
        import_raw(engine, physical, [document(identifier, "new alias collection")])
        engine.request(
            "PUT",
            f"/aliases/{engine.alias('articles')}",
            data={"collection_name": physical},
        )

        result = engine.search("new alias collection", ("articles",))
        assert result["articles"]["hits"][0]["document"]["id"] == str(identifier)
        assert engine.count("articles") == 1
        assert json.loads(engine.request("GET", f"/collections/{physical}"))["name"] == physical
    finally:
        engine.request(
            "PUT",
            f"/aliases/{engine.alias('articles')}",
            data={"collection_name": engine.physical_collection("articles")},
        )
        engine.request("DELETE", f"/collections/{physical}", allowed=(404,))


def test_reconciliation_matches_visible_postgres_counts(database, search_engine):
    from devfeed_core.models import Tag
    from devfeed_core.search_index import reconcile
    from test_public_search_integration import drain, seed

    seed(database)
    with database.begin() as session:
        session.add(Tag(name="Unlinked", slug="unlinked"))
    drain(database, search_engine)
    visible = reconcile(database, search_engine)

    assert visible == {"articles": 2, "topics": 1, "sources": 1, "tags": 1}
    assert search_engine.count("articles") == visible["articles"]
    assert search_engine.count("topics") == visible["topics"]
    assert search_engine.count("sources") == visible["sources"]
    assert search_engine.count("tags") == visible["tags"]
