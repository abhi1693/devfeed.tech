"""Serve real reader auth routes with isolated test identity/store for browser recovery."""

import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import tests.users.auth.test_auth as test_user_auth  # noqa: E402

# The mock issuer signs fresh tokens and verifies the real PKCE verifier. The
# fixture owns its in-memory store and cannot reach any deployed service.
test_user_auth.ORIGIN = "https://devfeed.tech"
with pytest.MonkeyPatch.context() as patches:
    fixture = test_user_auth.oidc_app.__wrapped__(patches)
    state = next(fixture)
    state.metadata["authorization_endpoint"] = "https://devfeed.tech/test-provider/authorize"

    async def app(scope, receive, send):
        async def capture(message):
            if (
                scope.get("path") == "/v1/user/auth/login"
                and message["type"] == "http.response.start"
                and message["status"] == 302
            ):
                location = urlsplit(dict(message["headers"])[b"location"].decode())
                if location.path.endswith("/authorize"):
                    state.params = parse_qs(location.query)
            await send(message)

        await state.client.app(scope, receive, capture)

    try:
        uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), access_log=False)
    finally:
        fixture.close()
