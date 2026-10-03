"""Server-only completed sign-up measurement; credentials and email never leave logs."""

import hashlib
import logging
from datetime import datetime

import httpx

from devfeed_user_api.config import Settings
from devfeed_user_api.x_attribution import valid_click_id

logger = logging.getLogger(__name__)
CONVERSIONS_URL = "https://ads-api.x.com/12/measurement/conversions/pc5f8"


def send_signup_conversion(
    settings: Settings,
    *,
    user_id: str,
    email: str | None,
    conversion_time: datetime,
    twclid: str | None = None,
) -> bool:
    """Send confirmed new accounts with the landing click ID and/or SHA-256 email."""
    if not settings.x_pixel_enabled or not settings.x_pixel_token or not settings.x_signup_event_id:
        return False
    normalized_email = (email or "").strip().lower()
    identifiers = []
    if normalized_email:
        identifiers.append({"hashed_email": hashlib.sha256(normalized_email.encode()).hexdigest()})
    captured = valid_click_id(twclid)
    if captured:
        identifiers.append({"twclid": captured})
    if not identifiers:
        logger.warning("x_signup_conversion_missing_identifier")
        return False
    conversion = {
        "conversion_time": conversion_time.isoformat(timespec="milliseconds").replace(
            "+00:00", "Z"
        ),
        "event_id": settings.x_signup_event_id,
        "conversion_id": f"signup-{user_id}",
        "identifiers": identifiers,
    }
    try:
        with httpx.Client(timeout=5, follow_redirects=False) as client:
            response = client.post(
                CONVERSIONS_URL,
                headers={
                    "X-Pixel-Token": settings.x_pixel_token.get_secret_value(),
                    "Content-Type": "application/json",
                },
                json={"conversions": [conversion]},
            )
        if response.status_code != 200:
            logger.warning(
                "x_signup_conversion_rejected", extra={"status_code": response.status_code}
            )
            return False
    except httpx.HTTPError:
        # Measurement is best-effort and cannot invalidate a successful sign-up.
        # Never log request/response bodies or exceptions containing credentials.
        logger.warning("x_signup_conversion_unavailable")
        return False
    logger.info("x_signup_conversion_sent", extra={"status_code": 200})
    return True
