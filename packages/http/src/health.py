"""Shared readiness response for independently deployed HTTP services."""

import logging
from collections.abc import Callable

from devfeed_core.db import database_revision
from devfeed_core.version import BACKWARD_COMPATIBLE_SCHEMA_REVISIONS, SCHEMA_REVISION
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from devfeed_http.schemas import UnhealthyResponse


def readiness_response(
    session,
    redis,
    logger: logging.Logger | None = None,
    *,
    revision_reader: Callable = database_revision,
    schema_revision: str = SCHEMA_REVISION,
):
    try:
        revision = revision_reader(session)
        session.close()
        if revision != schema_revision and revision not in BACKWARD_COMPATIBLE_SCHEMA_REVISIONS:
            return JSONResponse(
                status_code=503,
                content=UnhealthyResponse(status="migration_required").model_dump(),
            )
        redis.ping()
    except (SQLAlchemyError, RedisError) as exc:
        if logger is not None:
            logger.warning("readiness_failed", extra={"error_type": type(exc).__name__})
        return JSONResponse(
            status_code=503, content=UnhealthyResponse(status="unavailable").model_dump()
        )
    return {"status": "ok"}
