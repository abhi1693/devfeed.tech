"""Untrusted search and suggestion queries stay bounded text."""

import uuid
from types import SimpleNamespace

import pytest
from devfeed_core.search_suggestions import normalize_query
from fastapi.testclient import TestClient

RESULT_ID = uuid.UUID("8e65d3cd-ef29-455a-88c6-990b96da9479")
PROBE = '"><asdf alt="">-f3f3'


@pytest.mark.parametrize("value", ["python\0", "ﬃ" * 200, "word " * 21, "x" * 201])
def test_search_and_suggestions_reject_invalid_input_before_dependencies_run(value, monkeypatch):
    from devfeed_api import search
    from devfeed_api.dependencies import get_session
    from devfeed_api.main import create_app

    app = create_app()
    calls = []
    app.dependency_overrides[get_session] = lambda: SimpleNamespace(
        execute=lambda *args: calls.append(args)
    )
    monkeypatch.setattr(search, "Typesense", lambda: pytest.fail("invalid query reached index"))
    with TestClient(app) as client:
        for path in ["/v1/search", "/v1/search/suggestions"]:
            assert client.get(path, params={"q": value}).status_code == 422
    assert calls == []


@pytest.mark.parametrize(
    "value", [PROBE, "SELECT * FROM users;", "<script>alert(1)</script>", "日本語", "C++"]
)
def test_search_text_is_not_a_sql_or_html_blacklist(value, monkeypatch):
    from devfeed_api import search
    from devfeed_api.dependencies import get_session
    from devfeed_api.main import create_app

    app = create_app()
    calls = []
    app.dependency_overrides[get_session] = lambda: SimpleNamespace(execute=lambda *args: None)

    def query(q, kinds, page, **kwargs):
        calls.append(q)
        return {kind: {"hits": [], "found": 0} for kind in kinds}

    monkeypatch.setattr(search, "Typesense", lambda: SimpleNamespace(search=query))
    with TestClient(app) as client:
        response = client.get("/v1/search", params={"q": value})
    assert response.status_code == 200
    assert response.json()["query"] == value
    assert calls == [value]


def test_canonicalization_validates_after_nfkc_and_casefold():
    assert normalize_query("  Ｐｙｔｈｏｎ\tSQL ") == "python sql"
    assert normalize_query("  Ｐｙｔｈｏｎ\tSQL ", casefold=False) == "Python SQL"
    assert normalize_query("?!", allow_empty=True) == "?!"
    assert normalize_query("", allow_empty=True) == ""
    assert normalize_query("ﬃ" * 66) == "ffi" * 66
    for value in ["ﬃ" * 67, "ß" * 101, "\0", "!!!", ""]:
        with pytest.raises(ValueError):
            normalize_query(value)


@pytest.mark.parametrize("value", ["\ud800", "before\udfffafter"])
def test_query_normalization_rejects_unpaired_surrogates(value):
    with pytest.raises(ValueError, match="Text must be valid Unicode"):
        normalize_query(value)
    assert normalize_query("Unicode 東京 🙂 𐐀", casefold=False) == "Unicode 東京 🙂 𐐀"
