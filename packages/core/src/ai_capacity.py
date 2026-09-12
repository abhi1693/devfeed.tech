"""Share provider cooldowns across workers; never copy provider messages to Redis."""

import hashlib

from redis.exceptions import RedisError

from devfeed_core.config import get_settings
from devfeed_core.redis import create_redis

CAPACITY_ERRORS = {"codex_rate_limited", "codex_usage_limit"}


def cooldown_key() -> str:
    endpoint = get_settings().codex_app_server_url or "unconfigured"
    return "devfeed:ai:cooldown:" + hashlib.sha256(endpoint.encode()).hexdigest()


def cooldown_remaining(connection) -> int:
    return max(0, connection.ttl(cooldown_key()))


def pause_capacity(seconds: int) -> int:
    settings = get_settings()
    seconds = min(86400, max(settings.ai_capacity_cooldown_seconds, seconds))
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


def safe_pause(seconds: int) -> int:
    try:
        return pause_capacity(seconds)
    except RedisError:
        # The durable job still waits. Redis failure independently pauses dequeue.
        return max(get_settings().ai_capacity_cooldown_seconds, min(86400, seconds))
