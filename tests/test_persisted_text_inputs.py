"""Reject PostgreSQL-incompatible text at input boundaries without filtering content."""

import json
import logging
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from devfeed_admin_api import source_imports, topic_proposals, user_settings
from devfeed_admin_api.articles import AdminArticleUpdate
from devfeed_admin_api.auth import require_admin
from devfeed_admin_api.dependencies import get_session
from devfeed_admin_api.search_suggestions import SearchSuggestionReview
from devfeed_admin_api.source_imports import SourceImportRequest
from devfeed_core.discovery_import import parse_import, publisher_hint
from devfeed_core.schemas import (
    DatabaseText,
    Description,
    Keyword,
    Name,
    ReviewNote,
    SourceCreate,
    SourceDecision,
    SourcePatch,
    SourceProfileInput,
    SourceSubmission,
    SourceSubmitter,
    TagPatch,
    TagWrite,
    TaxonomyName,
    TextInput,
)
from devfeed_core.topics import TopicFact, TopicWrite
from devfeed_core.user_settings import TableSettings, TableSettingsPatch
from devfeed_http.errors import register_error_handlers
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

SOURCE = {"feed_url": "https://publisher.example/feed", "source_type": "publisher"}
TOPIC = {"name": "Python", "slug": "python", "kind": "language"}


@pytest.mark.parametrize("value", ["\x00text", "before\x00after", "text\x00"])
@pytest.mark.parametrize(
    "model,payload,location",
    [
        (SourceCreate, {**SOURCE, "name": "VALUE"}, ("name",)),
        (SourceSubmission, {**SOURCE, "name": "VALUE"}, ("name",)),
        (SourcePatch, {"name": "VALUE"}, ("name",)),
        (SourceSubmitter, {"name": "VALUE"}, ("name",)),
        (
            SourceCreate,
            {**SOURCE, "submitted_by": {"name": "VALUE"}},
            ("submitted_by", "name"),
        ),
        (SourceProfileInput, {"description": "VALUE"}, ("description",)),
        (SourceDecision, {"decision": "approved", "actor": "VALUE"}, ("actor",)),
        (SourceDecision, {"decision": "rejected", "note": "VALUE"}, ("note",)),
        (TagWrite, {"name": "VALUE", "slug": "python"}, ("name",)),
        (TagWrite, {"name": "Python", "slug": "python", "aliases": ["VALUE"]}, ("aliases", 0)),
        (TagPatch, {"name": "VALUE"}, ("name",)),
        (TagPatch, {"aliases": ["VALUE"]}, ("aliases", 0)),
        (TopicWrite, {**TOPIC, "name": "VALUE"}, ("name",)),
        (TopicWrite, {**TOPIC, "aliases": ["VALUE"]}, ("aliases", 0)),
        (TopicWrite, {**TOPIC, "keywords": ["VALUE"]}, ("keywords", 0)),
        (
            TopicFact,
            {
                "name": "VALUE",
                "value": "Python is a programming language.",
                "source_url": "https://python.org/about/",
                "retrieved_at": datetime(2026, 10, 5, tzinfo=UTC),
            },
            ("name",),
        ),
        (SearchSuggestionReview, {"status": "approved", "note": "VALUE"}, ("note",)),
        (AdminArticleUpdate, {"title": "VALUE", "expected_revision": 0}, ("title",)),
        (
            AdminArticleUpdate,
            {"title": "Python", "summary": "VALUE", "expected_revision": 0},
            ("summary",),
        ),
        (
            TopicFact,
            {
                "name": "Language",
                "value": "VALUE",
                "source_url": "https://python.org/about/",
                "retrieved_at": datetime(2026, 10, 5, tzinfo=UTC),
            },
            ("value",),
        ),
        (
            SourceImportRequest,
            {"format": "urls", "content": "https://publisher.example/", "name": "VALUE"},
            ("name",),
        ),
        (TableSettingsPatch, {"query": {"q": "VALUE"}}, ("query", "q")),
    ],
)
def test_models_reject_nul_before_persisting_text(model, payload, location, value):
    def replace_value(item):
        if isinstance(item, dict):
            return {key: replace_value(nested) for key, nested in item.items()}
        if isinstance(item, list):
            return [replace_value(nested) for nested in item]
        return value if item == "VALUE" else item

    with pytest.raises(ValidationError) as caught:
        model.model_validate(replace_value(payload))
    assert [error["loc"] for error in caught.value.errors()] == [location]
    assert caught.value.errors()[0]["type"] == "value_error"
    assert "Text cannot contain NUL characters" in caught.value.errors()[0]["msg"]


def test_database_validation_preserves_existing_published_string_contracts():
    assert TypeAdapter(TextInput).json_schema() == {"type": "string", "pattern": r"^[^\x00]*$"}
    assert TypeAdapter(DatabaseText).json_schema() == {"type": "string"}
    assert TypeAdapter(Name).json_schema() == {
        "type": "string",
        "minLength": 1,
        "maxLength": 200,
    }
    assert TypeAdapter(Name).validate_python("  東京 C++  ") == "東京 C++"
    with pytest.raises(ValidationError, match="Text cannot contain NUL characters"):
        TypeAdapter(DatabaseText).validate_python("before\x00after")


@pytest.mark.parametrize("value", ["\ud800", "before\udfffafter"])
def test_database_text_rejects_unpaired_surrogates(value):
    with pytest.raises(ValidationError, match="Text must be valid Unicode"):
        TypeAdapter(DatabaseText).validate_python(value)
    assert TypeAdapter(DatabaseText).validate_python("Unicode 東京 🙂 𐐀") == "Unicode 東京 🙂 𐐀"


@pytest.mark.parametrize("value", ["\ud800", "before\udfffafter"])
@pytest.mark.parametrize(
    "model,payload",
    [
        (SearchSuggestionReview, {"status": "approved", "note": "VALUE"}),
        (
            SourceImportRequest,
            {"format": "urls", "content": "https://publisher.example/", "name": "VALUE"},
        ),
        (TableSettingsPatch, {"query": {"q": "VALUE"}}),
        (
            TopicFact,
            {
                "name": "Language",
                "value": "VALUE",
                "source_url": "https://python.org/about/",
                "retrieved_at": datetime(2026, 10, 5, tzinfo=UTC),
            },
        ),
    ],
)
def test_persisted_json_text_rejects_unpaired_surrogates(model, payload, value):
    def replace_value(item):
        if isinstance(item, dict):
            return {key: replace_value(nested) for key, nested in item.items()}
        return value if item == "VALUE" else item

    with pytest.raises(ValidationError) as caught:
        model.model_validate(replace_value(payload))
    assert caught.value.errors()[0]["type"] in {"value_error", "string_unicode"}


@pytest.mark.parametrize(
    "alias,maximum",
    [(Name, 200), (Keyword, 100), (TaxonomyName, 100), (Description, 500), (ReviewNote, 1000)],
)
def test_shared_text_constraints_preserve_content_whitespace_and_length_bounds(alias, maximum):
    adapter = TypeAdapter(alias)
    content = "東京 — C++ / C#; O'Reilly <script> SELECT 'value'\nSecond line\t🙂"
    assert adapter.validate_python(f" \t{content}\n ") == content
    assert adapter.validate_python(f" {'é' * maximum} ") == "é" * maximum
    for invalid in ("", " \t\n", "é" * (maximum + 1)):
        with pytest.raises(ValidationError):
            adapter.validate_python(invalid)


def test_source_and_taxonomy_models_keep_existing_normalization():
    source = SourceCreate(
        **SOURCE,
        name=" 東京 C++ / C#; O'Reilly ",
        submitted_by={"name": " José — contributor "},
        description="A <b>useful</b> feed\nwith Unicode 東京",
    )
    assert source.name == "東京 C++ / C#; O'Reilly"
    assert source.submitted_by.name == "José — contributor"
    assert source.description == "A useful feed with Unicode 東京"
    assert SourceCreate(**SOURCE, name=" \t ").name is None
    assert SourceSubmission(**SOURCE, name=" \t ").name is None
    with pytest.raises(ValidationError):
        SourcePatch(name=" \t ")

    tag = TagWrite(name=" ## “C#” ", slug="c-sharp", aliases=[" 東京 C++ "])
    assert tag.name == "C#"
    assert tag.aliases == ["東京 C++"]
    topic = TopicWrite(**TOPIC, aliases=[" Python ", "PYTHON"], keywords=[" SQL; DROP "])
    assert topic.aliases == ["PYTHON"]
    assert topic.keywords == ["SQL; DROP"]


def test_review_notes_preserve_multiline_content_and_optional_moderation_semantics():
    text = "  Reviewed C++ / SQL <script> examples\nSecond line 東京  "
    decision = SourceDecision(decision="rejected", actor=" José ", note=text)
    assert decision.actor == "José"
    assert decision.note == text.strip()
    assert SourceDecision(decision="approved").note is None
    for note in (None, "", " \t ", text, "é" * 1000):
        review = SearchSuggestionReview(status="approved", note=note)
        assert review.note == note
    with pytest.raises(ValidationError):
        SearchSuggestionReview(status="approved", note="é" * 1001)


def test_article_and_topic_fact_text_keeps_punctuation_and_existing_bounds():
    text = "  東京 — C++ <script> SELECT 'value'\nSecond line  "
    article = AdminArticleUpdate(title=text, summary=text, expected_revision=0)
    assert article.title == text.strip()
    assert article.summary == text
    assert AdminArticleUpdate(title="Python", expected_revision=0).summary == ""
    maximum_article = AdminArticleUpdate(title="é" * 500, summary="é" * 100000, expected_revision=0)
    assert maximum_article.title == "é" * 500
    assert maximum_article.summary == "é" * 100000
    for payload in (
        {"title": " \t\n"},
        {"title": "é" * 501},
        {"title": "Python", "summary": "é" * 100001},
    ):
        with pytest.raises(ValidationError):
            AdminArticleUpdate(expected_revision=0, **payload)

    fact_payload = {
        "name": "Language",
        "source_url": "https://python.org/about/",
        "retrieved_at": datetime(2026, 10, 5, tzinfo=UTC),
    }
    assert TopicFact(value=text, **fact_payload).value == text
    assert TopicFact(value="é" * 500, **fact_payload).value == "é" * 500
    for value in ("", "é" * 501):
        with pytest.raises(ValidationError):
            TopicFact(value=value, **fact_payload)


def test_import_name_and_table_preferences_keep_existing_content_and_bounds():
    text = "  東京 — C++ <script> SELECT 'value'\nSecond line  "
    import_payload = {"format": "urls", "content": "https://publisher.example/"}
    assert SourceImportRequest(name=text, **import_payload).name == text
    assert SourceImportRequest(name="é" * 200, **import_payload).name == "é" * 200
    assert SourceImportRequest(**import_payload).name == "Admin import"
    for name in ("", "é" * 201):
        with pytest.raises(ValidationError):
            SourceImportRequest(name=name, **import_payload)

    table = TableSettingsPatch(columns={"summary": False}, query={"q": text, "status": ""})
    assert table.columns == {"summary": False}
    assert table.query == {"q": text.strip(), "status": ""}
    assert TableSettings(query={"q": "é" * 500}).query == {"q": "é" * 500}
    for value in ("é" * 501, True, 42, None, ["text"], {"q": "text"}):
        with pytest.raises(ValidationError):
            TableSettingsPatch(query={"q": value})


@pytest.mark.parametrize("name", ["\x00text", "before\x00after", "text\x00"])
@pytest.mark.parametrize("format", ["json", "markdown"])
def test_publisher_import_rejects_nul_in_names(name, format):
    body = (
        json.dumps([{"homepage_url": "https://publisher.example/", "name": name}])
        if format == "json"
        else f"[{name}](https://publisher.example/)"
    )
    with pytest.raises(ValueError, match="Publisher names cannot contain NUL"):
        parse_import(body.encode(), format)


def test_publisher_names_keep_unicode_markup_and_existing_truncation():
    name = "東京 — C++ <script> SELECT 'value'\nSecond line"
    assert publisher_hint("https://publisher.example/", name).name == name
    assert publisher_hint("https://publisher.example/", "é" * 201).name == "é" * 200
    assert publisher_hint("https://publisher.example/", "").name == "publisher.example"


@pytest.mark.parametrize("name", ["\ud800", "before\udfffafter"])
def test_publisher_import_rejects_escaped_unpaired_surrogates(name):
    with pytest.raises(ValueError, match="Text must be valid Unicode"):
        publisher_hint("https://publisher.example/", name)
    body = json.dumps([{"homepage_url": "https://publisher.example/", "name": name}])
    with pytest.raises(ValueError, match="Text must be valid Unicode"):
        parse_import(body.encode(), "json")


@pytest.mark.parametrize("field", ["homepage_url", "feed_url"])
@pytest.mark.parametrize("suffix", ["/before\ud800after", "/feed?q=before\udfffafter"])
def test_publisher_import_rejects_invalid_unicode_urls(field, suffix):
    publisher = {
        "homepage_url": "https://publisher.example/",
        "feed_url": "https://publisher.example/feed",
    }
    publisher[field] = f"https://publisher.example{suffix}"
    with pytest.raises(ValueError, match="Text must be valid Unicode"):
        publisher_hint(publisher["homepage_url"], feed=publisher["feed_url"])
    with pytest.raises(ValueError, match="Text must be valid Unicode"):
        parse_import(json.dumps([publisher]).encode(), "json")


@pytest.mark.parametrize(
    "homepage,feed",
    [
        (
            "https://publisher.example/東京/🙂",
            "https://publisher.example/feed?language=日本語",
        ),
        (
            "https://publisher.example/%E6%9D%B1%E4%BA%AC",
            "https://publisher.example/feed?q=%F0%9F%99%82",
        ),
    ],
)
def test_import_preserves_valid_unicode_and_percent_encoded_urls(homepage, feed):
    [hint] = parse_import(
        json.dumps([{"homepage_url": homepage, "feed_url": feed}]).encode(), "json"
    )
    assert hint.homepage == homepage
    assert hint.feed_hint == feed


@pytest.fixture
def persisted_admin_client(monkeypatch):
    def unexpected_storage(*args, **kwargs):
        pytest.fail("Invalid input reached database work")

    app = FastAPI()
    register_error_handlers(app, logging.getLogger(__name__), admin=True)
    for router in (source_imports.router, topic_proposals.router, user_settings.router):
        app.include_router(router)
    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(subject="test-admin")
    app.dependency_overrides[get_session] = lambda: SimpleNamespace(
        scalar=unexpected_storage, scalars=unexpected_storage, execute=unexpected_storage
    )
    monkeypatch.setattr(source_imports.discovery, "import_publishers", unexpected_storage)
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize(
    "body",
    [
        {"format": "urls", "content": "https://publisher.example/", "name": "before\x00after"},
        {
            "format": "json",
            "content": '[{"homepage_url":"https://publisher.example/","name":"before\\u0000after"}]',
        },
        {"format": "markdown", "content": "[before\x00after](https://publisher.example/)"},
    ],
)
def test_invalid_import_names_return_422_before_persistence(persisted_admin_client, body):
    response = persisted_admin_client.post("/v1/admin/source-imports", json=body)
    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    "path,params",
    [
        ("source-imports", {"q": "before\x00after"}),
        ("source-imports", {"q": "é" * 201}),
        ("topic-proposals", {"kind": "before\x00after"}),
        ("topic-proposals", {"source": "before\x00after"}),
        ("topic-proposals", {"kind": "é" * 101}),
        ("topic-proposals", {"source": "é" * 201}),
    ],
)
def test_invalid_admin_filters_return_422_before_sql(persisted_admin_client, path, params):
    response = persisted_admin_client.get(f"/v1/admin/{path}", params=params)
    assert response.status_code == 422, response.text


def test_invalid_table_query_returns_422_before_jsonb(persisted_admin_client):
    response = persisted_admin_client.patch(
        "/v1/admin/settings/tables/topic-proposals", json={"query": {"q": "before\x00after"}}
    )
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("value", ["\ud800", "before\udfffafter"])
@pytest.mark.parametrize(
    "method,path,payload",
    [
        (
            "POST",
            "/v1/admin/source-imports",
            {"format": "urls", "content": "https://publisher.example/", "name": "VALUE"},
        ),
        (
            "PATCH",
            "/v1/admin/settings/tables/topic-proposals",
            {"query": {"q": "VALUE"}},
        ),
    ],
)
def test_invalid_unicode_json_returns_422_before_storage(
    persisted_admin_client, method, path, payload, value
):
    body = json.dumps(payload).replace("VALUE", json.dumps(value)[1:-1])
    response = persisted_admin_client.request(
        method, path, content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["type"] in {"value_error", "string_unicode"}
    assert "input" not in response.json()["detail"][0]


@pytest.mark.parametrize("name", ["\ud800", "before\udfffafter"])
def test_invalid_unicode_import_content_returns_422_before_storage(persisted_admin_client, name):
    payload = {
        "format": "json",
        "content": json.dumps([{"homepage_url": "https://publisher.example/", "name": name}]),
    }
    response = persisted_admin_client.post("/v1/admin/source-imports", json=payload)
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("field", ["homepage_url", "feed_url"])
@pytest.mark.parametrize("suffix", ["/before\ud800after", "/feed?q=before\udfffafter"])
def test_invalid_unicode_import_url_returns_422_before_storage(
    persisted_admin_client, field, suffix
):
    publisher = {
        "homepage_url": "https://publisher.example/",
        "feed_url": "https://publisher.example/feed",
    }
    publisher[field] = f"https://publisher.example{suffix}"
    response = persisted_admin_client.post(
        "/v1/admin/source-imports", json={"format": "json", "content": json.dumps([publisher])}
    )
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("key", ["\ud800", "\udfff"])
@pytest.mark.parametrize("field,value", [("query", "ok"), ("columns", True)])
def test_invalid_unicode_setting_keys_have_serializable_validation_errors(
    persisted_admin_client, key, field, value
):
    response = persisted_admin_client.patch(
        "/v1/admin/settings/tables/topic-proposals",
        content=json.dumps({field: {key: value}}),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["type"] == "string_unicode"
