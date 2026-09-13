"""Physical RQ queues and backwards-compatible worker groups."""

from typing import Literal, get_args

QueueName = Literal[
    "ingestion",
    "analysis",  # Legacy deliveries: only consumers use this queue after upgrade.
    "relationships",
    "notifications",
    "solver",
    "article-analysis",
    "topic-analysis",
    "research-verification",
    "source-analysis",
    "article-enrichment",
    "source-enrichment",
    "images",
]
WorkerQueue = Literal[QueueName, "all", "background"]
QUEUES: tuple[str, ...] = get_args(QueueName)
AI_QUEUES = (
    "analysis",
    "relationships",
    "article-analysis",
    "topic-analysis",
    "research-verification",
    "source-analysis",
)
BACKGROUND_QUEUES = ("ingestion", "article-enrichment", "source-enrichment", "images")


def worker_queues(name: str, *, ai_enabled: bool, notifications_enabled: bool) -> list[str]:
    """Explicit queues opt in; groups follow feature flags and never include solver."""
    if name == "analysis":
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
