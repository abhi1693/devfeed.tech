"""Bounded error responses shared by the public and administrative APIs."""

import logging

from devfeed_core.services import OperationConflict, RecordNotFound
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from devfeed_http.schemas import ErrorResponse, ValidationIssue


def register_error_handlers(app: FastAPI, logger: logging.Logger, *, admin: bool = False) -> None:
    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        # The middleware logs the safe stack. ServerErrorMiddleware sends this response
        # outside that middleware, so attach the same request ID explicitly on 500s.
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(detail="Internal server error").model_dump(),
            headers={
                "X-Request-ID": request.state.request_id,
                **({"Cache-Control": "no-store"} if admin else {}),
            },
        )

    @app.exception_handler(IntegrityError)
    async def conflict(request: Request, exc: IntegrityError):
        if not admin:
            logger.warning("request_conflict", extra={"error_type": type(exc).__name__})
        return JSONResponse(
            status_code=409,
            content=ErrorResponse(detail="Conflicting or invalid record").model_dump(),
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_unavailable(request: Request, exc: SQLAlchemyError):
        logger.error(
            "admin_database_unavailable" if admin else "database_operation_failed",
            extra={"error_type": type(exc).__name__},
        )
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(
                detail="Database unavailable or migrations required"
                if admin
                else (
                    "Database unavailable or schema out of date. "
                    "Check connectivity and apply pending migrations."
                )
            ).model_dump(),
            headers={} if admin else {"Cache-Control": "no-store"},
        )

    @app.exception_handler(RecordNotFound)
    async def unknown_record(request: Request, exc: RecordNotFound):
        return JSONResponse(status_code=404, content=ErrorResponse(detail=str(exc)).model_dump())

    @app.exception_handler(OperationConflict)
    async def conflicting_operation(request: Request, exc: OperationConflict):
        return JSONResponse(status_code=409, content=ErrorResponse(detail=str(exc)).model_dump())

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request: Request, exc: RequestValidationError):
        if not admin:
            logger.warning("request_validation_failed", extra={"error_type": type(exc).__name__})
        # Keep validation responses bounded rather than echoing raw input.
        errors = [
            ValidationIssue(loc=list(error["loc"]), msg=error["msg"], type=error["type"])
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content=ErrorResponse(detail=errors).model_dump())
