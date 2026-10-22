"""HTTP dependency failures expose bounded recovery responses and close stream resources."""

import asyncio
import json
import logging
import time
from unittest.mock import Mock

import httpx
import pytest
from devfeed_http.errors import register_error_handlers
from devfeed_http.health import readiness_response
from devfeed_http.inbox import InboxConnection, InboxProxy
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from test_inbox_router import Chunks, operation


@pytest.mark.parametrize("admin", [False, True])
@pytest.mark.parametrize("conflict", [False, True])
def test_storage_errors_keep_http_recovery_meaning_without_exposing_queries_or_credentials(
    admin, conflict
):
    logger, app = Mock(spec=logging.Logger), FastAPI()
    register_error_handlers(app, logger, admin=admin)
    failure = (
        IntegrityError(
            "private SQL", {"token": "private credential"}, ValueError("private constraint")
        )
        if conflict
        else SQLAlchemyError("private endpoint and schema")
    )

    @app.get("/operation")
    def operation():
        raise failure

    with TestClient(app) as client:
        response = client.get("/operation")
    assert response.status_code == (409 if conflict else 503)
    assert "private" not in response.text
    if conflict:
        assert response.json() == {"detail": "Conflicting or invalid record"}
        if admin:
            logger.warning.assert_not_called()
        else:
            logger.warning.assert_called_once_with(
                "request_conflict", extra={"error_type": "IntegrityError"}
            )
    else:
        assert response.json() == {
            "detail": "Database unavailable or migrations required"
            if admin
            else (
                "Database unavailable or schema out of date. "
                "Check connectivity and apply pending migrations."
            )
        }
        logger.error.assert_called_once_with(
            "admin_database_unavailable" if admin else "database_operation_failed",
            extra={"error_type": "SQLAlchemyError"},
        )
        if not admin:
            assert response.headers["cache-control"] == "no-store"


def test_readiness_without_a_logger_still_closes_the_database_session_and_reports_redis_failure():
    session, redis = Mock(), Mock()
    redis.ping.side_effect = ConnectionError("private redis endpoint")
    response = readiness_response(
        session, redis, revision_reader=lambda _: "current", schema_revision="current"
    )
    assert response.status_code == 503 and json.loads(response.body) == {"status": "unavailable"}
    session.close.assert_called_once_with()
    assert "private" not in response.body.decode()


def test_inbox_client_uses_bounded_timeouts_without_environment_proxy_or_redirect_inheritance():
    async def run():
        async with InboxProxy.http_client() as client:
            assert client.trust_env is False and client.follow_redirects is False
            assert client.timeout == httpx.Timeout(30, connect=3, write=5, pool=3)

    asyncio.run(run())


def test_inbox_event_transport_failure_preserves_delivered_events_and_closes_response_and_client():
    async def run():
        stream = Chunks(
            [b"event: hint\ndata: {}\n\n"], failure=httpx.ReadError("private stream endpoint")
        )
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))
        )
        connection = InboxConnection(client)
        connection.upstream = await client.send(
            client.build_request("GET", "https://chimely.example/v1/inbox/stream"), stream=True
        )
        output = [
            chunk
            async for chunk in InboxProxy().events(
                connection, operation("Stream"), int(time.time()) + 60
            )
        ]
        assert output == [b": connected\n\n", b"event: hint\ndata: {}\n\n"]
        assert connection.closed and connection.upstream.is_closed and client.is_closed
        assert stream.closes == 1 and all(b"private" not in chunk for chunk in output)

    asyncio.run(run())
