"""Published article projections and reader identities for private read contracts."""

import uuid
from datetime import timedelta

import pytest
from devfeed_core.article_lifecycle import derive_publication_readiness
from devfeed_user_api.auth import UserIdentity

from tests.admin.articles.test_article_board_policy_unit import history
from tests.analysis.test_analysis import result
from tests.support.article_readiness import processing_case as processing_case
from tests.support.source_readiness import NOW


@pytest.fixture
def stories(processing_case):
    items = []
    for index in range(2):
        current, facts = history(processing_case, f"reader-starter-{index}")
        link = current.topic_links[0]
        link.origin, link.evidence = "ai", current.summary
        facts["analysis"].result = result(
            language=current.language,
            content_type=current.content_type,
            content_format=current.content_format,
            ai_title=current.ai_title,
            ai_summary=current.ai_summary,
            ai_description=current.ai_description,
            topics=[
                {
                    "topic_id": link.topic_id,
                    "role": link.role,
                    "relevance": link.relevance,
                    "evidence": link.evidence,
                }
            ],
            tags=[],
        ).model_dump(mode="json")
        assert derive_publication_readiness(current, **facts).stage == "ready"
        current.publication_status = "published"
        current.published_to_feed_at = current.feed_at = NOW - timedelta(seconds=30 * (index + 1))
        current.editorial_revision += 1
        current.slug = f"reader-starter-{index}"
        current.origins[0].source.slug = "engineering-publisher"
        current.origins[0].source_metadata = {}
        items.append(current)
    return items


def user():
    return UserIdentity(
        user_id=str(uuid.uuid4()),
        issuer="https://identity.example",
        subject="reader",
        organization_id="readers",
        name="Reader",
        expires_at=4_102_444_800,
        csrf_token="c" * 43,
    )
