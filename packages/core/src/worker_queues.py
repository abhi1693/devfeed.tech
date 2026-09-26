"""Physical RQ queues and supported worker groups."""

from typing import Literal, get_args

QueueName = Literal[
    "ingestion",
    "relationships",
    "notifications",
    "solver",
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
WorkerQueue = Literal[QueueName, "all", "background", "analysis"]
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
    "article-enrichment-fresh",
    "article-enrichment",
    "source-enrichment",
    "images",
)


def worker_queues(name: str, *, ai_enabled: bool, notifications_enabled: bool) -> list[str]:
    """Explicit queues opt in; groups follow feature flags and never include solver."""
    if name in {"article-analysis", "article-enrichment"}:
        return [f"{name}-fresh", name]
    if name == "analysis":
        # Retain the deployed CLI group name while consuming only current queues.
        return list(AI_QUEUES)
    if name in {"all", "background"}:
        return (
            list(BACKGROUND_QUEUES)
            + (list(AI_QUEUES) if name == "all" and ai_enabled else [])
            + (["notifications"] if notifications_enabled else [])
        )
    if name not in QUEUES:
        raise ValueError("Unknown worker queue")
    return [name]
