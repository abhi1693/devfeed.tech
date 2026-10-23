"""Source readiness settings and persisted lifecycle facts."""

import uuid
from datetime import UTC, datetime, timedelta

from devfeed_core.config import Settings
from devfeed_core.models import (
    Source,
    SourceEnrichmentJob,
)
from devfeed_core.source_enrichment import source_identity
from devfeed_core.source_relevance import VERSION

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)


def configuration(**changes):
    return Settings(
        _env_file=None,
        **{
            "database_url": "postgresql+psycopg://unit@database.invalid/unit_test",
            "redis_url": "redis://redis.invalid/15",
            "codex_app_server_url": "ws://127.0.0.1:4500",
            "codex_model": "test-model",
            "ai_enabled": False,
            "full_automation": False,
            "image_storage_enabled": False,
            "image_public_url": None,
            **changes,
        },
    )


def source(*, approval="pending", **changes):
    return Source(
        **{
            "id": uuid.uuid4(),
            "name": "Engineering publisher",
            "slug": "engineering-publisher",
            "feed_url": "https://publisher.example/feed",
            "website_url": "https://publisher.example/",
            "source_type": "publisher",
            "approval_status": approval,
            "enabled": approval == "approved",
            "processing_request": {},
            "relevance_assessment": {},
            "managed_logo": {},
            "last_success_at": NOW - timedelta(days=1) if approval == "approved" else None,
            "created_at": NOW - timedelta(days=2),
            "updated_at": NOW - timedelta(days=1),
            **changes,
        }
    )


def job(model, *, status="queued", **changes):
    return model(
        **{
            "id": uuid.uuid4(),
            "status": status,
            "attempts": 0 if status == "queued" else 1,
            "available_at": NOW - timedelta(minutes=10),
            "created_at": NOW - timedelta(hours=1),
            "finished_at": NOW - timedelta(minutes=1)
            if status in {"failed", "succeeded"}
            else None,
            "lease_token": uuid.uuid4() if status == "running" else None,
            "lease_until": NOW + timedelta(minutes=5) if status == "running" else None,
            "error": "Upstream unavailable" if status == "failed" else None,
            **changes,
        }
    )


def profile(current, *, status="succeeded", **changes):
    return job(
        SourceEnrichmentJob,
        status=status,
        **{
            "source_id": current.id,
            "operation": "profile",
            "result": {"input": source_identity(current), "sample": []},
            **changes,
        },
    )


def assessment(current, current_profile, *, status="succeeded", **changes):
    output = {
        "version": VERSION,
        "feed_url": current.feed_url,
        "source_type": current.source_type,
        "relevance": "uncertain",
        "reason": "The feed sample does not establish a supported editorial decision.",
        "approval_supported": False,
        "rejection_supported": False,
    }
    if status == "succeeded":
        current.relevance_assessment = output
    return job(
        SourceEnrichmentJob,
        status=status,
        **{
            "source_id": current.id,
            "operation": "relevance",
            "result": {
                "input": source_identity(current),
                "profile_job_id": str(current_profile.id),
                "policy_version": VERSION,
                **({"output": output} if status == "succeeded" else {}),
            },
            **changes,
        },
    )
