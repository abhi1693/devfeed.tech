"""Reader engagement fixture with disposable PostgreSQL and Redis."""

import pytest
from devfeed_core.models import Article
from devfeed_user_api import engagement
from devfeed_user_api.config import Settings
from sqlalchemy import select


@pytest.fixture
def direct_client(user_data, database, monkeypatch):
    client, current, first, _, _ = user_data
    monkeypatch.setattr(
        engagement,
        "get_settings",
        lambda: Settings(_env_file=None, base_url="http://testserver", cookie_secure=False),
    )
    client.app.dependency_overrides[engagement.optional_user] = lambda: None
    with database() as session:
        ids = {row.title: row.id for row in session.execute(select(Article.title, Article.id))}
    return client, current, first, ids
