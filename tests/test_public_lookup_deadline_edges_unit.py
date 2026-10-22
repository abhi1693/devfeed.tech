"""Public reads reject empty identities and exhausted external-search deadlines.

Article rows are supplied after public repository selection; these cases do not
claim SQL visibility, slug uniqueness or filtering behavior.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_api import cache, dependencies, feed, search
from devfeed_core.schemas import ArticleOut
from devfeed_user_api import dependencies as user_dependencies
from devfeed_user_api import profile
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from test_article_readiness_unit import processing_case as processing_case
from test_source_readiness_unit import configuration
from test_user_feed_read_remaining_unit import stories as stories


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
