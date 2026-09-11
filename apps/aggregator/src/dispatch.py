"""One-off, targeted publication using the scheduler's durable dispatch path."""

import logging
import uuid
from collections.abc import Callable
from typing import Literal

from devfeed_core.article_jobs import prepare_article_dispatch
from devfeed_core.db import session_factory
from devfeed_core.image_jobs import prepare_image_dispatch
from devfeed_core.job_definitions import JOB_DEFINITIONS, Job
from devfeed_core.job_dispatch import dispatch_jobs
from devfeed_core.logging import log_context
from devfeed_core.models import (
    Source,
    utcnow,
)
from devfeed_core.schemas import (
    ArticleEnrichmentJobOut,
    ImageJobOut,
    JobOut,
    SourceEnrichmentJobOut,
)
from devfeed_core.services import RecordNotFound, prepare_immediate_dispatch
from devfeed_core.source_enrichment import prepare_enrichment_dispatch
from devfeed_core.source_relevance import requires_relevance
from pydantic import BaseModel
from sqlalchemy.orm import Session

from devfeed_aggregator.queue import get_queue

logger = logging.getLogger(__name__)


ImmediateKind = Literal["ingestion", "article-enrichment", "images", "source-enrichment"]

IMMEDIATE_DISPATCH: dict[
    ImmediateKind, tuple[Callable[[Session, uuid.UUID], Job], type[BaseModel], str, str]
] = {
    "ingestion": (
        prepare_immediate_dispatch,
        JobOut,
        "Job not found",
        "ingestion_immediate_dispatch_failed",
    ),
    "article-enrichment": (
        prepare_article_dispatch,
        ArticleEnrichmentJobOut,
        "Article enrichment job not found",
        "article_enrichment_dispatch_failed",
    ),
    "images": (
        prepare_image_dispatch,
        ImageJobOut,
        "Image job not found",
        "image_immediate_dispatch_failed",
    ),
    "source-enrichment": (
        prepare_enrichment_dispatch,
        SourceEnrichmentJobOut,
        "Source enrichment job not found",
        "source_enrichment_dispatch_failed",
    ),
}


def dispatch_now(job_id: uuid.UUID, *, kind: ImmediateKind = "ingestion") -> dict:
    """Commit the due-time override before publication; concurrent claims coalesce."""
    prepare, output, missing, failure = IMMEDIATE_DISPATCH[kind]
    ingestion = kind == "ingestion"
    definition = JOB_DEFINITIONS[kind]
    factory = session_factory()
    with factory.begin() as session:
        job = prepare(session, job_id)
        fields = definition.log_fields(job)
        source_analysis = kind == "source-enrichment" and requires_relevance(
            session.get(Source, job.source_id)
        )
    with log_context(**fields):
        if ingestion:
            logger.info("ingestion_immediate_dispatch_requested")
        queue = get_queue("analysis") if source_analysis else get_queue()
        try:
            count = dispatch_jobs(
                factory,
                queue,
                1,
                utcnow(),
                job_id=job_id,
                kind=kind,
                **({"source_analysis": True} if source_analysis else {}),
            )
        except Exception:
            logger.exception(failure)
            raise
        finally:
            queue.connection.close()
        if ingestion and count == 0:
            logger.info("ingestion_immediate_dispatch_coalesced")
        with factory() as session:
            current = session.get(definition.model, job_id)
            if current is None:
                raise RecordNotFound(missing)
            return output.model_validate(current).model_dump(mode="json")
