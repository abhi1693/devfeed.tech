"""Browser consent accepts an SDK-issued authorization request without OAuth state.

Pending record writes/consumption and accepted grant writes are Redis boundaries;
these units do not execute Lua or claim single-use/atomic grant guarantees.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest
from devfeed_mcp import oauth
from devfeed_user_api import mcp
from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from test_account_agent_contracts_unit import agent as agent


@pytest.mark.parametrize("approved", [False, True])
def test_consent_without_optional_client_state_preserves_the_registered_redirect_and_issuer(
    agent, approved
):
    store = SimpleNamespace(incr=AsyncMock(return_value=1), expire=AsyncMock(), set=AsyncMock())
    provider = oauth.OAuthProvider(store, agent.settings.mcp_resource_url, agent.settings.base_url)
    client = OAuthClientInformationFull(
        client_id="agent-client",
        client_name="Reader agent",
        token_endpoint_auth_method="none",
        redirect_uris=[agent.pending["redirect_uri"]],
    )
    params = AuthorizationParams(
        redirect_uri=agent.pending["redirect_uri"],
        redirect_uri_provided_explicitly=True,
        code_challenge=agent.pending["code_challenge"],
        state=None,
        scopes=["devfeed:read"],
    )
    location = asyncio.run(provider.authorize(client, params))
    identifier = parse_qs(urlsplit(location).query)["request"][0]
    record = store.set.await_args.args[1]
    assert json.loads(record)["state"] is None
    agent.redis.getdel.return_value = record

    response = agent.client.post(
        f"/v1/user/mcp/requests/{identifier}",
        json={"approved": approved, "access": "read-only"} if approved else {"approved": False},
    )

    assert response.status_code == 200, response.text
    redirect = urlsplit(response.json()["redirect_url"])
    query = parse_qs(redirect.query)
    assert (redirect.scheme, redirect.netloc, redirect.path) == (
        "https",
        "agent.example",
        "/callback",
    )
    assert query["flow"] == ["reader"] and query["iss"] == [agent.settings.mcp_issuer_url]
    assert "state" not in query and "None" not in response.text
    agent.redis.getdel.assert_called_once_with(mcp.key("pending", identifier))
    if approved:
        assert "error" not in query and len(query["code"][0]) == 43
        write = agent.redis.eval.call_args.args
        saved = json.loads(write[7])
        assert saved["state"] is None and saved["code_challenge"] == params.code_challenge
        assert saved["scopes"] == ["devfeed:read"]
    else:
        assert query["error"] == ["access_denied"] and "code" not in query
        agent.redis.eval.assert_not_called()
