"""Share provider cooldowns across workers; never copy provider messages to Redis."""

import hashlib

from redis.exceptions import RedisError

from devfeed_core.config import get_settings
from devfeed_core.redis import create_redis

CAPACITY_ERRORS = {"codex_rate_limited", "codex_usage_limit", "codex_server_overloaded"}


def cooldown_key() -> str:
    endpoint = get_settings().codex_app_server_url or "unconfigured"
    return "devfeed:ai:cooldown:" + hashlib.sha256(endpoint.encode()).hexdigest()


def cooldown_remaining(connection) -> int:
    return max(0, connection.ttl(cooldown_key()))


def pause_capacity(seconds: int, *, reason: str | None = None) -> int:
    settings = get_settings()
    minimum = (
        settings.ai_server_overload_cooldown_seconds
        if reason == "codex_server_overloaded"
        else settings.ai_capacity_cooldown_seconds
    )
    seconds = min(86400, max(minimum, seconds))
    # Never shorten another worker's longer provider reset window.
    with create_redis(settings, socket_connect_timeout=3, socket_timeout=3) as connection:
        connection.eval(
            """
            local old = redis.call('ttl', KEYS[1])
            if old < tonumber(ARGV[1]) then
                redis.call('set', KEYS[1], 'capacity', 'EX', ARGV[1])
            end
            return 1
        """,
            1,
            cooldown_key(),
            seconds,
        )
    return seconds


def safe_pause(seconds: int, *, reason: str | None = None) -> int:
    try:
        return pause_capacity(seconds, reason=reason)
    except RedisError:
        # The durable job still waits. Redis failure independently pauses dequeue.
        settings = get_settings()
        minimum = (
            settings.ai_server_overload_cooldown_seconds
            if reason == "codex_server_overloaded"
            else settings.ai_capacity_cooldown_seconds
        )
        return max(minimum, min(86400, seconds))
