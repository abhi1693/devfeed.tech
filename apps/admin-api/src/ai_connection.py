"""Authenticated administration of the dedicated Codex account."""

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from devfeed_admin_api.auth import Admin
from devfeed_admin_api.codex_connection import CodexStatus, ConnectionProblem, DeviceLogin

router = APIRouter(prefix="/v1/admin/ai/connection", tags=["admin-ai-connection"])


class CancelLogin(BaseModel):
    login_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9-]+$")


@router.get("", response_model=CodexStatus, operation_id="admin_ai_connection")
async def status(request: Request, response: Response, admin: Admin):
    response.headers["Cache-Control"] = "no-store"
    return request.app.state.codex.status


@router.post("/login", response_model=DeviceLogin, operation_id="admin_ai_login")
async def login(request: Request, response: Response, admin: Admin):
    response.headers["Cache-Control"] = "no-store"
    try:
        return await request.app.state.codex.login()
    except ConnectionProblem as exc:
        # A reachable admin API with an unavailable Codex service is an operation
        # conflict. The UI reserves 503 for an unavailable admin service.
        raise HTTPException(409, str(exc)) from None


@router.post("/login/cancel", status_code=204, operation_id="admin_ai_login_cancel")
async def cancel(payload: CancelLogin, request: Request, admin: Admin):
    try:
        await request.app.state.codex.cancel(payload.login_id)
    except ConnectionProblem as exc:
        raise HTTPException(409, str(exc)) from None
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
