"""Public caller abuse and receipt replay cannot drain another reader's measurement quota."""

import hashlib
import hmac
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from devfeed_api import partner_placements as placement_api
from devfeed_api import partner_tracking as tracking_api
from devfeed_api.main import create_app
from devfeed_core.config import get_settings
from devfeed_core.models import PartnerDeliveryEvent, PartnerTrackedDailyMetric
from devfeed_core.partner_placements import deliver_placements
from devfeed_core.partner_tracking import PartnerTrackingTokens
from devfeed_http.partner_placements import PlacementQuery
from fastapi.testclient import TestClient
from pydantic import SecretStr
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import event, func, select
from sqlalchemy.exc import SQLAlchemyError

from tests.partners.placements.test_placement_rotation import seed_rotation

pytestmark = pytest.mark.integration
KEY = "disposable-public-partner-rate-limit-key-32-bytes"
PLACEMENTS = "/v1/partner-placements/query"
EVENTS = "/v1/partner-tracking/events"


@pytest.fixture
def public_clients(database, monkeypatch):
    monkeypatch.setattr(get_settings(), "partner_tracking_key", SecretStr(KEY))
    redis = Redis.from_url(get_settings().redis_url)
    monkeypatch.setattr(placement_api, "get_rate_limit_redis", lambda: redis)
    monkeypatch.setattr(tracking_api, "get_rate_limit_redis", lambda: redis)
    placement_budgets, tracking_budgets = [], []
    for module, observed in (
        (placement_api, placement_budgets),
        (tracking_api, tracking_budgets),
    ):
        original = module.consume_rate_limits

        def observe(redis, budgets, *, original=original, observed=observed):
            observed.extend(budgets)
            return original(redis, budgets)

        monkeypatch.setattr(module, "consume_rate_limits", observe)
    app = create_app()
    with (
        TestClient(app, client=("198.51.100.10", 50000)) as first,
        TestClient(app, client=("198.51.100.20", 50001)) as second,
    ):
        yield SimpleNamespace(
            first=first,
            second=second,
            redis=redis,
            placement_budgets=placement_budgets,
            tracking_budgets=tracking_budgets,
        )
    redis.close()


def issue_receipts(database, *, count=2):
    catalog = seed_rotation(database, count=1)
    receipts = []
    for _ in range(count):
        with database.begin() as session:
            rows = deliver_placements(
                session,
                PlacementQuery(surface="article", article_id=catalog["article"]),
                KEY,
                now=catalog["now"],
            )
            assert len(rows) == 1
            receipts.append(rows[0]["receipt"])
    return receipts


def reserve_shared_capacity(redis, token, *, remaining):
    receipt = PartnerTrackingTokens(KEY).verify(token)
    assert receipt is not None
    redis.set("devfeed:partner-events:minute", 3000 - remaining, ex=60)
    redis.set(f"devfeed:partner-events:{receipt.asset_id}:minute", 1000 - remaining, ex=60)
    return receipt


def assert_measurements(database, receipt, *, impressions, clicks):
    with database() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(PartnerDeliveryEvent)
                .where(PartnerDeliveryEvent.asset_id == receipt.asset_id)
            )
            == impressions + clicks
        )
        metric = session.get(
            PartnerTrackedDailyMetric, (receipt.asset_id, receipt.issued_at.date())
        )
        assert metric is not None
        assert (metric.impressions, metric.clicks) == (impressions, clicks)


def test_one_placement_caller_cannot_drain_other_callers_shared_budget(public_clients):
    state = public_clients
    payload = {"surface": "feed"}
    assert state.first.post(PLACEMENTS, json=payload).status_code == 200
    first_budgets = {budget.key: budget for budget in state.placement_budgets}
    state.placement_budgets.clear()
    assert state.second.post(PLACEMENTS, json=payload).status_code == 200
    second_budgets = {budget.key: budget for budget in state.placement_budgets}
    caller_keys = first_budgets.keys() - second_budgets.keys()
    shared_keys = first_budgets.keys() & second_budgets.keys()
    assert caller_keys, "Placement admission needs a budget specific to the request's peer"
    assert shared_keys
    for key in caller_keys:
        budget = first_budgets[key]
        state.redis.set(key, budget.limit - 1, ex=budget.window)
    for key in shared_keys:
        budget = first_budgets[key]
        state.redis.set(key, budget.limit - 3, ex=budget.window)

    assert state.first.post(PLACEMENTS, json=payload).status_code == 200
    for index in range(5):
        rejected = state.first.post(
            PLACEMENTS,
            json=payload,
            headers={
                "X-Forwarded-For": f"203.0.113.{index + 1}",
                "Forwarded": f"for=203.0.113.{index + 1}",
            },
        )
        assert rejected.status_code == 429
        assert int(rejected.headers["retry-after"]) > 0
        assert rejected.headers["cache-control"] == "no-store"
    for _ in range(2):
        assert state.second.post(PLACEMENTS, json=payload).status_code == 200
    assert state.second.post(PLACEMENTS, json=payload).status_code == 429


def test_signed_gateway_proofs_isolate_readers_behind_the_same_proxy(
    database, public_clients, monkeypatch
):
    state = public_clients
    gateway_key = "disposable-gateway-caller-key-32-bytes"
    monkeypatch.setattr(get_settings(), "partner_caller_key", SecretStr(gateway_key))
    token = issue_receipts(database, count=1)[0]

    def gateway_headers(address, path):
        payload = f"v1.{int(time.time())}.{address}"
        signing_key = hmac.digest(gateway_key.encode(), b"devfeed:partner-caller:v1", "sha256")
        signature = hmac.new(
            signing_key, f"{payload}\nPOST\n{path}".encode(), hashlib.sha256
        ).hexdigest()
        return {"X-DevFeed-Partner-Caller": f"{payload}.{signature}"}

    for path, body, observed, success in (
        (PLACEMENTS, {"surface": "feed"}, state.placement_budgets, 200),
        (EVENTS, {"receipt": token, "kind": "impression"}, state.tracking_budgets, 204),
    ):
        observed.clear()
        headers = gateway_headers("203.0.113.10", path)
        assert state.first.post(path, json=body, headers=headers).status_code == success
        first_budgets = {budget.key: budget for budget in observed}
        observed.clear()
        other_headers = gateway_headers("203.0.113.20", path)
        assert state.first.post(path, json=body, headers=other_headers).status_code == success
        other_keys = {budget.key for budget in observed}
        caller_keys = first_budgets.keys() - other_keys
        assert caller_keys, "Gateway proofs must distinguish readers on one transport peer"
        for key in caller_keys:
            budget = first_budgets[key]
            state.redis.set(key, budget.limit, ex=budget.window)
        assert state.first.post(path, json=body, headers=headers).status_code == 429
        assert state.first.post(path, json=body, headers=other_headers).status_code == success


def test_duplicate_impressions_preserve_capacity_for_a_click_and_charge_only_unique_events(
    database, public_clients
):
    state = public_clients
    token, next_token = issue_receipts(database)
    receipt = reserve_shared_capacity(state.redis, token, remaining=2)
    impression = {"receipt": token, "kind": "impression"}
    for _ in range(9):
        assert state.first.post(EVENTS, json=impression).status_code == 204
    assert state.second.post(EVENTS, json={"receipt": token, "kind": "click"}).status_code == 204
    assert (
        state.first.post(EVENTS, json={"receipt": next_token, "kind": "impression"}).status_code
        == 429
    )
    assert int(state.redis.get("devfeed:partner-events:minute")) == 3000
    assert int(state.redis.get(f"devfeed:partner-events:{receipt.asset_id}:minute")) == 1000
    assert_measurements(database, receipt, impressions=1, clicks=1)


def test_impression_caller_limit_preserves_clicks_and_other_callers(public_clients, database):
    state = public_clients
    token = issue_receipts(database, count=1)[0]
    impression = {"receipt": token, "kind": "impression"}
    assert state.first.post(EVENTS, json=impression).status_code == 204
    assert state.tracking_budgets
    for budget in state.tracking_budgets:
        state.redis.set(budget.key, budget.limit, ex=budget.window)

    assert state.first.post(EVENTS, json=impression).status_code == 429
    assert state.first.post(EVENTS, json={"receipt": token, "kind": "click"}).status_code == 204
    assert state.second.post(EVENTS, json=impression).status_code == 204
    receipt = PartnerTrackingTokens(KEY).verify(token)
    assert_measurements(database, receipt, impressions=1, clicks=1)


def test_concurrent_duplicate_impressions_preserve_a_clicks_shared_capacity(
    database, public_clients
):
    state = public_clients
    token = issue_receipts(database, count=1)[0]
    receipt = reserve_shared_capacity(state.redis, token, remaining=2)
    impression = {"receipt": token, "kind": "impression"}

    def report(_index):
        return state.first.post(EVENTS, json=impression).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(report, range(12)))
    # Attribution deliberately skips busy rows; those reports can be retried
    # after the first writer commits without consuming another measurement slot.
    assert set(responses) <= {204, 503}
    for status in responses:
        if status == 503:
            assert state.first.post(EVENTS, json=impression).status_code == 204
    assert state.second.post(EVENTS, json={"receipt": token, "kind": "click"}).status_code == 204
    assert int(state.redis.get("devfeed:partner-events:minute")) == 3000
    assert int(state.redis.get(f"devfeed:partner-events:{receipt.asset_id}:minute")) == 1000
    assert_measurements(database, receipt, impressions=1, clicks=1)


@pytest.mark.parametrize("failure_stage", ["metric", "commit", "redis_response"])
def test_failed_tracking_write_can_retry_without_recharging_shared_capacity(
    database, public_clients, monkeypatch, failure_stage
):
    state = public_clients
    token = issue_receipts(database, count=1)[0]
    receipt = reserve_shared_capacity(state.redis, token, remaining=2)
    writes = 0

    def fail_after_metric_insert(
        _connection, _cursor, _statement, _parameters, context, _executemany
    ):
        nonlocal writes
        statement = context.compiled.statement if context.compiled else None
        if (
            getattr(statement, "is_insert", False)
            and statement.table.name == "partner_tracked_daily_metrics"
        ):
            writes += 1
            raise SQLAlchemyError("Transient partner counter failure")

    def fail_before_commit(_connection):
        nonlocal writes
        writes += 1
        raise SQLAlchemyError("Transient partner commit failure")

    original_reserve = tracking_api.consume_reserved_rate_limits

    def lose_reservation_response(*args, **kwargs):
        nonlocal writes
        assert original_reserve(*args, **kwargs) == 0
        writes += 1
        raise RedisError("Reservation succeeded but its response was lost")

    engine = database.kw["bind"]
    if failure_stage == "metric":
        event_name, fail = "after_cursor_execute", fail_after_metric_insert
    else:
        event_name, fail = "commit", fail_before_commit
    if failure_stage == "redis_response":
        monkeypatch.setattr(tracking_api, "consume_reserved_rate_limits", lose_reservation_response)
    else:
        event.listen(engine, event_name, fail)
    try:
        failed = state.first.post(EVENTS, json={"receipt": token, "kind": "impression"})
    finally:
        if failure_stage == "redis_response":
            monkeypatch.setattr(tracking_api, "consume_reserved_rate_limits", original_reserve)
        else:
            event.remove(engine, event_name, fail)
    assert failed.status_code == 503
    assert writes == 1
    with database() as session:
        assert session.scalar(select(func.count()).select_from(PartnerDeliveryEvent)) == 0
        assert session.scalar(select(func.count()).select_from(PartnerTrackedDailyMetric)) == 0
    assert (
        state.first.post(EVENTS, json={"receipt": token, "kind": "impression"}).status_code == 204
    )
    assert int(state.redis.get("devfeed:partner-events:minute")) == 2999
    assert int(state.redis.get(f"devfeed:partner-events:{receipt.asset_id}:minute")) == 999
    assert state.second.post(EVENTS, json={"receipt": token, "kind": "click"}).status_code == 204
    assert int(state.redis.get("devfeed:partner-events:minute")) == 3000
    assert int(state.redis.get(f"devfeed:partner-events:{receipt.asset_id}:minute")) == 1000
    assert_measurements(database, receipt, impressions=1, clicks=1)
