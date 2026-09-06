"""Normalize only the configured, authenticated roles claim. No user-ID fallback."""

from devfeed_admin_api.config import Settings


def claim_roles(settings: Settings, claims: dict) -> set[str]:
    value = claims.get(settings.oidc_roles_claim)
    if settings.oidc_roles_format == "string_list":
        # For providers with a flat, application-scoped roles claim. Organization
        # membership is still checked separately by the OIDC identity verifier.
        if not isinstance(value, list) or not all(isinstance(role, str) for role in value):
            return set()
        return set(value)

    # Zitadel shape: {role_key: {organization_id: primary_domain}}. A role key
    # alone, or a grant in another organization, never grants administrator access.
    if not isinstance(value, dict):
        return set()
    roles = set()
    for role, grants in value.items():
        if (
            not isinstance(role, str)
            or not isinstance(grants, dict)
            or not all(
                isinstance(org, str) and isinstance(domain, str) for org, domain in grants.items()
            )
        ):
            return set()
        if grants.get(settings.oidc_organization_id):
            roles.add(role)
    return roles


def verified_roles(settings: Settings, id_claims: dict, userinfo: dict) -> list[str]:
    """Use either trusted source; if both assert roles, require agreement on access.

    Call only after signature/issuer/audience/nonce validation and UserInfo subject
    matching. Do not union conflicting assertions into broader privileges.
    """
    assertions = [
        claim_roles(settings, claims)
        for claims in (id_claims, userinfo)
        if settings.oidc_roles_claim in claims
    ]
    return sorted(set.intersection(*assertions)) if assertions else []
