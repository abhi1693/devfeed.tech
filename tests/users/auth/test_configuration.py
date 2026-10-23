"""Reader identity settings reject unsafe deployment combinations before serving requests."""

import pytest
from devfeed_user_api.config import Settings
from pydantic import ValidationError


@pytest.fixture(autouse=True)
def explicit_environment(monkeypatch):
    # These aliases are intentionally shared; keep each case independent of local deployment values.
    for name in (
        "DEVFEED_OIDC_GITHUB_IDP_ID",
        "DEVFEED_OIDC_GOOGLE_IDP_ID",
        "DEVFEED_X_PIXEL_ENABLED",
        "X_PIXEL_TOKEN",
        "X_SIGNUP_EVENT_ID",
    ):
        monkeypatch.delenv(name, raising=False)


def settings(**values):
    return Settings(_env_file=None, _env_prefix="DEVFEED_UNIT_UNUSED_", **values)


@pytest.mark.parametrize("provider", ["github", "google"])
@pytest.mark.parametrize("identifier", ["provider with spaces", "provider/selector", "p" * 129])
def test_direct_login_provider_configuration_cannot_inject_an_additional_scope(
    provider, identifier
):
    with pytest.raises(ValidationError, match="valid provider identifiers") as invalid:
        settings(**{f"oidc_{provider}_idp_id": identifier})
    assert identifier not in str(invalid.value)


@pytest.mark.parametrize(
    "values,diagnostic",
    [
        (
            {"mcp_session_ttl_seconds": 3601, "mcp_session_absolute_ttl_seconds": 3600},
            "MCP inactivity",
        ),
        ({"mcp_resource_url": "https://mcp.example/mcp"}, "configured together"),
        ({"mcp_issuer_url": "https://mcp.example"}, "configured together"),
        (
            {"mcp_resource_url": "http://mcp.example/mcp", "mcp_issuer_url": "https://mcp.example"},
            "MCP resource must use HTTPS",
        ),
        (
            {
                "mcp_resource_url": "https://mcp.example/mcp",
                "mcp_issuer_url": "https://mcp.example/oauth",
            },
            "MCP issuer must be an origin",
        ),
        (
            {"session_ttl_seconds": 301, "session_absolute_ttl_seconds": 300},
            "User session inactivity",
        ),
        ({"base_url": "https://reader.example/private"}, "without a path"),
        (
            {"base_url": "http://reader.example", "cookie_secure": True},
            "HTTP user development requires",
        ),
        (
            {"base_url": "https://reader.example", "cookie_secure": False},
            "HTTPS user requires secure cookies",
        ),
        ({"oidc_issuer_url": "http://identity.example"}, "OIDC issuer requires HTTPS"),
        (
            {"oidc_issuer_url": "https://private-user:private-password@identity.example"},
            "without credentials or query",
        ),
        ({"oidc_organization_id": "organization name"}, "cannot contain whitespace"),
        (
            {
                "oidc_organization_id": "readers",
                "oidc_organization_scope_template": "organization:{missing}",
            },
            "Invalid OIDC organization scope template",
        ),
        (
            {
                "oidc_organization_id": "readers",
                "oidc_organization_scope_template": "organization:{organization_id",
            },
            "Invalid OIDC organization scope template",
        ),
        (
            {
                "oidc_organization_id": "readers",
                "oidc_organization_scope_template": "organization:readers",
            },
            "requires a scope template and claim",
        ),
        (
            {
                "oidc_organization_id": "readers",
                "oidc_organization_scope_template": "organization: {organization_id}",
            },
            "requires a scope template and claim",
        ),
        (
            {"oidc_organization_id": "readers", "oidc_organization_claim": " "},
            "requires a scope template and claim",
        ),
    ],
)
def test_invalid_user_deployment_settings_fail_closed_with_configuration_diagnostics(
    values, diagnostic
):
    with pytest.raises(ValidationError, match=diagnostic) as invalid:
        settings(**values)
    assert "private-password" not in str(invalid.value)


@pytest.mark.parametrize("loopback", ["localhost", "127.0.0.1", "[::1]"])
def test_explicit_http_development_allows_loopback_identity_and_mcp_without_insecure_https_cookies(
    loopback,
):
    current = settings(
        base_url="http://192.0.2.10:3000",
        cookie_secure=False,
        oidc_issuer_url=f"http://{loopback}:8080/identity",
        mcp_resource_url=f"http://{loopback}:8003/mcp",
        mcp_issuer_url=f"http://{loopback}:8003",
        oidc_organization_id="readers",
        oidc_organization_scope_template="organization:{organization_id}",
        oidc_organization_claim="organization",
    )
    assert current.allows_request_origin("http://192.0.2.10:3000")
    assert (
        current.oidc_organization_scope_template.format(organization_id="readers")
        == "organization:readers"
    )
    assert current.mcp_resource_url.endswith("/mcp")


@pytest.mark.parametrize(
    "enabled,expected",
    [("true", True), (" TRUE ", True), ("false", False), ("1", False), (True, True)],
)
def test_signup_conversion_requires_the_explicit_enabled_value(enabled, expected):
    current = settings(DEVFEED_X_PIXEL_ENABLED=enabled, X_SIGNUP_EVENT_ID="tw-pc5f8-AB12")
    assert current.x_pixel_enabled is expected
    assert current.x_signup_event_id == "tw-pc5f8-AB12"


@pytest.mark.parametrize("identifier", ["tw-another-AB12", "tw-pc5f8-", "tw-pc5f8-A B"])
def test_signup_event_cannot_target_an_unrelated_pixel_or_inject_payload_text(identifier):
    with pytest.raises(ValidationError, match="must belong to pixel pc5f8"):
        settings(X_SIGNUP_EVENT_ID=identifier)
