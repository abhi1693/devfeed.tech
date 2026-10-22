"""Private content reads retain evidence and revision history without publishing.

Rows/pages and domain commands are explicit service boundaries. No SQL filters,
ownership predicates, row locks or transaction semantics are simulated here.
"""

import hashlib
import uuid
from datetime import timedelta
from unittest.mock import Mock

import pytest
from devfeed_admin_api import articles
from devfeed_core.models import Article, ArticleContent, ArticleReview
from devfeed_core.services import OperationConflict, RecordNotFound
from test_admin_editorial_policy_unit import editorial as editorial
from test_analysis import result
from test_article_readiness_unit import processing_case as processing_case
from test_source_readiness_unit import NOW


@pytest.fixture(autouse=True)
def retained_processing_history(editorial):
    value, evidence = editorial.article, editorial.evidence
    for link in value.topic_links:
        link.origin = "ai"
    for name in ("enrichment", "analysis"):
        evidence[name].available_at = evidence[name].created_at
    evidence["analysis"].result = result(
        ai_title=value.ai_title,
        ai_summary=value.ai_summary,
        ai_description=value.ai_description,
        language=value.language,
        content_type=value.content_type,
        content_format=value.content_format,
        topics=[
            {
                "topic_id": value.topic_links[0].topic_id,
                "role": "primary",
                "relevance": 0.9,
                "evidence": "Angular routing",
            }
        ],
    ).model_dump(mode="json")


@pytest.mark.parametrize("closed", [False, True])
def test_article_detail_explains_current_source_authority_without_rewriting_editorial_history(
    editorial, closed
):
    value = editorial.article
    if closed:
        publisher = value.origins[0].source
        publisher.approval_status, publisher.enabled = "rejected", False
        publisher.reviewed_at, publisher.reviewed_by, publisher.review_note = (
            NOW,
            "reviewer",
            "Publisher no longer meets policy",
        )
    response = editorial.client.get(f"/v1/admin/articles/{value.id}")
    assert response.status_code == 200, response.text
    output = response.json()
    assert (
        output["id"] == str(value.id) and output["editorial_revision"] == value.editorial_revision
    )
    assert output["publication_status"] == "unpublished" and output["review_status"] == "approved"
    assert ("no_approved_source" in output["publication_blockers"]) is closed
    assert output["sources"][0]["id"] == str(value.origins[0].source_id)
    editorial.session.add.assert_not_called()
    editorial.session.commit.assert_not_called()


@pytest.mark.parametrize("retained", [False, True])
def test_content_read_returns_saved_extraction_or_null_after_validating_parent(
    editorial, monkeypatch, retained
):
    value = editorial.article
    text = "Angular router guards control navigation with dependency injection."
    stored = ArticleContent(
        article_id=value.id,
        url=value.canonical_url,
        text=text,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        method="trafilatura",
        retrieved_at=NOW - timedelta(minutes=4),
    )
    parent = Mock()
    monkeypatch.setattr(articles, "require_record", parent)
    editorial.session.get.return_value = stored if retained else None
    response = editorial.client.get(f"/v1/admin/articles/{value.id}/content")
    assert response.status_code == 200, response.text
    parent.assert_called_once_with(editorial.session, Article, value.id)
    editorial.session.get.assert_called_once_with(ArticleContent, value.id)
    if retained:
        assert response.json() == {
            "article_id": str(value.id),
            "url": value.canonical_url,
            "text": text,
            "method": "trafilatura",
            "retrieved_at": (NOW - timedelta(minutes=4)).isoformat().replace("+00:00", "Z"),
        }
    else:
        assert response.json() is None
    editorial.session.commit.assert_not_called()


@pytest.mark.parametrize("path", ["content", "reviews"])
def test_missing_parent_does_not_disclose_or_fetch_detached_content_and_review_rows(
    editorial, monkeypatch, path
):
    monkeypatch.setattr(
        articles, "require_record", Mock(side_effect=RecordNotFound("Article not found"))
    )
    response = editorial.client.get(f"/v1/admin/articles/{uuid.uuid4()}/{path}")
    assert response.status_code == 404
    editorial.session.get.assert_not_called()
    editorial.session.scalars.assert_not_called()


def test_review_page_retains_revision_actor_and_automation_receipt(editorial, monkeypatch):
    value = editorial.article
    audit = ArticleReview(
        id=uuid.uuid4(),
        article_id=value.id,
        action="approve",
        actor="reviewer",
        note="Read source evidence",
        revision=value.editorial_revision,
        created_at=NOW - timedelta(minutes=1),
        automation={},
    )
    parent = Mock()
    monkeypatch.setattr(articles, "require_record", parent)
    page = Mock(return_value={"items": [audit], "total": 1, "limit": 25, "offset": 0})
    monkeypatch.setattr(articles, "paginate", page)
    response = editorial.client.get(
        f"/v1/admin/articles/{value.id}/reviews", params={"q": "reviewer"}
    )
    assert response.status_code == 200, response.text
    output = response.json()
    assert output["total"] == 1 and output["offset"] == 0
    (item,) = output["items"]
    assert (item["revision"], item["actor"], item["action"], item["automation"]) == (
        value.editorial_revision,
        "reviewer",
        "approve",
        {},
    )
    parent.assert_called_once_with(editorial.session, Article, value.id)
    editorial.session.commit.assert_not_called()


@pytest.mark.parametrize("empty", [False, True])
def test_preselected_article_page_preserves_global_count_and_full_private_projection(
    editorial, monkeypatch, empty
):
    value = editorial.article
    page = {"items": [] if empty else [value], "total": 8, "offset": 8 if empty else 7, "limit": 1}
    read = Mock(return_value=page)
    monkeypatch.setattr(articles, "paginate", read)
    response = editorial.client.get(
        "/v1/admin/articles", params={"limit": 1, "offset": page["offset"]}
    )
    assert response.status_code == 200, response.text
    output = response.json()
    assert (output["total"], output["offset"], output["limit"]) == (8, page["offset"], 1)
    assert [item["id"] for item in output["items"]] == ([] if empty else [str(value.id)])
    if not empty:
        (item,) = output["items"]
        assert item["classification_provenance"] == value.classification_provenance
        assert item["topics"][0]["topic_id"] == str(value.topic_links[0].topic_id)
    editorial.session.commit.assert_not_called()


@pytest.mark.parametrize(
    "error,status",
    [
        (ValueError("Choose an active topic"), 422),
        (OperationConflict("Article changed; reload before classifying"), 409),
        (RecordNotFound("Article not found"), 404),
    ],
)
def test_classification_domain_failures_keep_http_meaning_and_never_commit(
    editorial, monkeypatch, error, status
):
    apply = Mock(side_effect=error)
    monkeypatch.setattr(articles, "classify_manually", apply)
    value = editorial.article
    response = editorial.client.post(
        f"/v1/admin/articles/{value.id}/classify",
        json={
            "developer_relevance": "relevant",
            "page_kind": "article",
            "language": "en",
            "content_type": "tutorial",
            "content_format": "article",
            "topics": [],
            "tags": [],
            "expected_revision": value.editorial_revision,
        },
    )
    assert response.status_code == status, response.text
    parsed = apply.call_args.args[2]
    assert (
        parsed.actor == editorial.admin.subject
        and parsed.expected_revision == value.editorial_revision
    )
    editorial.session.commit.assert_not_called()
