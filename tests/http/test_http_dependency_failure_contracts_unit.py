"""HTTP dependency failures expose bounded recovery responses and close stream resources."""

import json
import logging
from unittest.mock import Mock

import pytest
from devfeed_http.errors import register_error_handlers
from devfeed_http.health import readiness_response
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError


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
