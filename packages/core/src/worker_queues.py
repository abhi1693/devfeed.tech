"""Physical RQ queues and supported worker groups."""

from typing import TYPE_CHECKING, Literal, get_args

if TYPE_CHECKING:
    from devfeed_core.config import Settings

QueueName = Literal[
    "ingestion",
    "relationships",
    "notifications",
    "solver",
    "source-discovery",
    "article-analysis-fresh",
    "article-analysis",
    "topic-analysis",
    "research-verification",
    "source-analysis",
    "article-enrichment-fresh",
    "article-enrichment",
    "source-enrichment",
    "images",
]
WorkerQueue = Literal["high", "default", "low"]
QUEUES: tuple[str, ...] = get_args(QueueName)
AI_QUEUES = (
    "relationships",
    "article-analysis-fresh",
    "article-analysis",
    "topic-analysis",
    "research-verification",
    "source-analysis",
)
BACKGROUND_QUEUES = (
    "ingestion",
    "source-discovery",
    "article-enrichment-fresh",
    "article-enrichment",
    "source-enrichment",
    "images",
)
# Logical job kinds are independent from configurable transport names.
LOGICAL_QUEUES = QUEUES
PRIORITY_QUEUES = ("high", "default", "low")
ROUTABLE_QUEUES = (*QUEUES, "partner-pipeline", "partner-evaluation", "recommendations")


def configured_queue(logical: str, settings: "Settings | None" = None) -> str:
    if logical not in ROUTABLE_QUEUES:
        raise ValueError("Unknown worker queue")
    if settings is None:
        from devfeed_core.config import get_settings

        settings = get_settings()
    if logical in settings.job_queue_overrides:
        return settings.job_queue_overrides[logical]
    if (
        logical.endswith("-fresh")
        and logical.removesuffix("-fresh") in settings.job_queue_overrides
    ):
        return settings.job_queue_overrides[logical.removesuffix("-fresh")]
    if logical in {"images", "solver"}:
        return logical
    if logical in {"article-analysis-fresh", "article-enrichment-fresh", "recommendations"}:
        return "high"
    return "low" if logical == "notifications" else "default"


def physical_queues(settings: "Settings | None" = None) -> tuple[str, ...]:
    if settings is None:
        from devfeed_core.config import get_settings

        settings = get_settings()
    return tuple(
        dict.fromkeys(
            (*PRIORITY_QUEUES, *(configured_queue(name, settings) for name in ROUTABLE_QUEUES))
        )
    )


def worker_queues(name: str, *, settings: "Settings | None" = None) -> list[str]:
    """Workers explicitly subscribe to one priority or configured dedicated queue."""
    if name not in physical_queues(settings):
        raise ValueError("Unknown worker queue")
    return [name]
