"""Validated personal preferences; identities and permissions stay with OIDC."""

import hashlib
import json
import re
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from devfeed_core.models import AdminPreference, utcnow
from devfeed_core.urls import validate_public_url


class SettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProfileSettings(SettingsModel):
    display_name: str | None = Field(default=None, max_length=100)
    avatar_url: str | None = Field(default=None, max_length=2048)

    @field_validator("display_name", "avatar_url")
    @classmethod
    def empty_as_none(cls, value):
        return value or None

    @field_validator("avatar_url")
    @classmethod
    def public_avatar(cls, value):
        return validate_public_url(value) if value else None


class NotificationSettings(SettingsModel):
    show_badge: bool = True
    sound: bool = False


class FeedSettings(SettingsModel):
    view: Literal["cards", "compact"] = "cards"


class ThemeSettings(SettingsModel):
    theme: Literal["system", "light", "dark"] = "system"


class AppearanceSettings(ThemeSettings):
    density: Literal["comfortable", "compact"] = "comfortable"
    reduce_motion: bool = False
    timezone: str = Field(default="local", max_length=100)
    time_format: Literal["system", "12", "24"] = "system"
    date_format: Literal["locale", "iso", "day-first", "month-first"] = "locale"

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        if value != "local":
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError("Choose a valid time zone") from None
        return value


class DefaultSettings(SettingsModel):
    refresh_seconds: Literal[0, 5, 10, 15, 30, 60] = 10
    page_size: Literal[10, 25, 50, 100] = 25
    remember_columns: bool = True
    remember_filters: bool = True
    remember_sort: bool = True
    landing_page: Literal["/", "/content/articles", "/taxonomy/topics", "/jobs/analysis"] = "/"
    overview_days: Literal[7, 30] = 30


SettingKey = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_-]{1,80}$")]
SettingValue = Annotated[str, StringConstraints(max_length=500)]


class TableSettings(SettingsModel):
    columns: dict[SettingKey, bool] = Field(default_factory=dict, max_length=100)
    query: dict[SettingKey, SettingValue] = Field(default_factory=dict, max_length=30)


class TableSettingsPatch(SettingsModel):
    columns: dict[SettingKey, bool] | None = Field(default=None, max_length=100)
    query: dict[SettingKey, SettingValue] | None = Field(default=None, max_length=30)


class UserSettings(SettingsModel):
    profile: ProfileSettings = Field(default_factory=ProfileSettings)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)
    appearance: AppearanceSettings = Field(default_factory=AppearanceSettings)
    defaults: DefaultSettings = Field(default_factory=DefaultSettings)
    tables: dict[str, TableSettings] = Field(default_factory=dict)


def owner_key(*, issuer: str, subject: str, organization_id: str) -> str:
    identity = json.dumps([issuer, organization_id, subject], separators=(",", ":"))
    return hashlib.sha256(identity.encode()).hexdigest()


def read_settings(session: Session, owner: str) -> UserSettings:
    row = session.get(AdminPreference, owner)
    return UserSettings.model_validate(row.settings if row else {})


def write_settings(
    session: Session, owner: str, section: str, value: dict, *, table: str | None = None
) -> UserSettings:
    # Serialize updates for this account only. A saved column choice cannot
    # overwrite a simultaneous profile/appearance change from another tab.
    session.execute(
        insert(AdminPreference)
        .values(owner_key=owner, settings={}, updated_at=utcnow())
        .on_conflict_do_nothing(index_elements=[AdminPreference.owner_key])
    )
    row = session.scalar(
        select(AdminPreference)
        .where(AdminPreference.owner_key == owner)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert row is not None
    settings = dict(row.settings)
    if table:
        tables = dict(settings.get("tables", {}))
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", table) or (
            table not in tables and len(tables) >= 64
        ):
            raise ValueError("Invalid table preference key or too many saved tables")
        tables[table] = {**tables.get(table, {}), **value}
        settings["tables"] = tables
    else:
        settings[section] = value
    result = UserSettings.model_validate(settings)
    row.settings = result.model_dump(mode="json")
    row.updated_at = utcnow()
    session.flush()
    return result
