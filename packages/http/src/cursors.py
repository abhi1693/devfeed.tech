import base64
import binascii
import json
import uuid
from datetime import datetime

from devfeed_core.models import Article
from fastapi import HTTPException


def encode_cursor(article: Article) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(
            [
                article.feed_at.isoformat(),
                str(article.id),
            ]
        ).encode()
    ).decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        payload = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if (
            not isinstance(payload, list)
            or len(payload) != 2
            or not all(isinstance(value, str) for value in payload)
        ):
            raise ValueError("Cursor requires a timestamp and UUID string pair")
        date, identifier = payload
        parsed_date = datetime.fromisoformat(date)
        if parsed_date.tzinfo is None:
            raise ValueError("Cursor requires timezone")
        return parsed_date, uuid.UUID(identifier)
    except (ValueError, TypeError, binascii.Error, UnicodeError) as exc:
        raise HTTPException(422, "Invalid feed cursor") from exc
