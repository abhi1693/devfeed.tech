"""Only verified, correctly scoped role grants authorize administration."""

import pytest
from devfeed_admin_api.config import Settings
from devfeed_admin_api.roles import claim_roles, verified_roles
from pydantic import ValidationError

ROLE_CLAIM = "urn:zitadel:iam:org:project:roles"


@pytest.fixture
def settings():
    return Settings(_env_file=None, oidc_organization_id="org-1")


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        [],
        ["superuser"],
        "superuser",
        "reader superuser",
        True,
        1,
        {"superuser": True},
        {"superuser": "org-1"},
        {"superuser": ["org-1"]},
        {"superuser": {}},
        {"superuser": {"org-1": None}},
        {"superuser": {"org-1": ""}},
        {"superuser": {"different-org": "other.example"}},
        {"Superuser": {"org-1": "org.example"}},
        {"superuser-extra": {"org-1": "org.example"}},
    ],
)
def test_malformed_or_unrelated_roles_do_not_grant_access(settings, value):
    assert "superuser" not in claim_roles(settings, {ROLE_CLAIM: value})


def test_only_roles_for_configured_organization_are_returned(settings):
    assert claim_roles(
        settings,
        {
            ROLE_CLAIM: {
                "superuser": {"org-1": "org.example", "org-2": "other.example"},
                "editor": {"org-2": "other.example"},
                "reader": {"org-1": "org.example"},
            }
        },
    ) == {"superuser", "reader"}


def test_other_project_claims_are_not_searched_for_privileges(settings):
    assert (
        claim_roles(
            settings,
            {
                "urn:zitadel:iam:org:project:another-project:roles": {
                    "superuser": {"org-1": "org.example"},
                },
            },
        )
        == set()
    )


def test_flat_generic_provider_roles_require_explicit_configuration(settings):
    settings.oidc_roles_claim = "roles"
    settings.oidc_roles_format = "string_list"
    assert claim_roles(settings, {"roles": ["reader", "superuser"]}) == {"reader", "superuser"}
    assert claim_roles(settings, {"roles": "superuser"}) == set()
    assert claim_roles(settings, {"roles": ["superuser", {}]}) == set()


def test_conflicting_assertions_never_widen_access(settings):
    privileged = {ROLE_CLAIM: {"superuser": {"org-1": "org.example"}}}
    unprivileged = {ROLE_CLAIM: {}}
    assert verified_roles(settings, privileged, {}) == ["superuser"]
    assert verified_roles(settings, {}, privileged) == ["superuser"]
    assert verified_roles(settings, privileged, privileged) == ["superuser"]
    assert verified_roles(settings, privileged, unprivileged) == []
    assert verified_roles(settings, unprivileged, privileged) == []


@pytest.mark.parametrize(
    "values",
    [
        {"admin_required_role": ""},
        {"admin_required_role": "super user"},
        {"oidc_roles_claim": ""},
        {"oidc_roles_format": "guess"},
        {"oidc_role_scope_template": "roles:{unknown}"},
        {"oidc_role_scope_template": "roles {role}"},
    ],
)
def test_invalid_role_configuration_is_rejected(values):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


def test_scope_can_be_generic_or_omitted():
    assert (
        Settings(_env_file=None, oidc_role_scope_template="roles").oidc_role_scope_template
        == "roles"
    )
    assert Settings(_env_file=None, oidc_role_scope_template=None).oidc_role_scope_template is None
