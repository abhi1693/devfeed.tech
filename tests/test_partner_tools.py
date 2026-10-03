"""Partner inventory is review-only and fails closed on stale or invented evidence."""

import copy
import uuid
from datetime import timedelta

import pytest
from devfeed_core.models import utcnow
from devfeed_core.partner_tools import ProductInput, eligible, validate_result
from pydantic import ValidationError


@pytest.fixture
def product_payload():
    return {
        "provider": "nick-launches",
        "external_id": "api-checker",
        "name": "API Checker",
        "product_url": "https://checker.example/",
        "listing_url": "https://nicklaunches.com/products/checker/",
        "description": "Checks OpenAPI specifications for incompatible changes.",
        "pricing": "free",
        "technologies": ["OpenAPI"],
        "evidence": [
            {
                "url": "https://checker.example/docs",
                "quote": "Detect breaking changes between OpenAPI specifications.",
                "capability": "Check API compatibility in CI.",
            }
        ],
    }


@pytest.fixture
def sample(product_payload):
    identifier = str(uuid.uuid4())
    return {
        "product": product_payload,
        "articles": [
            {
                "id": identifier,
                "title": "OpenAPI compatibility in CI",
                "text": "Detect breaking OpenAPI changes before merging a pull request.",
                "content_type": "tutorial",
                "published": True,
            }
        ],
    }


def positive(sample):
    return {
        "decisions": [
            {
                "article_id": sample["articles"][0]["id"],
                "relevant": True,
                "reason": "Checks the API compatibility task described in this tutorial.",
                "article_quote": sample["articles"][0]["text"],
                "evidence_index": 0,
                "technology": "OpenAPI",
            }
        ]
    }


def test_grounded_positive_and_negative(sample):
    result = positive(sample)
    assert validate_result(sample, result)["decisions"][0]["relevant"]
    result["decisions"][0].update(
        relevant=False, article_quote="", evidence_index=-1, technology=""
    )
    assert not validate_result(sample, result)["decisions"][0]["relevant"]


@pytest.mark.parametrize(
    "change", ["invented", "news", "unpublished", "missing", "duplicate", "technology", "evidence"]
)
def test_reject_unsupported_matches(sample, change):
    result = positive(sample)
    decision = result["decisions"][0]
    if change == "invented":
        decision["article_quote"] = "This invented quotation does not exist."
    if change == "news":
        sample["articles"][0]["content_type"] = "news"
    if change == "unpublished":
        sample["articles"][0]["published"] = False
    if change == "missing":
        result["decisions"] = []
    if change == "duplicate":
        result["decisions"].append(copy.deepcopy(decision))
    if change == "technology":
        decision["technology"] = "PostgreSQL"
    if change == "evidence":
        decision["evidence_index"] = 5
    with pytest.raises(ValueError):
        validate_result(sample, result)


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1/", "javascript:alert(1)", "https://user:pass@example.com/"]
)
def test_product_urls_use_public_url_validation(product_payload, url):
    product_payload["product_url"] = url
    with pytest.raises(ValidationError):
        ProductInput.model_validate(product_payload)


def test_verification_expires():
    from types import SimpleNamespace

    now = utcnow()
    product = SimpleNamespace(
        status="approved",
        verified_at=now - timedelta(days=91),
        merged_into_id=None,
        excluded=False,
    )
    assert not eligible(product, now)
    product.verified_at = now
    assert eligible(product, now)
    product.status = "paused"
    assert not eligible(product, now)


def test_partner_endpoints_are_admin_only():
    from devfeed_admin_api.main import create_app as admin_app
    from devfeed_api.main import create_app as public_app
    from devfeed_user_api.main import create_app as user_app

    for create in (public_app, user_app):
        assert not any("partner-tools" in path for path in create().openapi()["paths"])
    document = admin_app().openapi()
    for path, methods in document["paths"].items():
        if "/partner-tools" in path:
            assert all(operation.get("security") for operation in methods.values())


def test_title_alone_is_insufficient_evidence(sample):
    result = positive(sample)
    result["decisions"][0]["article_quote"] = sample["articles"][0]["title"]
    sample["articles"][0]["text"] = ""
    with pytest.raises(ValueError):
        validate_result(sample, result)


def test_technology_must_be_supported_by_product_evidence(sample):
    result = positive(sample)
    sample["product"]["evidence"][0]["quote"] = (
        "Provides generic tools for building better software."
    )
    with pytest.raises(ValueError):
        validate_result(sample, result)
