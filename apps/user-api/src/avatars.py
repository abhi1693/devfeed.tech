"""Authenticated avatar upload/removal, serialized with all other profile edits."""

from contextlib import nullcontext
from typing import Annotated

from devfeed_core.cache import invalidate_public_cache
from devfeed_core.models import UserAccount
from devfeed_core.user_settings import UserProfileSettings
from devfeed_http.body_limits import BodyLimitedRoute
from devfeed_http.image_uploads import MAX_BYTES, MAX_REQUEST_BYTES
from devfeed_http.operation_policies import ExtensionTransportPolicy, ReaderGatewayPolicy
from fastapi import APIRouter, File, HTTPException, UploadFile

from devfeed_user_api.auth import User
from devfeed_user_api.avatar_storage import AvatarStorage
from devfeed_user_api.dependencies import DB
from devfeed_user_api.preferences import lock_account
from devfeed_user_api.profile import profile_value

router = APIRouter(
    prefix="/v1/user/settings/profile/avatar", tags=["user-profile"], route_class=BodyLimitedRoute
)


@router.post(
    "",
    response_model=UserProfileSettings,
    openapi_extra=ReaderGatewayPolicy(
        body_limit=MAX_REQUEST_BYTES,
        body_timeout_ms=10_000,
        extension=ExtensionTransportPolicy(timeout_ms=45_000),
    ).bounded_openapi_extra(rejection="image"),
)
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
    upload = AvatarStorage(account_id)
    variants, widths = upload.normalize(file.file.read(MAX_BYTES + 1))
    with upload as objects:
        urls = objects.replace(variants, widths=widths)
        account.profile = {
            **account.profile,
            "avatar_url": next(item["url"] for item in urls if item["width"] == 256),
            "avatar_variants": urls,
        }
        session.flush()
        result = profile_value(session, account)
        session.commit()
    invalidate_public_cache(reasons=("avatar",))
    return result


@router.delete(
    "",
    response_model=UserProfileSettings,
    openapi_extra=ReaderGatewayPolicy(
        extension=ExtensionTransportPolicy(timeout_ms=45_000)
    ).openapi_extra(),
)
def remove_avatar(user: User, session: DB):
    account_id = lock_account(session, user)
    account = session.get(UserAccount, account_id)
    assert account is not None
    with (
        AvatarStorage(account_id)
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
    invalidate_public_cache(reasons=("avatar",))
    return result
