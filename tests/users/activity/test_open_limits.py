"""Shared Redis budgets bound cookie rotation and work across service replicas."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from devfeed_user_api.open_limits import get_redis, limit_open_requests
from fastapi import HTTPException

pytestmark = pytest.mark.integration


def test_per_viewer_budget_spans_articles_and_rejections_do_not_charge_shared_budget(database):
    for index in range(30):
        limit_open_requests(str(index), "viewer", anonymous=True)
    before = get_redis().get("devfeed:user:open-limits:anonymous:minute")
    for _ in range(5):
        with pytest.raises(HTTPException) as error:
            limit_open_requests("another", "viewer", anonymous=True)
        assert error.value.status_code == 429
        assert 0 < int(error.value.headers["Retry-After"]) <= 60
    assert get_redis().get("devfeed:user:open-limits:anonymous:minute") == before


def test_article_cap_is_atomic_across_concurrent_visitors(database):
    def attempt(index):
        try:
            limit_open_requests("article", f"visitor-{index}", anonymous=True)
            return 200
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(40)))
    assert results.count(200) == results.count(429) == 20
    # A real signed-in viewer is unaffected by the exhausted anonymous budget.
    limit_open_requests("article", "signed-in", anonymous=False)


def test_global_anonymous_budget_survives_article_and_cookie_rotation(database):
    for index in range(120):
        limit_open_requests(str(index), str(index), anonymous=True)
    with pytest.raises(HTTPException) as error:
        limit_open_requests("fresh-article", "fresh-cookie", anonymous=True)
    assert error.value.status_code == 429
    assert all(
        get_redis().ttl(key) > 0 for key in get_redis().scan_iter("devfeed:user:open-limits:*")
    )
