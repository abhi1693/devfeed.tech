"""Public search projection, bounded documents, and suggestion admission policies."""

import hashlib
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import search_records, search_suggestions, topic_logos

ID = uuid.UUID(int=1)
SECOND = uuid.UUID(int=2)
NOW = datetime(2026, 10, 4, tzinfo=UTC)


@pytest.mark.parametrize("kind", search_records.MODELS)
def test_empty_record_requests_perform_no_database_work(kind):
    session = Mock()
    assert search_records.public_records(session, kind, []) == {}
    session.execute.assert_not_called()


@pytest.mark.parametrize("kind", search_records.MODELS)
def test_record_hydration_is_keyed_by_identity_and_returns_plain_values(kind):
    session = Mock()
    session.execute.return_value.mappings.return_value = [{"id": ID, "name": "Python"}]
    assert search_records.public_records(session, kind, [ID]) == {ID: {"id": ID, "name": "Python"}}
    assert session.execute.call_count == 1


@pytest.mark.parametrize("kind", ["articles", "topics", "sources", "tags"])
def test_public_hits_strip_markup_encode_paths_and_preserve_available_metadata(monkeypatch, kind):
    monkeypatch.setattr(
        topic_logos,
        "get_settings",
        lambda: SimpleNamespace(image_public_url="https://images.example"),
    )
    row = {
        "id": ID,
        "name": "C++",
        "slug": "c++/guide",
        "title": "Readable title",
        "description": "<b>Original</b>",
        "ai_description": "<b>Preferred</b>",
        "feed_at": NOW,
        "content_type": "tutorial",
        "logo_url": "https://source.example/logo",
        "managed_logo": {"variants": [{"key": "64.png", "width": 64}]},
    }
    value = search_records.hit(kind, row)
    assert value["id"] == str(ID) and value["title"] == "Readable title"
    assert value["description"] == "Preferred" and value["label"] == "tutorial"
    assert value["href"] == f"/{kind}/c%2B%2B%2Fguide"
    assert value["published_at"] == NOW.isoformat()
    assert value["image_url"] == (
        "https://images.example/64.png" if kind in {"topics", "sources"} else row["logo_url"]
    )


def test_sparse_hits_have_safe_fallbacks_and_bounded_descriptions():
    value = search_records.hit(
        "sources", {"id": ID, "name": "Source", "summary": "<p>" + "x" * 500 + "</p>"}
    )
    assert value["title"] == "Source" and len(value["description"]) == 320
    assert value["href"] == f"/sources/{ID}"
    assert value["published_at"] is None and value["image_url"] is None
    assert value["logo_variants"] == []


def test_article_documents_deduplicate_bound_terms_and_sort_related_identifiers(monkeypatch):
    row = {
        "id": ID,
        "title": "Article",
        "slug": "article",
        "summary": "<b>Summary</b>",
        "aliases": ["Python", "Python", ""],
        "keywords": ["typing"],
        "feed_at": NOW,
        "author": "Reader",
        "content_type": "tutorial",
    }
    monkeypatch.setattr(search_records, "public_records", lambda *_: {ID: row})
    session = Mock()
    session.execute.side_effect = [
        [(ID, SECOND, "Python", "python", ["Py", "Python"])],
        [(ID, SECOND, "Engineering")],
        [(ID, SECOND, "Typing", "typing", ["static typing"])],
    ]
    documents, removed = search_records.documents(session, "articles", [ID, SECOND])
    assert removed == {SECOND}
    assert len(documents) == 1
    value = documents[0]
    assert value["description"] == "Summary"
    assert value["terms"] == [
        "Python",
        "python",
        "Py",
        "Engineering",
        "Typing",
        "typing",
        "static typing",
        "article",
    ]
    assert value["topics"] == value["sources"] == value["tags"] == [str(SECOND)]
    assert value["published_at"] == int(NOW.timestamp())
    assert value["author"] == "Reader" and value["content_type"] == "tutorial"


def test_non_article_documents_are_bounded_and_do_not_request_article_links(monkeypatch):
    monkeypatch.setattr(
        search_records,
        "public_records",
        lambda *_: {
            ID: {
                "id": ID,
                "name": "Topic",
                "description": "d" * 14000,
                "aliases": [f"term-{index}-" + "x" * 250 for index in range(400)],
            }
        },
    )
    session = Mock()
    documents, removed = search_records.documents(session, "topics", [ID])
    value = documents[0]
    assert removed == set() and value["title"] == "Topic"
    assert len(value["description"]) == 12000
    assert len(value["terms"]) == 300 and all(len(term) <= 200 for term in value["terms"])
    assert value["published_at"] == 0 and value["author"] == ""
    assert value["content_type"] == "article"
    assert value["topics"] == value["sources"] == value["tags"] == []
    session.execute.assert_not_called()


def test_absent_documents_return_tombstones_without_loading_relations(monkeypatch):
    monkeypatch.setattr(search_records, "public_records", lambda *_: {})
    session = Mock()
    assert search_records.documents(session, "articles", [ID]) == ([], {ID})
    session.execute.assert_not_called()


@pytest.mark.parametrize(
    "query,expected",
    [
        ("  Python\tAsync ", "python async"),
        ("ＰＹＴＨＯＮ", "python"),
        ("Straße", "strasse"),
        ("x" * 200, "x" * 200),
    ],
)
def test_query_normalization_handles_unicode_and_exact_length_boundary(query, expected):
    assert search_suggestions.normalize_query(query) == expected
    assert search_suggestions.query_hash(expected) == hashlib.sha256(expected.encode()).hexdigest()


@pytest.mark.parametrize("query", ["", " \t ", "!!!", "x" * 201, "word " * 21])
def test_invalid_search_queries_never_write_aggregates(query):
    session = Mock()
    with pytest.raises(ValueError):
        search_suggestions.record_successful_query(session, query)
    session.execute.assert_not_called()


def test_aggregate_write_has_only_normalized_query_and_count_fields():
    session = Mock()
    digest = search_suggestions.record_successful_query(session, "  Ｐython ")
    assert digest == search_suggestions.query_hash("python")
    values = session.execute.call_args.args[0].compile().params
    assert values["query"] == "python" and values["query_hash"] == digest
    assert values["successful_count"] == 1
    assert not {"user_id", "ip", "session_id"}.intersection(values)


@pytest.mark.parametrize("prefix", ["", " \t "])
def test_empty_suggestion_prefix_avoids_database_reads(prefix):
    session = Mock()
    assert search_suggestions.approved_suggestions(session, prefix, limit=5, minimum=2) == []
    session.scalars.assert_not_called()


def test_suggestions_escape_literal_wildcards_and_retain_the_volume_limit():
    session = Mock()
    session.scalars.return_value = ["python_%", "python_guide"]
    assert search_suggestions.approved_suggestions(session, " PYTHON_% ", limit=5, minimum=2) == [
        "python_%",
        "python_guide",
    ]
    values = session.scalars.call_args.args[0].compile().params
    assert "python/_/%" in values.values() and 5 in values.values() and 2 in values.values()


@pytest.mark.parametrize("digest", ["x" * 64, "A" * 64, "a" * 63, "a" * 65])
def test_moderation_rejects_invalid_hashes_before_lookup(digest):
    session = Mock()
    with pytest.raises(ValueError, match="Invalid query hash"):
        search_suggestions.review_query(
            session, digest, status="approved", reviewer="admin", note=None
        )
    session.get.assert_not_called()


def test_moderation_rejects_missing_queries_and_records_review_actor_and_note():
    session = Mock()
    digest = "a" * 64
    session.get.return_value = None
    with pytest.raises(LookupError):
        search_suggestions.review_query(
            session, digest, status="approved", reviewer="admin", note=None
        )
    row = SimpleNamespace()
    session.get.return_value = row
    assert (
        search_suggestions.review_query(
            session, digest, status="rejected", reviewer="admin", note="private"
        )
        is row
    )
    assert row.status == "rejected" and row.reviewed_by == "admin" and row.review_note == "private"
    assert row.reviewed_at.tzinfo == UTC
