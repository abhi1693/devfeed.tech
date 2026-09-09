"""Personal settings are always scoped to the authenticated account."""

from devfeed_core.user_settings import (
    AppearanceSettings,
    DefaultSettings,
    NotificationSettings,
    ProfileSettings,
    TableSettingsPatch,
    UserSettings,
    owner_key,
    read_settings,
    write_settings,
)
from fastapi import APIRouter, HTTPException

from devfeed_admin_api.auth import Admin
from devfeed_admin_api.dependencies import DB

router = APIRouter(prefix="/v1/admin/settings", tags=["admin-settings"])


def account(admin):
    return owner_key(
        issuer=admin.issuer, subject=admin.subject, organization_id=admin.organization_id
    )


def save(session, admin, section, body):
    result = write_settings(session, account(admin), section, body.model_dump(mode="json"))
    session.commit()
    return result


@router.get("", response_model=UserSettings, operation_id="admin_settings_get")
def get(admin: Admin, session: DB):
    return read_settings(session, account(admin))


@router.put("/profile", response_model=UserSettings, operation_id="admin_settings_profile")
def profile(body: ProfileSettings, admin: Admin, session: DB):
    return save(session, admin, "profile", body)


@router.put(
    "/notifications", response_model=UserSettings, operation_id="admin_settings_notifications"
)
def notifications(body: NotificationSettings, admin: Admin, session: DB):
    return save(session, admin, "notifications", body)


@router.put("/appearance", response_model=UserSettings, operation_id="admin_settings_appearance")
def appearance(body: AppearanceSettings, admin: Admin, session: DB):
    return save(session, admin, "appearance", body)


@router.put("/defaults", response_model=UserSettings, operation_id="admin_settings_defaults")
def defaults(body: DefaultSettings, admin: Admin, session: DB):
    return save(session, admin, "defaults", body)


@router.patch("/tables/{table}", response_model=UserSettings, operation_id="admin_settings_table")
def table_settings(table: str, body: TableSettingsPatch, admin: Admin, session: DB):
    try:
        result = write_settings(
            session, account(admin), "tables", body.model_dump(exclude_none=True), table=table
        )
    except ValueError:
        raise HTTPException(422, "Invalid table preference key or too many saved tables") from None
    session.commit()
    return result


@router.delete("/tables", response_model=UserSettings, operation_id="admin_settings_tables_reset")
def reset_tables(admin: Admin, session: DB):
    result = write_settings(session, account(admin), "tables", {})
    session.commit()
    return result
