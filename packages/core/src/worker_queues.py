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
WorkerQueue = Literal[
    QueueName,
    "all",
    "background",
    "analysis",
    "high",
    "default",
    "low",
]
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
SHARED_BACKGROUND_QUEUES = tuple(q for q in BACKGROUND_QUEUES if q != "source-discovery")


# Logical lane names survive transport changes in job metadata and reports.
LOGICAL_QUEUES = QUEUES
PRIORITY_QUEUES = ("high", "default", "low")
LEGACY_ALIASES = {
    "partner-pipeline": "ingestion",
    "partner-evaluation": "source-analysis",
    "recommendations": "ingestion",
}
ROUTABLE_QUEUES = (*QUEUES, *LEGACY_ALIASES)


def configured_queue(logical: str, settings: "Settings | None" = None) -> str:
    if logical not in ROUTABLE_QUEUES:
        raise ValueError("Unknown worker queue")
    if settings is None:
        from devfeed_core.config import get_settings

        settings = get_settings()
    if settings.job_queue_mode == "legacy":
        return LEGACY_ALIASES.get(logical, logical)
    if logical in settings.job_queue_overrides:
        return settings.job_queue_overrides[logical]
    # A kind override also covers its fresh lane unless explicitly overridden.
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
    destinations = tuple(configured_queue(name, settings) for name in ROUTABLE_QUEUES)
    return (
        tuple(dict.fromkeys((*PRIORITY_QUEUES, *destinations)))
        if settings.job_queue_mode == "priority"
        else tuple(dict.fromkeys(destinations))
    )


def worker_queues(
    name: str,
    *,
    ai_enabled: bool,
    notifications_enabled: bool,
    settings: "Settings | None" = None,
    legacy: bool = False,
) -> list[str]:
    if settings is None:
        from devfeed_core.config import get_settings

        settings = get_settings()
    if not legacy and settings.job_queue_mode == "priority":
        return _priority_worker_queues(name, settings)
    if legacy and name in PRIORITY_QUEUES:
        raise ValueError("Priority queues are not legacy queues")
    if name in {"article-analysis", "article-enrichment"}:
        return [f"{name}-fresh", name]
    if name == "analysis":
        # Retain the deployed CLI group name while consuming only current queues.
        return list(AI_QUEUES)
    if name in {"all", "background"}:
        return (
            list(SHARED_BACKGROUND_QUEUES)
            + (list(AI_QUEUES) if name == "all" and ai_enabled else [])
            + (["notifications"] if notifications_enabled else [])
        )
    if name not in QUEUES:
        raise ValueError("Unknown worker queue")
    return [name]


def _priority_worker_queues(name: str, settings: "Settings") -> list[str]:
    if name in {"background", "analysis"}:
        raise ValueError("Legacy worker groups require --legacy-queues")
    if name in ROUTABLE_QUEUES:
        return _dedicated_worker_queues(name, settings)
    if name in physical_queues(settings):
        return [name]
    if name == "all":
        reserved = {configured_queue("images", settings), configured_queue("solver", settings)}
        return [q for q in physical_queues(settings) if q not in reserved]
    raise ValueError("Unknown worker queue")


def _dedicated_worker_queues(name: str, settings: "Settings") -> list[str]:
    # Old slim/uncredentialed dedicated entrypoints must not silently become
    # generic consumers when a logical queue maps into shared priority transport.
    base = name.removesuffix("-fresh")
    family = (
        (base + "-fresh", base) if base in {"article-analysis", "article-enrichment"} else (name,)
    )
    selected = family if name in {"article-analysis", "article-enrichment"} else (name,)
    destinations = list(dict.fromkeys(configured_queue(q, settings) for q in selected))
    owners = {q for q in ROUTABLE_QUEUES if configured_queue(q, settings) in destinations}
    if not owners <= set(family):
        raise ValueError(
            "Logical worker selectors require dedicated queue overrides; "
            "use an explicit physical priority queue or --legacy-queues"
        )
    return destinations
