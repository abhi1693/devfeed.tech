"""Direct ClickHouse opens preserve reader behavior without PostgreSQL analytics rows."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from devfeed_core.models import ArticleLike
from devfeed_user_api import engagement
from sqlalchemy import text

from tests.support.user_analytics import direct_client as direct_client
from tests.support.user_personalization import user_data as user_data

pytestmark = pytest.mark.integration


def test_direct_open_deduplicates_and_never_creates_pg_analytics_rows(direct_client, database):
    client, current, _, ids, _ = direct_client
    article = ids["Article 000"]
    url = f"/v1/user/articles/{article}/open"
    headers = {"Origin": "http://testserver"}
    first = client.post(url, headers=headers)
    assert first.status_code == 200 and first.json()["opens"] == 1
    assert client.post(url, headers=headers).json()["opens"] == 1
    client.cookies.clear()
    assert client.post(url, headers=headers).json()["opens"] == 2
    assert (
        client.post(f"/v1/user/articles/{ids['Article 113']}/open", headers=headers).status_code
        == 404
    )
    client.app.dependency_overrides[engagement.optional_user] = lambda: current
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: client.post(url, headers=headers), range(4)))
    assert all(result.status_code == 200 for result in results)
    assert (
        client.get("/v1/user/engagement", params={"article_id": str(article)}).json()[0]["opens"]
        == 3
    )
    with database() as session:
        for table in ("article_opens", "article_engagement"):
            # Upgrading installations retain frozen history until import ends;
            # neither live direct path may create another PostgreSQL fact.
            if session.scalar(text("SELECT to_regclass(:name)"), {"name": table}):
                assert session.scalar(text(f"SELECT count(*) FROM {table}")) == 0


def test_direct_trending_merges_likes_and_opens_and_filters_visibility(direct_client, database):
    client, _, first, ids, _ = direct_client
    with database.begin() as session:
        session.add(ArticleLike(article_id=ids["Article 000"], user_id=first))
        session.add(ArticleLike(article_id=ids["Article 110"], user_id=first))
    headers = {"Origin": "http://testserver"}
    assert (
        client.post(f"/v1/user/articles/{ids['Article 001']}/open", headers=headers).status_code
        == 200
    )
    result = client.get("/v1/user/trending", params={"limit": 1})
    assert result.status_code == 200
    assert result.json()["items"][0]["id"] == str(ids["Article 000"])
    next_page = client.get(
        "/v1/user/trending", params={"limit": 1, "cursor": result.json()["next_cursor"]}
    )
    assert next_page.status_code == 200
    assert next_page.json()["items"][0]["id"] == str(ids["Article 001"])
    assert next_page.json()["next_cursor"] is None
