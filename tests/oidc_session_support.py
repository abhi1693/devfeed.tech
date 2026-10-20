"""Private correlation for browser-session fixtures; independent MCP grants omit it."""

import time


def provider_session(settings, subject):
    return {
        "issuer": settings.oidc_issuer_url,
        "client_id": settings.oidc_client_id,
        "subject": subject,
        "sid": None,
        "issued_at": int(time.time()),
    }
