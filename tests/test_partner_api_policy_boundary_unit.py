"""Account-owned portal commands and configuration retain explicit security policy."""

from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from devfeed_core.partner_connections import ConnectionOut
from devfeed_core.partner_connectors import ConnectorConfig, connector_snapshot
from devfeed_http.partner_portal import AccountOut, PartnerPreferencesInput
from devfeed_partner_api import connections, portal
from devfeed_partner_api.auth import PartnerIdentity
from devfeed_partner_api.config import Settings
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session
from test_partner_connection_workflow_unit import connection
from test_partner_placement_workflow_unit import account


def identity():
    return PartnerIdentity(
        subject="partner-user",
        issuer="https://identity.example",
        organization_id="partners",
        roles=["partner"],
        expires_at=2**31,
        csrf_token="test-csrf",
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"oidc_github_idp_id": "invalid provider"},
        {"oidc_role_scope_template": "urn:roles:{unknown}"},
        {"oidc_role_scope_template": "urn:roles:{role} other"},
        {"oidc_issuer_url": "https://user:password@identity.example"},
        {"oidc_issuer_url": "http://identity.example"},
        {"partner_base_url": "https://partner.example/path"},
        {"partner_base_url": "http://localhost:8003"},
        {"partner_base_url": "https://partner.example", "partner_cookie_secure": False},
        {"oidc_organization_id": "invalid org"},
        {"oidc_organization_id": "partners", "oidc_organization_scope_template": "urn:{unknown}"},
        {"oidc_organization_id": "partners", "oidc_organization_scope_template": "urn:fixed"},
        {"oidc_organization_id": "partners", "oidc_organization_claim": " "},
    ],
)
def test_partner_configuration_rejects_unsafe_origins_or_ambiguous_oidc_scopes(
    monkeypatch, changes
):
    # Avoid any developer machine environment configuration in these pure cases.
    for name in list(__import__("os").environ):
        if name.startswith("DEVFEED_"):
            monkeypatch.delenv(name)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **changes)


@pytest.mark.parametrize("origin", ["http://localhost:8003", "https://partner.example"])
def test_partner_configuration_supports_loopback_development_and_secure_deployment(
    monkeypatch, origin
):
    for name in list(__import__("os").environ):
        if name.startswith("DEVFEED_"):
            monkeypatch.delenv(name)
    configured = Settings(
        _env_file=None,
        partner_base_url=origin,
        partner_cookie_secure=origin.startswith("https"),
        oidc_issuer_url="http://127.0.0.1:8080",
        oidc_organization_id="partners",
        oidc_github_idp_id="github-provider",
    )
    assert configured.partner_base_url == origin
    assert configured.oidc_organization_scope_template.format(organization_id="partners").endswith(
        ":partners"
    )


@pytest.mark.parametrize(
    "case",
    [
        "explicit",
        "retained",
        "clear",
        "disallowed_link",
        "disallowed_audience",
        "disallowed_format",
    ],
)
def test_partner_preferences_only_mutate_admitted_marketing_settings(monkeypatch, case):
    owner, session, access = account(), Mock(spec=Session), Mock()
    owner.audience = {"experience_levels": ["senior"]}
    if case.startswith("disallowed") or case == "retained":
        owner.tier = "bronze"
    access.lock.return_value = owner
    monkeypatch.setattr(portal, "PartnerAccountAccess", lambda *_: access)
    enroll = Mock()
    monkeypatch.setattr(portal, "request_promotion_enrollment", enroll)
    payload = {
        "explicit": {
            "outbound_destination": "listing_url",
            "link_rel": ["sponsored"],
            "audience": {"experience_levels": ["mid_level"]},
            "ad_formats": ["feed", "engagement"],
        },
        "retained": {"audience": owner.audience, "ad_formats": owner.ad_formats},
        "clear": {"audience": {}, "ad_formats": []},
        "disallowed_link": {"link_rel": ["ugc"]},
        "disallowed_audience": {"audience": {"experience_levels": ["entry_level"]}},
        "disallowed_format": {"ad_formats": ["engagement"]},
    }[case]
    original_mode, original_tier = owner.approval_mode, owner.tier
    if case.startswith("disallowed"):
        with pytest.raises(HTTPException) as refused:
            portal.update_preferences(
                owner.id, PartnerPreferencesInput(**payload), session, identity()
            )
        assert refused.value.status_code == 422
        enroll.assert_not_called()
        session.commit.assert_not_called()
    else:
        output = portal.update_preferences(
            owner.id, PartnerPreferencesInput(**payload), session, identity()
        )
        assert output is owner
        for name, expected in payload.items():
            actual = getattr(owner, name)
            if name == "audience":
                assert actual["experience_levels"] == expected.get("experience_levels", [])
            else:
                assert actual == expected
        enroll.assert_called_once_with(session, owner.id)
        assert AccountOut.model_validate(owner).id == owner.id
        session.commit.assert_called_once()
    assert owner.approval_mode == original_mode and owner.tier == original_tier
    access.lock.assert_called_once_with(owner.id)


@pytest.mark.parametrize("failure", ["none", "prepare", "revoked", "revision"])
def test_logo_command_rechecks_write_authority_after_external_preparation(monkeypatch, failure):
    owner, saved, session, access = account(), connection(), Mock(spec=Session), Mock()
    saved.account_id, saved.logo_url = owner.id, "https://branding.example/old.png"
    saved.updated_by = connections.actor(identity().model_copy(update={"subject": "prior-owner"}))
    prior_actor, prior_logo = dict(saved.updated_by), saved.logo_url
    prepared = Mock()
    monkeypatch.setattr(connections, "authorize_connection_write", lambda *_: access)
    monkeypatch.setattr(connections, "ConnectionLogoEdit", lambda *_args, **_kwargs: prepared)
    monkeypatch.setattr(connections, "get_redis", lambda: object())
    monkeypatch.setattr(connections, "limit_image_upload", Mock())
    reads = Mock(return_value=saved)
    monkeypatch.setattr(connections, "connection_access", reads)
    monkeypatch.setattr(connections, "partner_connection_views", lambda *_: [saved])

    def prepare():
        session.rollback.assert_called_once()
        if failure == "prepare":
            raise HTTPException(422, "Image normalization failed")
        if failure == "revision":
            saved.revision += 1  # A real concurrent connection edit is now visible.

    prepared.prepare.side_effect = prepare

    @contextmanager
    def stage(_session, authorize):
        # The uploader is an external service boundary. This does not prove its
        # object replacement, compensation, database guard or transaction behavior.
        assert callable(authorize)
        yield

    prepared.stage.side_effect = stage
    prepared.apply.side_effect = lambda row: setattr(row, "logo_url", None)
    if failure == "revoked":
        access.lock.side_effect = HTTPException(404, "Partner account not found")
    if failure == "none":
        assert (
            connections.save_connection_logo(
                owner.id, saved.provider, 3, session, identity(), source_url=None
            )
            is saved
        )
        assert saved.logo_url is None and saved.updated_by == connections.actor(identity())
        access.require.assert_called_once_with(owner.id)
        access.lock.assert_called_once_with(owner.id)
        session.commit.assert_called_once()
    else:
        with pytest.raises(HTTPException) as refused:
            connections.save_connection_logo(
                owner.id, saved.provider, 3, session, identity(), source_url=None
            )
        assert (
            refused.value.status_code == {"prepare": 422, "revoked": 404, "revision": 409}[failure]
        )
        assert saved.logo_url == prior_logo and saved.updated_by == prior_actor
        prepared.apply.assert_not_called()
        session.commit.assert_not_called()


def test_partner_connection_projection_redacts_upstream_diagnostics_and_falls_back_to_bronze(
    monkeypatch,
):
    saved = connection(last_error="private upstream token or response")
    view = ConnectionOut(
        provider=saved.provider,
        name=saved.name,
        revision=saved.revision,
        sync_interval_minutes=360,
        api_url="https://nicklaunches.com/api/v1/products/",
        connector=ConnectorConfig.model_validate(connector_snapshot(saved)),
        enabled=True,
        state="error",
        last_sync_at=None,
        next_sync_at=None,
        error=saved.last_error,
        products=0,
        qualified=0,
        checking=0,
        needs_attention=0,
        excluded=0,
        ai_enabled=False,
    )
    monkeypatch.setattr(connections, "connection_views", lambda *_: [view])
    [output] = connections.partner_connection_views(Mock(spec=Session), [saved])
    assert output.account_tier == "bronze"
    assert output.error == "The latest import failed. Review the connection settings and try again."
    assert "private upstream" not in output.model_dump_json()
