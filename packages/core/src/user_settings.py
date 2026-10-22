"""Validated personal preferences; identities and permissions stay with OIDC."""

import hashlib
import json
import re
import uuid
from datetime import date
from typing import Annotated, Literal, get_args
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
)
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from devfeed_core.models import AdminPreference, utcnow
from devfeed_core.partner_audiences import ExperienceLevel
from devfeed_core.schemas import ContentType, DatabaseText, ImageVariant, TextInput, TopicKind
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
    logo_variants: list[ImageVariant] = Field(default_factory=list)
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


DevCardStat = Literal["current_streak", "longest_streak", "total_reading_days"]


def default_dev_card_stats() -> list[DevCardStat]:
    return ["current_streak", "longest_streak", "total_reading_days"]


class DevCardSettings(SettingsModel):
    """Content a user chooses to show on their public Dev Card."""

    motion: Literal["static", "animated"] = "animated"
    theme: Literal["classic", "terminal", "aurora", "minimal"] = "classic"
    accent: Literal["default", "teal", "violet", "blue", "amber", "rose"] = "default"
    technologies: list[uuid.UUID] | None = None
    stats: list[DevCardStat] = Field(default_factory=default_dev_card_stats, max_length=3)

    @field_validator("technologies", "stats")
    @classmethod
    def unique_card_content(cls, value):
        if value is None:
            return value
        if len(value) != len(set(value)):
            raise ValueError("Dev Card selections must be unique")
        return value


class ProfileLink(SettingsModel):
    url: str = Field(max_length=2048)
    label: TextInput | None = Field(default=None, max_length=80)

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


class UserReadingWeek(SettingsModel):
    today: date
    timezone: Literal["UTC"] = "UTC"
    days: list[UserReadingHeatmapDay] = Field(min_length=7, max_length=7)


class ProfileSettings(SettingsModel):
    display_name: TextInput | None = Field(default=None, max_length=100)
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
    bio: TextInput | None = Field(default=None, max_length=160)
    location: TextInput | None = Field(default=None, max_length=100)
    about: TextInput | None = Field(default=None, max_length=5000)
    experience_level: ExperienceLevel | None = None
    links: list[ProfileLink] = Field(default_factory=list, max_length=20)
    stack: list[UserStackItem] = Field(default_factory=list, max_length=100)
    visibility: ProfileVisibility = Field(default_factory=ProfileVisibility)
    dev_card: DevCardSettings = Field(default_factory=DevCardSettings)

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


class AvatarVariant(SettingsModel):
    width: Literal[32, 64, 128, 256, 512]
    url: str = Field(max_length=2048)


class UserProfileSettings(ProfileSettings):
    avatar_variants: list[AvatarVariant] = Field(default_factory=list, max_length=5)
    username: str | None = None
    bio: str | None = None
    location: str | None = None
    about: str | None = None
    experience_level: ExperienceLevel | None = None
    links: list[ProfileLink] = Field(default_factory=list)
    stack: list[UserStackOut] = Field(default_factory=list)
    visibility: ProfileVisibility = Field(default_factory=ProfileVisibility)
    dev_card: DevCardSettings = Field(default_factory=DevCardSettings)
    reading_streak: UserReadingStreak = Field(default_factory=UserReadingStreak)


class PublicUserProfile(ProfileSettings):
    avatar_variants: list[AvatarVariant] = Field(default_factory=list, max_length=5)
    username: str
    bio: str | None = None
    about: str | None = None
    links: list[ProfileLink] = Field(default_factory=list)
    location: str | None = None
    stack: list[UserStackOut] | None = None
    reading_streak: UserReadingStreak | None = None
    dev_card: DevCardSettings = Field(default_factory=DevCardSettings)


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


class DefaultColumnFeed(SettingsModel):
    kind: Literal["latest", "recommended"]


class SelectionColumnFeed(SettingsModel):
    kind: Literal["source", "topic", "tag"]
    id: uuid.UUID


class ReaderColumn(SettingsModel):
    id: uuid.UUID
    feed: Annotated[DefaultColumnFeed | SelectionColumnFeed, Field(discriminator="kind")]


MAX_READER_COLUMNS = 12


def reader_column_feed_identity(column: ReaderColumn) -> tuple[str, uuid.UUID | None]:
    feed = column.feed
    return feed.kind, feed.id if isinstance(feed, SelectionColumnFeed) else None


def unique_reader_columns(value: list[ReaderColumn]) -> list[ReaderColumn]:
    if len(value) != len({column.id for column in value}):
        raise ValueError("Reader column IDs must be unique")
    if len(value) != len({reader_column_feed_identity(column) for column in value}):
        raise ValueError("Each feed can appear only once in a workspace")
    return value


class FeedSettings(SettingsModel):
    view: Literal["cards", "compact", "stacked", "columns"] = "cards"
    columns: list[ReaderColumn] = Field(default_factory=list, max_length=MAX_READER_COLUMNS)

    _unique_columns = field_validator("columns")(unique_reader_columns)

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


MAX_READER_FEED_VIEWS = 256
_MAIN_FEED_VIEW_SCOPES = frozenset({"my_feed", "latest", "trending", "read_later", "search"})


def normalize_feed_view_scope(value: str) -> str:
    """Identify feeds by their meaning and stable IDs, independent of browser URLs."""
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        raise ValueError("Choose a supported feed")
    if value in _MAIN_FEED_VIEW_SCOPES:
        return value
    kind, separator, identifier = value.partition(":")
    if separator and kind == "content" and identifier in get_args(ContentType):
        return value
    if separator and kind in {"topic", "source", "tag"} and identifier.isascii():
        try:
            return f"{kind}:{uuid.UUID(identifier)}"
        except ValueError:
            pass
    raise ValueError("Choose a supported feed")


FeedViewScope = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64),
    AfterValidator(normalize_feed_view_scope),
]


class FeedViewSettings(SettingsModel):
    view: Literal["cards", "compact", "stacked", "columns"]


def _feed_view(value: object) -> FeedViewSettings | None:
    try:
        return FeedViewSettings.model_validate({"view": value})
    except ValidationError:
        return None


def reader_feed_view(value: object, feed: str) -> FeedViewSettings:
    """Recover one scope without rewriting unknown or malformed saved preferences."""
    scope = normalize_feed_view_scope(feed)
    stored = value if isinstance(value, dict) else {}
    views = stored.get("feed_views")
    selected = (
        (_feed_view(views.get(scope)) if isinstance(views, dict) else None)
        or _feed_view(stored.get("feed_view_default"))
        or _feed_view(stored.get("view"))
        or FeedViewSettings(view="cards")
    )
    if selected.view == "columns" and scope != "my_feed":
        return FeedViewSettings(view="cards")
    return selected


def update_reader_feed_view(value: object, feed: str, payload: FeedViewSettings) -> dict:
    """Update just one layout and freeze the legacy default for untouched feeds."""
    scope = normalize_feed_view_scope(feed)
    if payload.view == "columns" and scope != "my_feed":
        raise ValueError("Columns is available only on My feed")
    stored = dict(value) if isinstance(value, dict) else {}
    previous = stored.get("feed_views")
    views = dict(previous) if isinstance(previous, dict) else {}
    if scope not in views and len(views) >= MAX_READER_FEED_VIEWS:
        raise ValueError("You can customize up to 256 feeds")
    if _feed_view(stored.get("feed_view_default")) is None:
        default = _feed_view(stored.get("view")) or FeedViewSettings(view="cards")
        stored["feed_view_default"] = default.view
    views[scope] = payload.view
    stored["feed_views"] = views
    return stored


def _normalize_reader_columns(value: object) -> list[ReaderColumn]:
    """Recover valid unique columns without parsing unrelated feed preferences."""
    columns = []
    seen = set()
    seen_feeds = set()
    if isinstance(value, list):
        for candidate in value:
            try:
                column = ReaderColumn.model_validate(candidate)
            except ValidationError:
                continue
            identity = reader_column_feed_identity(column)
            if column.id in seen or identity in seen_feeds:
                continue
            columns.append(column)
            seen.add(column.id)
            seen_feeds.add(identity)
            if len(columns) == MAX_READER_COLUMNS:
                break
    return columns


def normalize_feed_settings(value: object) -> FeedSettings:
    """Read saved preferences defensively without changing their stored representation."""
    stored = value if isinstance(value, dict) else {}
    normalized = {}
    for field in ("view", "languages", "content_types"):
        if field not in stored:
            continue
        try:
            parsed = FeedSettings.model_validate({field: stored[field]})
        except ValidationError:
            continue
        normalized[field] = getattr(parsed, field)
    normalized["columns"] = _normalize_reader_columns(stored.get("columns"))
    return FeedSettings.model_validate(normalized)


class ReaderColumnsState(SettingsModel):
    initialized: bool
    columns: list[ReaderColumn] = Field(max_length=MAX_READER_COLUMNS)

    _unique_columns = field_validator("columns")(unique_reader_columns)


class ReaderColumnsUpdate(SettingsModel):
    columns: list[ReaderColumn] = Field(max_length=MAX_READER_COLUMNS)
    mode: Literal["replace", "initialize", "restore_empty"] = "replace"

    _unique_columns = field_validator("columns")(unique_reader_columns)


ReaderWorkspace = Literal["latest", "my_feed"]


class ReaderColumnStarters(SettingsModel):
    feeds: list[SelectionColumnFeed] = Field(default_factory=list, max_length=4)


def reader_columns_state(
    value: object, workspace: ReaderWorkspace = "latest"
) -> ReaderColumnsState:
    """Keep intentional empty workspaces distinct without extending FeedSettings receipts."""
    stored = value if isinstance(value, dict) else {}
    if workspace == "my_feed" and (
        "my_feed_columns" in stored or "my_feed_columns_initialized" in stored
    ):
        stored = {
            field: stored[f"my_feed_{field}"]
            for field in ("columns", "columns_initialized")
            if f"my_feed_{field}" in stored
        }
    columns = _normalize_reader_columns(stored.get("columns"))
    initialized = (
        bool(columns)
        or stored.get("columns_initialized") is True
        or ("columns_initialized" not in stored and isinstance(stored.get("columns"), list))
    )
    return ReaderColumnsState(initialized=initialized, columns=columns)


def preserve_my_feed_columns(value: object) -> dict:
    """Freeze the prior shared workspace before a Latest-only change can replace it."""
    stored = dict(value) if isinstance(value, dict) else {}
    if "my_feed_columns" not in stored and "my_feed_columns_initialized" not in stored:
        previous = reader_columns_state(stored)
        stored["my_feed_columns"] = [column.model_dump(mode="json") for column in previous.columns]
        stored["my_feed_columns_initialized"] = previous.initialized
    return stored


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
SettingValue = Annotated[DatabaseText, StringConstraints(max_length=500)]


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
