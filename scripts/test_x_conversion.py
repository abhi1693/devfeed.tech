"""Send one explicitly requested test conversion using server environment settings."""

import argparse
import uuid
from datetime import UTC, datetime

from devfeed_user_api.config import get_settings
from devfeed_user_api.x_conversions import send_signup_conversion

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", help="Email identifier for the test (hashed locally)")
    parser.add_argument(
        "--twclid", help="Real landing-page click ID to attribute the test conversion"
    )
    args = parser.parse_args()
    if not args.email and not args.twclid:
        parser.error("Provide --email and/or --twclid for the test conversion")
    settings = get_settings()
    if not settings.x_pixel_enabled:
        parser.error("Set DEVFEED_X_PIXEL_ENABLED=true to send a test conversion")
    if not settings.x_pixel_token or not settings.x_signup_event_id:
        parser.error("Set X_PIXEL_TOKEN and X_SIGNUP_EVENT_ID in the server environment or .env")
    if send_signup_conversion(
        settings,
        user_id=f"test-{uuid.uuid4()}",
        email=args.email,
        twclid=args.twclid,
        conversion_time=datetime.now(UTC),
    ):
        print("X Conversion API returned HTTP 200")
    else:
        raise SystemExit("X Conversion API did not return HTTP 200; see the status-only server log")
