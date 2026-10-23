import base64
import json
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from devfeed_api.dependencies import get_session
from devfeed_api.feed import decode_cursor, encode_cursor
from devfeed_api.main import create_app
from devfeed_core.models import Article
from fastapi import HTTPException
from fastapi.testclient import TestClient

DATE = "2026-01-01T00:00:00+00:00"
IDENTIFIER = "dc12ba8f-1b4f-42e8-aa80-d752c6d74c4b"


def cursor_for(payload):
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


@pytest.fixture
def client_without_database():
    app = create_app()

    def query(_):
        pytest.fail("Malformed cursor reached an article query")

    app.dependency_overrides[get_session] = lambda: SimpleNamespace(scalars=query)
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        [],
        "",
        123,
        [DATE],
        [DATE, IDENTIFIER, "extra"],
        [DATE, 123],
        [DATE, 1.5],
        [DATE, []],
        [DATE, {}],
        [DATE, None],
        [DATE, True],
        [123, IDENTIFIER],
        [[], IDENTIFIER],
        [{}, IDENTIFIER],
        [None, IDENTIFIER],
        {DATE: "ignored", IDENTIFIER: "ignored"},
        [DATE, ""],
        [DATE, "not-a-uuid"],
        ["not-a-date", IDENTIFIER],
        ["2026-01-01", IDENTIFIER],
    ],
)
def test_malformed_cursor_shape_and_types_are_422_before_query(client_without_database, payload):
    cursor = cursor_for(payload)
    with pytest.raises(HTTPException) as error:
        decode_cursor(cursor)
    assert error.value.status_code == 422 and error.value.detail == "Invalid feed cursor"
    response = client_without_database.get("/v1/feed", params={"cursor": cursor})
    assert response.status_code == 422 and response.json() == {"detail": "Invalid feed cursor"}


@pytest.mark.parametrize("cursor", ["not-a-cursor", "!!!!", "_w==", "ew=="])
def test_invalid_encoding_or_json_is_422(client_without_database, cursor):
    assert client_without_database.get("/v1/feed", params={"cursor": cursor}).status_code == 422


@pytest.mark.parametrize("date", [DATE, "2026-01-01T05:30:00+05:30"])
def test_valid_cursor_round_trip_preserves_ordering_timestamp_and_uuid(date):
    article = Article(id=uuid.UUID(IDENTIFIER), feed_at=datetime.fromisoformat(date))
    assert decode_cursor(encode_cursor(article)) == (article.feed_at, article.id)
