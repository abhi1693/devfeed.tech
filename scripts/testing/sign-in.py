"""Run the real sign-in route for browser tests with an isolated provider and store."""

import json
import sys
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from devfeed_user_api import auth
from devfeed_user_api.config import Settings
from fastapi import FastAPI
from fastapi.testclient import TestClient

settings = Settings(
    _env_file=None,
    base_url="https://devfeed.tech",
    oidc_issuer_url="https://identity.example",
    oidc_client_id="browser-test",
    oidc_organization_id="test-org",
)
store = Mock()
app = FastAPI()
app.include_router(auth.router)
with (
    patch.object(auth, "get_settings", return_value=settings),
    patch.object(auth, "get_redis", return_value=store),
    patch.object(
        auth.oidc,
        "discovery",
        return_value={
            "authorization_endpoint": sys.argv[2],
        },
    ),
    TestClient(app) as client,
):
    response = client.get(sys.argv[1], follow_redirects=False)
    if response.status_code == 302:
        # Prove the route retained the exact destination for the callback.
        flow = json.loads(store.set.call_args.args[1])
        assert flow["return_to"] == parse_qs(urlsplit(sys.argv[1]).query).get("return_to", ["/"])[0]
    print(
        json.dumps(
            {
                "status": response.status_code,
                "headers": dict(response.headers),
                "body": response.text,
            }
        )
    )
