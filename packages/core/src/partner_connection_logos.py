"""Managed import-platform branding; public reads never expose remote logo sources."""

import hashlib

from pydantic import Field, field_validator

from devfeed_core.config import get_settings
from devfeed_core.schemas import InputModel, ORMModel
from devfeed_core.urls import validate_public_url

CONNECTION_LOGO_SIZES = (16, 32, 64, 96)


class ConnectionLogoInput(InputModel):
    logo_url: str | None = Field(default=None, max_length=2048)

    @field_validator("logo_url")
    @classmethod
    def public_logo_url(cls, value):
        if value is None:
            return None
        value = validate_public_url(value)
        if not value.startswith("https://"):
            raise ValueError("Connection logos require a public HTTPS image URL")
        return value


class ConnectionLogoUpdate(ConnectionLogoInput):
    logo_url: str | None = Field(max_length=2048)
    expected_revision: int = Field(ge=1, strict=True)


class ConnectionLogoVariant(ORMModel):
    url: str
    width: int


class PlacementPlatform(ORMModel):
    name: str
    logo_url: str | None
    logo_variants: list[ConnectionLogoVariant] = Field(default_factory=list)


def connection_logo_prefix(provider):
    return f"connection-logos/v1/{hashlib.sha256(provider.encode()).hexdigest()}"


def connection_logo_variants(connection):
    asset = connection.managed_logo or {}
    base = (get_settings().image_public_url or "").rstrip("/")
    prefix = connection_logo_prefix(connection.provider)
    if (
        not connection.logo_url
        or not base.startswith("https://")
        or asset.get("source_url") != connection.logo_url
        or asset.get("object_prefix") != prefix
        or not asset.get("cache_version")
    ):
        return []
    variants = asset.get("variants", [])
    if {item.get("width") for item in variants} != set(CONNECTION_LOGO_SIZES):
        return []
    return [
        ConnectionLogoVariant(
            width=size,
            url=f"{base}/{prefix}/{size}.webp?v={asset['cache_version']}",
        )
        for size in CONNECTION_LOGO_SIZES
    ]


def connection_platform(connection):
    variants = connection_logo_variants(connection)
    return PlacementPlatform(
        name=connection.name or connection.provider.replace("-", " ").title(),
        logo_url=next((item.url for item in variants if item.width >= 64), None),
        logo_variants=variants,
    )
