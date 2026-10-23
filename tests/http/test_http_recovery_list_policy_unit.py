"""Browser recovery and operator lists preserve binding, bounds and useful failures.

Redis get/getdel and database methods supply explicit read outcomes. These tests
do not execute Redis atomicity, SQL predicates or row-lock semantics.
"""

import json
from types import SimpleNamespace as NS
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from devfeed_core.models import Topic
from devfeed_core.schemas import MAX_OFFSET
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_core.topics import TopicOut
from devfeed_http import oidc_retry as recovery
from devfeed_http.pagination import (
    Listing,
    Page,
    paginate,
    prohibit_references,
    record,
    require_record,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError
from sqlalchemy import select
from starlette.requests import Request

from tests.support.taxonomy_command_helpers import topic

BROWSER, TOKEN = "b" * 43, "t" * 43


def key(kind, value):
    return f"admin:{kind}:{value}"


@pytest.mark.parametrize("invalid", ["", "short", "with spaces", "b" * 257])
def test_recovery_refuses_invalid_browser_identity_before_saving_any_context(invalid):
    redis = Mock()
    with pytest.raises(recovery.ExpiredRetry):
        recovery.save(redis, key=key, browser=invalid, policy="current-client")
    redis.set.assert_not_called()


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "{",
        "[]",
        "null",
        '{"browser":12}',
        json.dumps({"browser": "other-browser", "policy": "current-client"}),
        json.dumps({"browser": BROWSER, "policy": "different-client"}),
    ],
)
def test_unusable_or_unbound_recovery_never_consumes_another_context(raw):
    redis = Mock()
    redis.get.return_value = raw
    with pytest.raises(recovery.ExpiredRetry):
        recovery.consume(redis, key=key, token=TOKEN, browser=BROWSER, policy="current-client")
    redis.getdel.assert_not_called()


def test_recovery_lost_compare_and_delete_cannot_resume_a_replaced_or_consumed_flow():
    redis = Mock()
    raw = json.dumps(
        {"browser": BROWSER, "policy": "current-client", "destination": "/jobs/analysis"}
    )
    redis.get.return_value, redis.getdel.return_value = raw, None
    with pytest.raises(recovery.ExpiredRetry):
        recovery.consume(redis, key=key, token=TOKEN, browser=BROWSER, policy="current-client")
    redis.getdel.assert_called_once_with(key("retry", TOKEN))


def test_recovery_saves_only_browser_policy_and_validated_destination_then_consumes_that_document():
    redis = Mock()
    token = recovery.save(
        redis, key=key, browser=BROWSER, policy="current-client", destination="/jobs/analysis"
    )
    saved_key, document = redis.set.call_args.args
    assert saved_key == key("retry", token) and redis.set.call_args.kwargs == {"ex": 300}
    assert json.loads(document) == {
        "browser": BROWSER,
        "policy": "current-client",
        "destination": "/jobs/analysis",
    }
    redis.get.return_value = redis.getdel.return_value = document
    assert recovery.consume(
        redis, key=key, token=token, browser=BROWSER, policy="current-client"
    ) == json.loads(document)


@pytest.mark.parametrize("failure", ["factory", "save"])
def test_dependency_outage_still_redirects_without_creating_a_retry_cookie_or_leaking_details(
    failure,
):
    redis, cookie = Mock(), Mock()
    factory = Mock(return_value=redis)
    if failure == "factory":
        factory.side_effect = ConnectionError("private redis endpoint")
    else:
        redis.set.side_effect = ConnectionError("private redis endpoint")
    value = recovery.Recovery(
        "https://admin.example/", BROWSER, "current-client", key, cookie, factory
    )
    response = value.unavailable(destination="/jobs/analysis")
    assert response.status_code == 302
    location = urlsplit(response.headers["location"])
    params = parse_qs(location.query)
    assert location.netloc == "admin.example" and location.path == "/login"
    assert params["error"] == ["login_unavailable"] and params["ref"][0]
    assert "retry" not in params and "private" not in response.headers["location"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    cookie.assert_not_called()


@pytest.mark.parametrize("query", [b"", b"retry=a&retry=b"])
def test_recovery_adapter_rejects_missing_or_duplicate_retry_parameter_without_reading_store(query):
    factory = Mock()
    value = recovery.Recovery(
        "https://admin.example", BROWSER, "current-client", key, Mock(), factory
    )
    request = Request({"type": "http", "query_string": query, "headers": []})
    with pytest.raises(recovery.ExpiredRetry):
        value.consume(request, TOKEN)
    factory.assert_not_called()
    assert value.consume(request, None) is None


@pytest.fixture
def listing():
    session = Mock()
    app = FastAPI()

    @app.get("/topics", response_model=Page[TopicOut])
    def topics(query: Listing):
        return paginate(
            session, select(Topic), query, {"name": Topic.name, "created": Topic.created_at}
        )

    with TestClient(app) as client:
        yield NS(client=client, session=session)


@pytest.mark.parametrize("sort", [None, "name", "-created"])
def test_preselected_page_retains_global_total_and_requested_window_with_real_topic_projection(
    listing, sort
):
    current = topic()
    listing.session.scalar.return_value = 45
    listing.session.scalars.return_value.all.return_value = [current]
    response = listing.client.get(
        "/topics",
        params={"q": "  React  ", "limit": 1, "offset": 25, **({"sort": sort} if sort else {})},
    )
    assert response.status_code == 200, response.text
    output = response.json()
    assert (output["total"], output["limit"], output["offset"]) == (45, 1, 25)
    assert [item["id"] for item in output["items"]] == [str(current.id)]
    assert output["items"][0]["name"] == "React"


def test_empty_last_page_keeps_total_instead_of_claiming_dataset_is_empty(listing):
    listing.session.scalar.return_value = 45
    listing.session.scalars.return_value.all.return_value = []
    response = listing.client.get("/topics", params={"offset": 50})
    assert response.json() == {"items": [], "total": 45, "limit": 25, "offset": 50}


@pytest.mark.parametrize(
    "params",
    [
        {"sort": "private_column"},
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
        {"offset": MAX_OFFSET + 1},
        {"q": "x" * 201},
        {"q": "bad\x00query"},
    ],
)
def test_list_transport_rejects_unsupported_sort_or_unbounded_inputs_before_querying(
    listing, params
):
    response = listing.client.get("/topics", params=params)
    assert response.status_code == 422
    listing.session.scalar.assert_not_called()
    listing.session.scalars.assert_not_called()


@pytest.mark.parametrize("lock", [False, True])
@pytest.mark.parametrize("present", [False, True])
def test_record_adapter_returns_current_row_or_typed_missing_parent_error(lock, present):
    current, session = topic(), Mock()
    session.scalar.return_value = current if present else None
    if present:
        assert record(session, Topic, current.id, lock=lock) is current
    else:
        with pytest.raises(RecordNotFound, match="Topic not found"):
            record(session, Topic, current.id, lock=lock)


@pytest.mark.parametrize("present", [False, True])
def test_nested_resource_parent_presence_is_required_without_hydrating_it(present):
    current, session = topic(), Mock()
    session.scalar.return_value = current.id if present else None
    if present:
        assert require_record(session, Topic, current.id) is None
    else:
        with pytest.raises(RecordNotFound, match="Topic not found"):
            require_record(session, Topic, current.id)
    session.get.assert_not_called()


@pytest.mark.parametrize("blocked", [False, True])
def test_delete_guard_reports_first_remaining_dependency_and_stops(blocked):
    session = Mock()
    session.scalar.side_effect = [False, blocked]
    references = [("articles", select(Topic.id)), ("relationships", select(Topic.id))]
    if blocked:
        with pytest.raises(OperationConflict, match="linked relationships exist"):
            prohibit_references(session, references)
    else:
        assert prohibit_references(session, references) is None
    assert session.scalar.call_count == 2
