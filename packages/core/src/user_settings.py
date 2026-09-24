"""Validated personal preferences; identities and permissions stay with OIDC."""

import hashlib
import json
import re
import uuid
from datetime import date
from typing import Annotated, Literal, get_args
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from devfeed_core.models import AdminPreference, utcnow
from devfeed_core.schemas import ContentType, TopicKind
from devfeed_core.urls import validate_public_url
from devfeed_core.usernames import normalize_username


class SettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class UserStackItem(SettingsModel):
    topic_id: uuid.UUID
    section: Literal["primary", "hobby", "learning", "past"] = "primary"
    since_year: int | None = Field(default=None, ge=1900, le=2100)

    @field_validator("since_year")
    @classmethod
    def not_future(cls, value):
        if value is not None and value > utcnow().year:
            raise ValueError("Using-since year cannot be in the future")
        return value


class UserStackOut(UserStackItem):
    name: str
    slug: str
    kind: TopicKind
    logo_url: str | None
    status: str


class ProfileVisibility(SettingsModel):
    public: bool = True
    # Retained for older clients/stored profiles; these sections are always included.
    location: bool = Field(default=True, deprecated=True)
    stack: bool = Field(default=True, deprecated=True)
    heatmap: bool = Field(default=True, deprecated=True)
    achievements: bool = False

    @field_validator("location", "stack", "heatmap")
    @classmethod
    def always_include_profile_sections(cls, value: bool) -> bool:
        return True


class ProfileLink(SettingsModel):
    url: str = Field(max_length=2048)
    label: str | None = Field(default=None, max_length=80)

    _url = field_validator("url")(validate_public_url)

    @field_validator("label")
    @classmethod
    def empty_label(cls, value):
        return value or None


class UserReadingStreak(SettingsModel):
    current_days: int = 0
    longest_days: int = 0
    total_days: int = 0
    last_read_date: date | None = None


class UserReadingHeatmapDay(SettingsModel):
    date: date
    article_count: int = Field(ge=0)


class UserReadingHeatmap(SettingsModel):
    year: int
    timezone: Literal["UTC"] = "UTC"
    metric: Literal["distinct_article_opens"] = "distinct_article_opens"
    days: list[UserReadingHeatmapDay] = Field(default_factory=list)


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


class UserProfileUpdate(ProfileSettings):
    """User-owned profile fields, including optional public-profile details."""

    username: str | None = Field(default=None, max_length=30)
    bio: str | None = Field(default=None, max_length=160)
    location: str | None = Field(default=None, max_length=100)
    about: str | None = Field(default=None, max_length=5000)
    links: list[ProfileLink] = Field(default_factory=list, max_length=20)
    stack: list[UserStackItem] = Field(default_factory=list, max_length=100)
    visibility: ProfileVisibility = Field(default_factory=ProfileVisibility)

    @field_validator("username")
    @classmethod
    def empty_username_as_none(cls, value):
        return normalize_username(value)

    @field_validator("bio")
    @classmethod
    def empty_bio_as_none(cls, value):
        return value or None

    @field_validator("location")
    @classmethod
    def empty_location_as_none(cls, value):
        return value or None

    @field_validator("about")
    @classmethod
    def empty_about_as_none(cls, value):
        return value or None

    @field_validator("links", mode="before")
    @classmethod
    def legacy_links(cls, value):
        if isinstance(value, list):
            return [{"url": link} if isinstance(link, str) else link for link in value]
        return value

    @field_validator("links")
    @classmethod
    def public_unique_links(cls, value):
        if len(value) != len({link.url for link in value}):
            raise ValueError("Links must be unique")
        return value

    @field_validator("stack")
    @classmethod
    def unique_stack(cls, value):
        if len(value) != len({item.topic_id for item in value}):
            raise ValueError("Stack topics must be unique")
        return value


class UserProfileSettings(ProfileSettings):
    username: str | None = None
    bio: str | None = None
    location: str | None = None
    about: str | None = None
    links: list[ProfileLink] = Field(default_factory=list)
    stack: list[UserStackOut] = Field(default_factory=list)
    visibility: ProfileVisibility = Field(default_factory=ProfileVisibility)
    reading_streak: UserReadingStreak = Field(default_factory=UserReadingStreak)


class PublicUserProfile(ProfileSettings):
    username: str
    bio: str | None = None
    about: str | None = None
    links: list[ProfileLink] = Field(default_factory=list)
    location: str | None = None
    stack: list[UserStackOut] | None = None
    reading_streak: UserReadingStreak | None = None


class NotificationSettings(SettingsModel):
    show_badge: bool = True
    sound: bool = False


# Base language codes supported by the article language detector.
LanguageCode = Literal[
    "en",
    "af",
    "sq",
    "ar",
    "hy",
    "az",
    "eu",
    "be",
    "bn",
    "nb",
    "bs",
    "bg",
    "ca",
    "zh",
    "hr",
    "cs",
    "da",
    "nl",
    "eo",
    "et",
    "fi",
    "fr",
    "lg",
    "ka",
    "de",
    "el",
    "gu",
    "he",
    "hi",
    "hu",
    "is",
    "id",
    "ga",
    "it",
    "ja",
    "kk",
    "ko",
    "la",
    "lv",
    "lt",
    "mk",
    "ms",
    "mi",
    "mr",
    "mn",
    "nn",
    "fa",
    "pl",
    "pt",
    "pa",
    "ro",
    "ru",
    "sr",
    "sn",
    "sk",
    "sl",
    "so",
    "st",
    "es",
    "sw",
    "sv",
    "tl",
    "ta",
    "te",
    "th",
    "ts",
    "tn",
    "tr",
    "uk",
    "ur",
    "vi",
    "cy",
    "xh",
    "yo",
    "zu",
]


def default_languages() -> list[LanguageCode]:
    return ["en"]


class FeedSettings(SettingsModel):
    view: Literal["cards", "compact"] = "cards"
    languages: list[LanguageCode] = Field(
        default_factory=default_languages, min_length=1, max_length=75
    )

    @field_validator("languages")
    @classmethod
    def unique_languages(cls, value):
        return sorted(set(value))

    content_types: list[ContentType] = Field(
        default_factory=lambda: list(get_args(ContentType)), min_length=1, max_length=6
    )

    @field_validator("content_types")
    @classmethod
    def unique_types(cls, value):
        return [kind for kind in get_args(ContentType) if kind in value]


class UserAppearanceSettings(SettingsModel):
    theme: Literal["system", "light", "dark"] = "system"

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


class AppearanceSettings(UserAppearanceSettings):
    density: Literal["comfortable", "compact"] = "comfortable"
    reduce_motion: bool = False


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
