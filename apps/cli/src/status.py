import logging
from datetime import datetime
from typing import cast

from devfeed_aggregator.queue import get_queue
from devfeed_core.db import session_factory
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleEnrichmentJob,
    ArticleImageJob,
    IngestionJob,
    Source,
    SourceEnrichmentJob,
    Topic,
    utcnow,
)
from redis.exceptions import RedisError
from rq import Queue, Worker
from rq.serializers import JSONSerializer
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)


def snapshot() -> dict:
    result: dict = {"database_available": False, "redis_available": False}
    try:
        with session_factory()() as session:
            result.update(
                sources=session.scalar(select(func.count()).select_from(Source)),
                source_review={
                    state: count
                    for state, count in session.execute(
                        select(Source.approval_status, func.count()).group_by(
                            Source.approval_status
                        )
                    )
                },
                source_enrichment_jobs={
                    state: count
                    for state, count in session.execute(
                        select(SourceEnrichmentJob.status, func.count()).group_by(
                            SourceEnrichmentJob.status
                        )
                    )
                },
                articles=session.scalar(select(func.count()).select_from(Article)),
                topics=session.scalar(select(func.count()).select_from(Topic)),
                article_publication={
                    state: count
                    for state, count in session.execute(
                        select(Article.publication_status, func.count()).group_by(
                            Article.publication_status
                        )
                    )
                },
                article_review={
                    state: count
                    for state, count in session.execute(
                        select(Article.review_status, func.count()).group_by(Article.review_status)
                    )
                },
                article_analysis_jobs={
                    state: count
                    for state, count in session.execute(
                        select(ArticleAnalysisJob.status, func.count()).group_by(
                            ArticleAnalysisJob.status
                        )
                    )
                },
                article_enrichment_jobs={
                    state: count
                    for state, count in session.execute(
                        select(ArticleEnrichmentJob.status, func.count()).group_by(
                            ArticleEnrichmentJob.status
                        )
                    )
                },
                jobs={
                    state: count
                    for state, count in session.execute(
                        select(IngestionJob.status, func.count()).group_by(IngestionJob.status)
                    )
                },
                image_jobs={
                    state: count
                    for state, count in session.execute(
                        select(ArticleImageJob.status, func.count()).group_by(
                            ArticleImageJob.status
                        )
                    )
                },
                oldest_active_image_job_at=session.scalar(
                    select(func.min(ArticleImageJob.created_at)).where(
                        ArticleImageJob.status.in_(["queued", "running"])
                    )
                ),
                oldest_active_job_at=session.scalar(
                    select(func.min(IngestionJob.created_at)).where(
                        IngestionJob.status.in_(["queued", "running"])
                    )
                ),
                database_available=True,
            )
    except SQLAlchemyError as exc:
        logger.warning(
            "status_dependency_failed",
            extra={"dependency": "database", "error_type": type(exc).__name__},
        )
        result["database_error"] = (
            "Database unavailable or schema not initialized; check 'devfeed db upgrade'."
        )
    queue = get_queue()
    try:
        heartbeat = cast(bytes | None, queue.connection.get("devfeed:scheduler:heartbeat"))
        last_seen = None
        if heartbeat:
            try:
                last_seen = datetime.fromisoformat(heartbeat.decode())
                if last_seen.tzinfo is None:
                    last_seen = None
            except (ValueError, UnicodeError):
                pass
        age = (utcnow() - last_seen).total_seconds() if last_seen else None
        analysis_queue = Queue("analysis", connection=queue.connection, serializer=JSONSerializer)
        result.update(
            redis_available=True,
            queue_depth=queue.count,
            analysis_queue_depth=analysis_queue.count,
            analysis_workers=[
                {
                    "name": item.name,
                    "state": item.get_state(),
                    "last_heartbeat": item.last_heartbeat,
                }
                for item in Worker.all(queue=analysis_queue, serializer=JSONSerializer)
            ],
            scheduler_last_seen=last_seen,
            scheduler_healthy=age is not None and 0 <= age < 120,
            workers=[
                {
                    "name": item.name,
                    "state": item.get_state(),
                    "pid": item.pid,
                    "last_heartbeat": item.last_heartbeat,
                }
                for item in Worker.all(queue=queue, serializer=JSONSerializer)
            ],
        )
    except RedisError as exc:
        logger.warning(
            "status_dependency_failed",
            extra={"dependency": "redis", "error_type": type(exc).__name__},
        )
        result.update(
            redis_available=False,
            queue_depth=None,
            analysis_queue_depth=None,
            analysis_workers=[],
            scheduler_last_seen=None,
            scheduler_healthy=False,
            workers=[],
        )
    finally:
        queue.connection.close()
    result["dependencies_ready"] = result["database_available"] and result["redis_available"]
    return result
