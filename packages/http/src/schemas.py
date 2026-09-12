"""HTTP contracts shared by the public, administrator and reader APIs."""

from typing import Any, Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok"]


class UnhealthyResponse(BaseModel):
    status: Literal["migration_required", "unavailable"]


class VersionResponse(BaseModel):
    version: str
    required_schema_revision: str


class ValidationIssue(BaseModel):
    loc: list[str | int]
    msg: str
    type: str


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    detail: str | ErrorDetail | list[ValidationIssue]


class FeedValidationResponse(BaseModel):
    detail: str
    upstream_status: int | None
    retryable: bool


class OIDCCallbackQuery(BaseModel):
    # Protocol failures must reach the callback's state/cookie cleanup and redirect.
    # Keep length and repeated-parameter checks inside that flow, not a JSON 422.
    state: str | None = None
    code: str | None = None
    error: str | None = None
    iss: str | None = None


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    "4XX": {"model": ErrorResponse, "description": "Invalid or unauthorized request"},
    "5XX": {"model": ErrorResponse, "description": "Service or dependency unavailable"},
    422: {"model": ErrorResponse, "description": "Request validation failed"},
}
