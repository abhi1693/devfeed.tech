"""Connection-owned raster logos share bounded uploads, safe fetching and R2 compensation."""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from enum import Enum
from typing import Final, Literal, Self, TypedDict

from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FeedError, _fetch
from devfeed_core.models import PartnerConnection
from devfeed_core.object_storage import R2Storage
from devfeed_core.partner_connection_logos import (
    CONNECTION_LOGO_SIZES,
    ConnectionLogoInput,
    connection_logo_prefix,
    connection_logo_variants,
)
from devfeed_core.urls import validate_public_url
from fastapi import HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from devfeed_http.image_uploads import MAX_BYTES, ImageUploadStorage

logger = logging.getLogger(__name__)


class _Unset(Enum):
    VALUE = "unset"


_UNSET: Final = _Unset.VALUE


class LogoVariant(TypedDict):
    width: int


class ManagedLogo(TypedDict, total=False):
    object_prefix: str
    cache_version: str
    source_url: str
    variants: list[LogoVariant]


class ConnectionLogoStorage(ImageUploadStorage):
    def __init__(self, provider: str, *, storage: R2Storage | None = None) -> None:
        super().__init__(
            connection_logo_prefix(provider),
            CONNECTION_LOGO_SIZES,
            square=True,
            fit="contain",
            storage=storage,
        )


@contextmanager
def connection_logo_guard(session: Session, provider: str) -> Iterator[None]:
    """Serialize fixed-key replacements through rollback without locking the catalog."""
    key = int.from_bytes(
        hashlib.sha256(f"connection-logo\0{provider}".encode()).digest()[:8],
        byteorder="big",
        signed=True,
    )
    with session.get_bind().engine.connect() as guard:
        try:
            locked = guard.scalar(select(func.pg_try_advisory_lock(key)))
            guard.commit()
        except BaseException:
            # Acquisition can succeed on PostgreSQL before a transport/commit error.
            # Discard that physical session rather than pooling an uncertain lock.
            guard.invalidate()
            raise
        if not locked:
            raise HTTPException(
                503,
                "Connection logo is being updated; try again shortly",
                headers={"Retry-After": "1"},
            )
        try:
            yield
        finally:
            try:
                if not guard.scalar(select(func.pg_advisory_unlock(key))):
                    raise RuntimeError("Connection logo guard was lost")
                guard.commit()
            except Exception:
                # A session lock must never be returned to the reusable pool.
                guard.invalidate()
                logger.exception("connection_logo_guard_release_failed")


class ConnectionLogoEdit:
    """Prepare outside locks, stage under a source guard, then publish in a short transaction."""

    def __init__(
        self,
        connection: PartnerConnection,
        *,
        source_url: str | None | Literal[_Unset.VALUE] = _UNSET,
        file: UploadFile | None = None,
    ) -> None:
        self.provider, self.file = connection.provider, file
        self.source_url: str | None = (
            None
            if file is not None
            else connection.logo_url
            if source_url is _UNSET
            else source_url
        )
        self.changed = file is not None or (
            source_url is not _UNSET
            and (
                source_url != connection.logo_url
                or bool(source_url and not connection_logo_variants(connection))
                or bool(source_url is None and connection.managed_logo)
            )
        )
        self.encoded: dict[int, bytes] | None = None
        self.base = ""
        self.asset: ManagedLogo = {}
        self.published_url: str | None = None

    def prepare(self) -> None:
        if not self.changed:
            return
        settings = get_settings()
        try:
            self.base = validate_public_url(settings.image_public_url or "")
            if not settings.image_storage_enabled or not self.base.startswith("https://"):
                raise ValueError("HTTPS image storage required")
        except ValueError:
            raise HTTPException(503, "Public HTTPS image storage is required for logos") from None
        if self.file is not None:
            if self.file.content_type not in {"image/jpeg", "image/png", "image/webp"}:
                raise HTTPException(415, "Choose a JPEG, PNG or WebP logo")
            body = self.file.file.read(MAX_BYTES + 1)
        elif self.source_url:
            try:
                result = _fetch(
                    self.source_url,
                    None,
                    None,
                    accept="image/jpeg,image/png,image/webp",
                    max_bytes=MAX_BYTES,
                    timeout=3,
                    deadline=time.monotonic() + 10,
                    before_request=ConnectionLogoInput.public_logo_url,
                )
                if (
                    result.status != 200
                    or not result.body
                    or (result.content_type or "").split(";", 1)[0].strip().lower()
                    not in {"image/jpeg", "image/png", "image/webp"}
                ):
                    raise ValueError("Unsupported logo response")
            except (FeedError, ValueError):
                raise HTTPException(
                    422, "Couldn’t retrieve a public JPEG, PNG or WebP logo"
                ) from None
            body = result.body
        else:
            return
        self.encoded, _widths = ConnectionLogoStorage(self.provider).normalize(body)

    @contextmanager
    def stage(self, session: Session, authorize: Callable[[], object]) -> Iterator[Self]:
        if not self.changed:
            yield self
            return
        with connection_logo_guard(session, self.provider):
            try:
                authorize()
                # Release the read transaction before any object-storage I/O.
                session.rollback()
                with ConnectionLogoStorage(self.provider) as objects:
                    if self.encoded is None:
                        objects.remove()
                    else:
                        objects.replace(self.encoded)
                        self.published_url = (
                            f"{self.base.rstrip('/')}/{objects.prefix}/64.webp?v={objects.revision}"
                        )
                        self.asset = {
                            "object_prefix": objects.prefix,
                            "cache_version": objects.revision,
                            "source_url": self.published_url,
                            "variants": [{"width": size} for size in CONNECTION_LOGO_SIZES],
                        }
                    try:
                        yield self
                    except BaseException:
                        # Catalog/account locks must be released BEFORE compensation.
                        # The independent session guard stays held until it completes.
                        session.rollback()
                        raise
            except BaseException:
                session.rollback()
                raise

    def apply(self, connection: PartnerConnection) -> None:
        if self.changed:
            connection.logo_url, connection.managed_logo = self.published_url, dict(self.asset)
            connection.revision = (connection.revision or 1) + 1
