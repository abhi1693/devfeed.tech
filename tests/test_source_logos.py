"""Publisher logo presentation and scheduling stay bounded and perform no remote reads."""

import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core.config import get_settings
from devfeed_core.models import Source
from devfeed_core.schemas import SourceRef
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_core.source_logos import backfill_source_logos, request_source_logo
from devfeed_core.topic_logos import SOURCE_LOGO_SIZES, logo_current
from sqlalchemy.dialects import postgresql


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("DEVFEED_IMAGE_STORAGE_ENABLED", "true")
    monkeypatch.setenv("DEVFEED_IMAGE_PUBLIC_URL", "https://images.test")
    get_settings.cache_clear()
    return Source(
        id=uuid.uuid4(),
        slug="publisher",
        name="Publisher",
        source_type="publisher",
        logo_url="https://publisher.test/logo.png",
        managed_logo={},
    )


def test_public_source_uses_managed_variants_and_keeps_editing_url(enabled):
    enabled.managed_logo = {
        "source_url": enabled.logo_url,
        "version": "v1",
        "variants": [
            {"width": size, "key": f"logos/hash/{size}.webp"} for size in SOURCE_LOGO_SIZES
        ],
    }
    assert logo_current(enabled)
    output = SourceRef.model_validate(enabled)
    assert output.logo_url == "https://images.test/logos/hash/64.webp"
    assert [v.width for v in output.logo_variants] == list(SOURCE_LOGO_SIZES)
    assert "managed_logo" not in output.model_dump()
    assert enabled.logo_url == "https://publisher.test/logo.png"
    enabled.logo_url = "https://publisher.test/new.png"
    assert not logo_current(enabled)
    assert SourceRef.model_validate(enabled).logo_url == output.logo_url
    enabled.logo_url = None
    assert SourceRef.model_validate(enabled).logo_url is None
    assert SourceRef.model_validate(enabled).logo_variants == []


def test_unprocessed_sources_retain_existing_fallback(enabled):
    assert SourceRef.model_validate(enabled).logo_url == enabled.logo_url
    assert SourceRef.model_validate(enabled).logo_variants == []


def test_source_request_uses_its_own_subject_and_coalesces(enabled):
    session = Mock()
    session.scalar.side_effect = [enabled, None, None]
    job = request_source_logo(session, enabled.id, automatic=True)
    assert job.source_id == enabled.id and job.topic_id is None and job.article_id is None
    assert job.operation == "source-logo" and job.storage_version == "v1"
    session.add.assert_called_once_with(job)
    session.scalar.side_effect = [enabled, job]
    assert request_source_logo(session, enabled.id).source_id == enabled.id
    session.scalar.side_effect = [enabled, None, uuid.uuid4()]
    assert request_source_logo(session, enabled.id, automatic=True) is None


def test_missing_disabled_and_cleared_sources_do_not_schedule(enabled, monkeypatch):
    session = Mock()
    session.scalar.return_value = None
    with pytest.raises(RecordNotFound, match="Source not found"):
        request_source_logo(session, uuid.uuid4())
    session.scalar.return_value = enabled
    enabled.logo_url = None
    assert request_source_logo(session, enabled.id) is None
    enabled.logo_url = "https://publisher.test/logo.png"
    monkeypatch.setenv("DEVFEED_IMAGE_STORAGE_ENABLED", "false")
    get_settings.cache_clear()
    assert request_source_logo(session, enabled.id) is None
    with pytest.raises(OperationConflict):
        backfill_source_logos(session)
    session.add.assert_not_called()


def test_source_backfill_filters_attempts_before_limit_and_skips_locked(enabled):
    session = Mock()
    session.scalars.return_value = SimpleNamespace(all=lambda: [])
    assert backfill_source_logos(session, 17) == []
    sql = str(session.scalars.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "article_image_jobs.source_id = sources.id" in sql
    assert "sources.managed_logo" in sql and "SKIP LOCKED" in sql and "LIMIT" in sql
    for invalid in [0, 501]:
        with pytest.raises(ValueError):
            backfill_source_logos(session, invalid)
