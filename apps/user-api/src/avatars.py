"""Authenticated avatar upload/removal, serialized with all other profile edits."""

from contextlib import nullcontext
from typing import Annotated

from devfeed_core.cache import invalidate_public_cache
from devfeed_core.models import UserAccount
from devfeed_core.user_settings import UserProfileSettings
from fastapi import APIRouter, File, HTTPException, UploadFile
from starlette.responses import JSONResponse

from devfeed_user_api.auth import User
from devfeed_user_api.avatar_storage import (
    MAX_BYTES,
    MAX_REQUEST_BYTES,
    AvatarObjects,
    normalize_avatar,
)
from devfeed_user_api.dependencies import DB
from devfeed_user_api.preferences import lock_account
from devfeed_user_api.profile import profile_value

router = APIRouter(prefix="/v1/user/settings/profile/avatar", tags=["user-profile"])


class AvatarUploadBodyLimit:
    """Bound even chunked multipart bodies before the form parser allocates/spools."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != router.prefix
        ):
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared = 0
        if declared > MAX_REQUEST_BYTES:
            return await self.reject(scope, receive, send)
        chunks = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > MAX_REQUEST_BYTES:
                return await self.reject(scope, receive, send)
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        pending = True

        async def replay():
            nonlocal pending
            if pending:
                pending = False
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    async def reject(self, scope, receive, send):
        await JSONResponse({"detail": "Choose an image under 5 MB"}, status_code=413)(
            scope, receive, send
        )


@router.post("", response_model=UserProfileSettings)
def upload_avatar(
    file: Annotated[UploadFile, File(description="JPEG, PNG or WebP, up to 5 MB")],
    user: User,
    session: DB,
):
    account_id = lock_account(session, user)
    account = session.get(UserAccount, account_id)
    assert account is not None
    if file.content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(415, "Choose a JPEG, PNG or WebP image")
    variants = normalize_avatar(file.file.read(MAX_BYTES + 1))
    with AvatarObjects(account_id) as objects:
        urls = objects.replace(variants)
        account.profile = {
            **account.profile,
            "avatar_url": next(item["url"] for item in urls if item["width"] == 256),
            "avatar_variants": urls,
        }
        session.flush()
        result = profile_value(session, account)
        session.commit()
    invalidate_public_cache()
    return result


@router.delete("", response_model=UserProfileSettings)
def remove_avatar(user: User, session: DB):
    account_id = lock_account(session, user)
    account = session.get(UserAccount, account_id)
    assert account is not None
    with (
        AvatarObjects(account_id)
        if account.profile.get("avatar_variants")
        else nullcontext() as objects
    ):
        if objects is not None:
            objects.remove()
        values = {**account.profile, "avatar_url": None, "avatar_variants": []}
        account.profile = values
        session.flush()
        result = profile_value(session, account)
        session.commit()
    invalidate_public_cache()
    return result
