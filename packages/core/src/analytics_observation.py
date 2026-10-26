"""Current operator observation contracts, independent of serving storage."""

import hashlib
import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from devfeed_core.config import get_settings

PIPELINE_POLICY_VERSION = "pipeline-v2"


class AnalyticsSnapshot(BaseModel):
    status: Literal["fresh", "stale", "refreshing", "catching_up", "rebuilding"]
    generated_at: datetime | None = None
    generation: str | None = None
    policy_version: str | None = None
    article_generation: int | None = Field(default=None, ge=0)
    catalog_generation: int | None = Field(default=None, ge=0)
    catalog_revision: list[list[str]] | None = None
    catalog_target_revision: list[list[str]] | None = None
    catalog_lag_seconds: float | None = Field(default=None, ge=0)
    lag_seconds: float | None = Field(default=None, ge=0)


def pipeline_policy_version(settings=None) -> str:
    """Configuration and code changes invalidate every affected derived decision."""
    from devfeed_core.analysis import PROMPT_VERSION
    from devfeed_core.article_analysis_steps import STEP_SPECS
    from devfeed_core.logos import LOGO_VERSION
    from devfeed_core.managed_images import IMAGE_VERSION, THUMBNAIL_VERSION
    from devfeed_core.source_relevance import VERSION

    settings = settings or get_settings()
    payload = {
        "version": PIPELINE_POLICY_VERSION,
        "prompt": PROMPT_VERSION,
        "steps": {name: spec.prompt_version for name, spec in STEP_SPECS.items()},
        "images": (IMAGE_VERSION, THUMBNAIL_VERSION, LOGO_VERSION),
        "source_relevance": VERSION,
        "settings": {
            name: getattr(settings, name)
            for name in (
                "ai_enabled",
                "full_automation",
                "image_storage_enabled",
                "image_public_url",
                "analysis_max_candidates",
                "analysis_fallback_candidates",
                "ai_content_not_before",
            )
        },
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
