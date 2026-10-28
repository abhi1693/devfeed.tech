"""Bounded worker observations for admission, not automatic production scaling."""

from datetime import datetime

from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry

from devfeed_core.ai_capacity import cooldown_remaining
from devfeed_core.config import get_settings
from devfeed_core.models import utcnow
from devfeed_core.redis import create_redis
from devfeed_core.worker_queues import QUEUES, configured_queue
from redis import Redis


def worker_capacity(connection: Redis) -> dict[str, int | bool]:
    keys = sorted(connection.smembers("rq:workers"))
    if len(keys) > 1000:
        raise ValueError("Worker registry exceeds observation limit")
    result: dict[str, int | bool] = {
        "observed": True,
        "cooldown_seconds": cooldown_remaining(connection),
        "topic_workers": 0,
        "article_workers": 0,
        "idle_topic_workers": 0,
        "idle_article_workers": 0,
        "shared_workers": 0,
        "eligible_topic_workers": 0,
        "eligible_article_workers": 0,
        "busy_topic_workers": 0,
        "busy_article_workers": 0,
    }
    with connection.pipeline(transaction=False) as pipe:
        for key in keys:
            pipe.hmget(key, "queues", "state", "death", "last_heartbeat")
            pipe.ttl(key)
        values = pipe.execute()
    for index in range(len(keys)):
        raw, ttl = values[index * 2 : index * 2 + 2]
        queues, state, death, heartbeat = [v.decode() if isinstance(v, bytes) else v for v in raw]
        if ttl <= 0 or death or state not in {"idle", "busy"} or not heartbeat:
            continue
        try:
            age = (
                utcnow() - datetime.fromisoformat(heartbeat.replace("Z", "+00:00"))
            ).total_seconds()
        except (ValueError, TypeError):
            continue
        if not 0 <= age <= 120:
            continue
        physical = set((queues or "").split(","))
        # Only consumers of the currently configured transport provide admission
        # capacity. Old physical queues can still drain without admitting new work.
        queues = [name for name in QUEUES if configured_queue(name) in physical]
        relevant = False
        for kind in ("topic", "article"):
            if any(name in queues for name in (f"{kind}-analysis", f"{kind}-analysis-fresh")):
                relevant = True
                result[f"eligible_{kind}_workers"] += 1
                result[f"busy_{kind}_workers"] += state == "busy"
                # Fresh/ordinary article lanes share one execution family.
                # Physical priority queues can cover several logical kinds.
                # A busy cross-family worker cannot reserve capacity twice.
                family = (
                    {"topic-analysis"}
                    if kind == "topic"
                    else {"article-analysis", "article-analysis-fresh"}
                )
                if set(queues) <= family or state == "idle":
                    result[f"{kind}_workers"] += 1
                    result[f"idle_{kind}_workers"] += state == "idle"
        result["shared_workers"] += relevant and len(queues) > 1
    return result


def observe_capacity() -> dict[str, int | bool]:
    try:
        with create_redis(
            get_settings(),
            socket_connect_timeout=0.2,
            socket_timeout=0.2,
            retry=Retry(NoBackoff(), 0),
        ) as connection:
            return worker_capacity(connection)
    except (RedisError, ValueError):
        return {"observed": False}


def topic_admission_limit(capacity: dict[str, int | bool]) -> int:
    settings = get_settings()
    if not capacity["observed"]:
        return min(4, settings.topic_decision_max_pending)
    if capacity["cooldown_seconds"]:
        return 0
    # Shared priority workers may be busy with another kind from the same
    # transport queue. Keep a bounded topic backlog without requiring an idle
    # window; execution remains limited by the actual worker replica count.
    workers = capacity.get("eligible_topic_workers", 0)
    return min(settings.topic_decision_max_pending, workers * settings.topic_decision_worker_buffer)
