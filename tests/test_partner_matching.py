"""Specific evidence and practical tasks gate contextual article suggestions."""

import uuid
from types import SimpleNamespace as NS

import pytest
from devfeed_core.partner_matching import article_fingerprint, article_match, grounded_technologies
from devfeed_http.partner_placements import PlacementQuery
from pydantic import ValidationError


def product():
    return NS(
        technologies=["Kubernetes", "AI"],
        evidence=[
            {
                "quote": (
                    "Monitor Kubernetes clusters and inspect metrics "
                    "with independently configured alerts."
                ),
                "capability": "Monitor Kubernetes clusters and inspect metrics",
            }
        ],
    )


def article(**kwargs):
    return NS(
        **{
            "id": uuid.uuid4(),
            "title": "Monitor Kubernetes clusters",
            "editorial_revision": 1,
            "summary": (
                "Monitor Kubernetes clusters and inspect cluster metrics "
                "in this practical tutorial."
            ),
            "content_type": "tutorial",
            "publication_status": "published",
            **kwargs,
        }
    )


def test_specific_technology_and_grounded_task_both_required():
    assert grounded_technologies(product()) == ["Kubernetes"]
    relevant, score, reason = article_match(product(), article())
    assert relevant and score >= 75 and "Kubernetes" in reason
    assert not article_match(
        product(),
        article(
            title="Deploy Kubernetes applications",
            summary="Deploy Kubernetes applications with namespace configuration and manifests.",
        ),
    )[0]
    assert not article_match(
        product(),
        article(
            title="Monitor database clusters",
            summary="Monitor database clusters and inspect metrics for PostgreSQL services.",
        ),
    )[0]


@pytest.mark.parametrize(
    "overrides",
    [
        {"content_type": "news"},
        {"publication_status": "unpublished"},
        {"summary": "Too short"},
    ],
)
def test_nonpractical_or_unpublished_content_never_qualifies(overrides):
    assert not article_match(product(), article(**overrides))[0]


def test_unsupported_capability_cannot_be_grounded_by_technology_alone():
    item = product()
    item.evidence[0]["quote"] = (
        "This service supports Kubernetes for developer productivity and collaboration."
    )
    assert not article_match(item, article())[0]


def test_fingerprint_changes_on_original_text_and_editorial_decisions():
    current = article()
    old = article_fingerprint(current)
    current.summary += " Changed original content."
    assert article_fingerprint(current) != old
    old = article_fingerprint(current)
    current.editorial_revision += 1
    assert article_fingerprint(current) != old


@pytest.mark.parametrize(
    "payload",
    [
        {"surface": "article"},
        {"surface": "topic"},
        {"surface": "feed", "topic_id": str(uuid.uuid4())},
        {"surface": "article", "article_id": str(uuid.uuid4()), "topic_id": str(uuid.uuid4())},
        {"surface": "feed", "user_id": str(uuid.uuid4())},
        {"surface": "feed", "exclude_product_ids": [str(uuid.uuid4())] * 101},
    ],
)
def test_query_accepts_context_only_and_has_bounded_exclusions(payload):
    with pytest.raises(ValidationError):
        PlacementQuery.model_validate(payload)
