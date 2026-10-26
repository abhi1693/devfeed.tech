"""Recovery commands keep editorial gates; reporting composes already aggregated facts."""

import uuid
from unittest.mock import Mock

import pytest
from devfeed_admin_api import automation
from devfeed_core.models import ArticleAnalysisJob, ArticleEnrichmentJob

from tests.support.admin_editorial import operator_api
from tests.support.article_readiness import processing_case as processing_case
from tests.support.source_readiness import NOW, configuration, job


@pytest.fixture
def recovery(processing_case, monkeypatch):
    article, evidence = processing_case
    article.review_status = "pending"
    monkeypatch.setattr(automation, "record", lambda *args, **kw: article)
    authority = Mock(return_value=[article.origins[0].source_id])
    monkeypatch.setattr(automation, "approved_sources", authority)
    monkeypatch.setattr(automation, "lock_topics", Mock())
    with operator_api(automation) as api:
        api.article, api.evidence, api.authority = article, evidence, authority
        yield api


@pytest.mark.parametrize("action", ["enrich", "analyze"])
def test_explicit_recovery_queues_only_the_requested_stage_without_publishing(
    recovery, monkeypatch, action
):
    api = recovery
    monkeypatch.setattr(automation, "get_settings", lambda: configuration(ai_enabled=True))
    requested = job(
        ArticleEnrichmentJob if action == "enrich" else ArticleAnalysisJob,
        article_id=api.article.id,
    )
    command = Mock(return_value=requested)
    other = Mock()
    monkeypatch.setattr(
        automation,
        "request_article_enrichment" if action == "enrich" else "request_analysis",
        command,
    )
    monkeypatch.setattr(
        automation,
        "request_analysis" if action == "enrich" else "request_article_enrichment",
        other,
    )
    response = api.client.post(
        f"/v1/admin/automation/articles/{api.article.id}/{action}", json={"expected_revision": 2}
    )
    assert response.status_code == 200 and response.json() == {
        "status": "queued",
        "job_id": str(requested.id),
        "decision": None,
    }
    command.assert_called_once_with(api.session, api.article.id)
    other.assert_not_called()
    assert (
        api.article.review_status == "pending" and api.article.publication_status == "unpublished"
    )
    api.session.commit.assert_called_once_with()


@pytest.mark.parametrize(
    "barrier", ["source", "revision", "approved", "rejected", "published", "disabled-ai"]
)
def test_recovery_refuses_missing_authority_or_changed_editorial_state(
    recovery, monkeypatch, barrier
):
    api = recovery
    monkeypatch.setattr(
        automation, "get_settings", lambda: configuration(ai_enabled=barrier != "disabled-ai")
    )
    if barrier == "source":
        api.authority.return_value = []
    elif barrier in {"approved", "rejected"}:
        api.article.review_status = barrier
    elif barrier == "published":
        api.article.review_status = "approved"
        api.article.publication_status, api.article.published_to_feed_at = "published", NOW
    command = Mock()
    monkeypatch.setattr(automation, "request_analysis", command)
    response = api.client.post(
        f"/v1/admin/automation/articles/{api.article.id}/analyze",
        json={"expected_revision": 1 if barrier == "revision" else 2},
    )
    assert response.status_code == 409
    assert (
        "approved source"
        if barrier == "source"
        else "Enable AI"
        if barrier == "disabled-ai"
        else "Article changed"
    ) in response.json()["detail"]
    command.assert_not_called()
    api.session.commit.assert_not_called()


@pytest.mark.parametrize("barrier", ["active", "missing", None])
def test_evaluation_waits_for_settled_analysis_and_returns_domain_decision_without_overriding_it(
    recovery, monkeypatch, barrier
):
    api = recovery
    active = (
        job(ArticleAnalysisJob, article_id=api.article.id, status="running")
        if barrier == "active"
        else None
    )
    api.session.scalar.side_effect = (
        [active.id]
        if active
        else [None, None if barrier == "missing" else api.evidence["analysis"]]
    )
    decision = {"status": "blocked", "blockers": ["manual_review_required"]}
    command = Mock(return_value=decision)
    monkeypatch.setattr(automation, "apply_publication_policy", command)
    response = api.client.post(
        f"/v1/admin/automation/articles/{api.article.id}/evaluate", json={"expected_revision": 2}
    )
    assert response.status_code == (409 if barrier else 200)
    if barrier:
        assert ("Wait for active analysis" if active else "Analyze the article") in response.json()[
            "detail"
        ]
        command.assert_not_called()
        api.session.commit.assert_not_called()
    else:
        assert response.json() == {"status": "blocked", "job_id": None, "decision": decision}
        command.assert_called_once_with(api.session, api.article, api.evidence["analysis"])
        api.session.commit.assert_called_once_with()


def test_compact_blockers_keep_overlapping_causes_and_original_recovery_actions(monkeypatch):
    first, second = uuid.uuid4(), uuid.uuid4()

    def cause(code, action, ids):
        return automation.AutomationBlocker(
            code=code,
            label=code.replace("_", " "),
            count=len(ids),
            action=action,
            targets=[
                automation.RecoveryTarget(
                    id=value, title="Source article", kind="article", revision=2
                )
                for value in ids
            ],
        )

    reader = Mock(
        return_value=[
            cause("insufficient_source_text", "enrich", [first]),
            cause("analysis_failed", "analyze", [first, second]),
        ]
    )
    monkeypatch.setattr(automation, "automation_blockers", reader)
    session = object()
    result = automation.automation_blocker_rows(session, [first, second])
    assert result == {
        first: [
            {
                "code": "insufficient_source_text",
                "label": "insufficient source text",
                "action": "enrich",
            },
            {"code": "analysis_failed", "label": "analysis failed", "action": "analyze"},
        ],
        second: [{"code": "analysis_failed", "label": "analysis failed", "action": "analyze"}],
    }
    reader.assert_called_once_with(
        session, [first, second], include_proposals=False, target_limit=2
    )


@pytest.mark.parametrize("count", [0, 201])
def test_compact_blocker_projection_skips_empty_batches_and_rejects_unbounded_work(
    monkeypatch, count
):
    reader = Mock()
    monkeypatch.setattr(automation, "automation_blockers", reader)
    if count:
        with pytest.raises(ValueError, match="at most 200"):
            automation.automation_blocker_rows(object(), [uuid.uuid4() for _ in range(count)])
    else:
        assert automation.automation_blocker_rows(object(), []) == {}
    reader.assert_not_called()
