"""Engagement and trending adapt selected read results without exposing reader identity.

Aggregate and article pages are authoritative repository projections. These units
do not compute SQL popularity, publication eligibility or per-user membership.
"""

import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_user_api import dependencies, engagement
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from test_user_feed_read_remaining_unit import processing_case as processing_case
from test_user_feed_read_remaining_unit import stories as stories
from test_user_source_boundary_remaining_unit import user


@contextmanager
def read_api(viewer=None):
    app, session = FastAPI(), Mock(spec=Session)
    app.include_router(engagement.router)
    app.dependency_overrides[engagement.optional_user] = lambda: viewer
    app.dependency_overrides[dependencies.get_session] = lambda: session
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, session=session)


@pytest.mark.parametrize("signed_in", [False, True])
def test_engagement_projection_returns_counts_and_owned_flags_without_personal_identifiers(
    signed_in,
):
    viewer, identifier = user() if signed_in else None, uuid.uuid4()
    selected = {
        "article_id": identifier,
        "likes": 3,
        "opens": 4,
        "liked": signed_in,
        "bookmarked": signed_in,
    }
    with read_api(viewer) as api:
        api.session.execute.return_value.mappings.return_value = [selected]
        response = api.client.get("/v1/user/engagement", params={"article_id": str(identifier)})
        assert response.status_code == 200
        assert response.json() == [{**selected, "article_id": str(identifier)}]
        assert set(response.json()[0]) == set(engagement.EngagementOut.model_fields)
        api.session.execute.assert_called_once()
        api.session.commit.assert_not_called()
        api.session.add.assert_not_called()


@pytest.mark.parametrize(
    "offset,empty,next_cursor",
    [(0, False, "1"), (999999, False, "1000000"), (1000000, False, None), (0, True, None)],
)
def test_trending_lookahead_returns_only_the_requested_page_and_stops_at_the_supported_cursor_bound(
    stories, offset, empty, next_cursor
):
    with read_api() as api:
        api.session.scalars.return_value = [] if empty else stories
        response = api.client.get("/v1/user/trending", params={"cursor": offset, "limit": 1})
        assert response.status_code == 200, response.text
        output = response.json()
        assert [item["id"] for item in output["items"]] == ([] if empty else [str(stories[0].id)])
        assert output["next_cursor"] == next_cursor
        if not empty:
            assert "classification_provenance" not in output["items"][0]
        api.session.scalars.assert_called_once()
        api.session.commit.assert_not_called()
