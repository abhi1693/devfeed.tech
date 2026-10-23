"""Partner products, published articles, and matching facts."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

from devfeed_core.models import (
    Article,
    PartnerProduct,
    Topic,
)
from devfeed_core.urls import fingerprint

NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)


def product(**changes):
    return PartnerProduct(
        **{
            "id": uuid.uuid4(),
            "name": "API Checker",
            "product_url": "https://checker.example/",
            "tagline": "Detect breaking OpenAPI changes before deployment.",
            "pricing": "free",
            "revision": 2,
            "updated_at": NOW - timedelta(hours=1),
            "status": "approved",
            "verified_at": NOW - timedelta(hours=1),
            "assessment_revision": 2,
            "assessment": {"state": "qualified"},
            "excluded": False,
            "technologies": ["OpenAPI"],
            "matching_tokens": ["openapi"],
            "evidence": [
                {
                    "url": "https://checker.example/docs",
                    "quote": "Detect breaking OpenAPI changes before deployment.",
                    "capability": "Detect breaking changes before deployment.",
                }
            ],
            "reviews": [],
            **changes,
        }
    )


def article(**changes):
    identifier = changes.get("id", uuid.uuid4())
    url = changes.get("canonical_url", f"https://publisher.example/openapi-{identifier}")
    return Article(
        **{
            "id": identifier,
            "title": "Detect breaking OpenAPI changes",
            "summary": "Detect breaking OpenAPI changes before deployment using a CI check.",
            "content_type": "tutorial",
            "publication_status": "published",
            "review_status": "approved",
            "canonical_url": url,
            "url_hash": fingerprint(url),
            "slug": f"openapi-{identifier}",
            "discovered_at": NOW - timedelta(hours=2),
            "published_to_feed_at": NOW - timedelta(hours=1),
            "editorial_revision": 2,
            "matching_tokens": ["openapi", "detect", "breaking", "changes"],
            **changes,
        }
    )


def topic(name, **changes):
    return Topic(
        **{
            "id": uuid.uuid4(),
            "name": name,
            "slug": name.casefold().replace(" ", "-"),
            "status": "active",
            "kind": "technology",
            "aliases": [],
            "matching_tokens": [name.casefold()],
            "updated_at": NOW - timedelta(days=1),
            **changes,
        }
    )


def result(values):
    return NS(all=lambda: values)
