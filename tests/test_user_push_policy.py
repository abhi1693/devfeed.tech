"""User API startup publishes only the current session-policy fingerprint."""

import asyncio
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from devfeed_core.web_push import USER_PUSH_POLICY_KEY
from devfeed_user_api import main, oidc, web_push
from devfeed_user_api.config import Settings
from redis.exceptions import RedisError


@pytest.mark.parametrize("enabled", [False, True])
def test_startup_publishes_policy_before_serving_push_enrollment(monkeypatch, enabled):
    publish = Mock()
    monkeypatch.setattr(main, "get_web_push_settings", lambda: NS(web_push_enabled=enabled))
    monkeypatch.setattr(web_push, "publish_session_policy", publish)

    async def exercise():
        async with main.push_session_policy(None):
            assert publish.call_count == int(enabled)

    asyncio.run(exercise())


def test_policy_publication_failure_blocks_push_startup(monkeypatch):
    monkeypatch.setattr(main, "get_web_push_settings", lambda: NS(web_push_enabled=True))
    monkeypatch.setattr(
        web_push, "publish_session_policy", Mock(side_effect=RedisError("unavailable"))
    )

    async def exercise():
        async with main.push_session_policy(None):
            pytest.fail("Serving push consent without its authorization policy")

    with pytest.raises(RedisError):
        asyncio.run(exercise())


def test_only_a_policy_fingerprint_is_shared_with_workers(monkeypatch):
    settings = Settings(_env_file=None)
    client = Mock()
    monkeypatch.setattr(web_push, "user_settings", lambda: settings)
    monkeypatch.setattr(web_push.auth, "get_redis", lambda: client)
    web_push.publish_session_policy()
    client.set.assert_called_once_with(USER_PUSH_POLICY_KEY, oidc.policy_key(settings))
    original = client.set.call_args.args[1]
    assert len(original) == 64
    settings.session_ttl_seconds += 1
    web_push.publish_session_policy()
    assert client.set.call_args.args[1] != original
