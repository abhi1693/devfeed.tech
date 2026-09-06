"""One-off, targeted publication using the scheduler's durable dispatch path."""

import logging
import uuid

from devfeed_core.article_jobs import prepare_article_dispatch
from devfeed_core.db import session_factory
from devfeed_core.image_jobs import prepare_image_dispatch
from devfeed_core.logging import log_context
from devfeed_core.models import (
    ArticleEnrichmentJob,
    ArticleImageJob,
    IngestionJob,
    SourceEnrichmentJob,
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

from devfeed_aggregator.queue import get_queue
from devfeed_aggregator.scheduler import dispatch_jobs

logger = logging.getLogger(__name__)


def dispatch_article_now(job_id: uuid.UUID) -> dict:
    factory = session_factory()
    with factory.begin() as session:
        job = prepare_article_dispatch(session, job_id)
        article_id = job.article_id
    with log_context(job_id=job_id, article_id=article_id):
        queue = get_queue()
        try:
            dispatch_jobs(factory, queue, 1, utcnow(), job_id=job_id, articles=True)
        except Exception:
            logger.exception("article_enrichment_dispatch_failed")
            raise
        finally:
            queue.connection.close()
        with factory() as session:
            current = session.get(ArticleEnrichmentJob, job_id)
            if current is None:
                raise RecordNotFound("Article enrichment job not found")
            return ArticleEnrichmentJobOut.model_validate(current).model_dump(mode="json")


def dispatch_now(job_id: uuid.UUID) -> dict:
    """Make one queued job due and publish it now; no scheduler/worker is started.

    The override commits first. If Redis is unavailable, the durable job stays
    eligible for the scheduler instead of waiting out its previous cooldown.
    Concurrent scheduler/worker claims coalesce under the existing job-row lock.
    """
    factory = session_factory()
    with factory.begin() as session:
        job = prepare_immediate_dispatch(session, job_id)
        source_id = job.source_id
    with log_context(job_id=job_id, source_id=source_id):
        logger.info("ingestion_immediate_dispatch_requested")
        queue = get_queue()
        try:
            count = dispatch_jobs(factory, queue, 1, utcnow(), job_id=job_id)
        except Exception:
            logger.exception("ingestion_immediate_dispatch_failed")
            raise
        finally:
            queue.connection.close()
        if count == 0:
            # The scheduler or a worker won the race after the override committed.
            logger.info("ingestion_immediate_dispatch_coalesced")
        with factory() as session:
            current = session.get(IngestionJob, job_id)
            if current is None:
                raise RecordNotFound("Job not found")
            return JobOut.model_validate(current).model_dump(mode="json")


def dispatch_image_now(job_id: uuid.UUID) -> dict:
    factory = session_factory()
    with factory.begin() as session:
        job = prepare_image_dispatch(session, job_id)
        article_id = job.article_id
    with log_context(job_id=job_id, article_id=article_id):
        queue = get_queue()
        try:
            dispatch_jobs(factory, queue, 1, utcnow(), job_id=job_id, images=True)
        except Exception:
            logger.exception("image_immediate_dispatch_failed")
            raise
        finally:
            queue.connection.close()
        with factory() as session:
            current = session.get(ArticleImageJob, job_id)
            if current is None:
                raise RecordNotFound("Image job not found")
            return ImageJobOut.model_validate(current).model_dump(mode="json")


def dispatch_source_enrichment(job_id: uuid.UUID) -> dict:
    factory = session_factory()
    with factory.begin() as session:
        job = prepare_enrichment_dispatch(session, job_id)
        source_id = job.source_id
    with log_context(job_id=job_id, source_id=source_id):
        queue = get_queue()
        try:
            dispatch_jobs(factory, queue, 1, utcnow(), job_id=job_id, profiles=True)
        except Exception:
            logger.exception("source_enrichment_dispatch_failed")
            raise
        finally:
            queue.connection.close()
        with factory() as session:
            current = session.get(SourceEnrichmentJob, job_id)
            if current is None:
                raise RecordNotFound("Source enrichment job not found")
            return SourceEnrichmentJobOut.model_validate(current).model_dump(mode="json")
