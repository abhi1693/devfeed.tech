"""A distinct maintenance protocol preserves approval across API rollout and races."""

import pytest
from devfeed_admin_api import article_pipeline as pipeline
from devfeed_core import editorial
from devfeed_core.config import get_settings
from devfeed_core.models import Article, ArticleRefresh, FeedNotificationEvent
from sqlalchemy import func, select, text

from tests.support import article_refresh as support
from tests.support.article_refresh import published_retry_case as published_retry_case

pytestmark = pytest.mark.integration


def request_body(database, case, target):
    with database() as session:
        article = session.get(Article, case.article_id)
        model = support.JOB_MODELS[target]
        previous = session.scalar(
            select(model)
            .where(model.article_id == article.id, model.refresh_id.is_(None))
            .order_by(model.created_at.desc(), model.id.desc())
            .limit(1)
        )
        return {
            "target": target,
            "expected_revision": article.editorial_revision,
            "expected_job_id": str(previous.id) if previous else None,
        }


@pytest.mark.parametrize("target", ["enrichment", "analysis", "images"])
def test_current_maintenance_endpoint_creates_private_work_and_keeps_approved_response(
    database,
    admin_client,
    client,
    published_retry_case,
    target,
):
    case = published_retry_case
    body = request_body(database, case, target)
    response = admin_client.post(
        f"/v1/admin/articles/{case.article_id}/maintenance/retry", json=body
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["stage"] == "published"
    assert result["editorial_revision"] == body["expected_revision"]
    assert result["maintenance"]["target"] == target
    assert result["maintenance"]["status"] == "active"
    with database() as session:
        owner = session.scalar(select(ArticleRefresh))
        job = session.get(support.JOB_MODELS[target], getattr(owner, support.JOB_SLOTS[target]))
        assert job.refresh_id == owner.id and job.status == "queued"
        article = session.get(Article, case.article_id)
        assert (
            article.publication_status == "published"
            and article.published_to_feed_at == case.published_at
        )
        assert session.scalar(select(func.count()).select_from(FeedNotificationEvent)) == 1
    assert client.get(case.path).json() == case.body


def test_maintenance_route_rejects_an_unpublished_article_before_any_ordinary_enqueue(
    database,
    admin_client,
    published_retry_case,
):
    case = published_retry_case
    with database.begin() as session:
        article = session.get(Article, case.article_id)
        editorial.decide_article(
            session,
            article.id,
            editorial.EditorialDecision(
                action="unpublish",
                actor="Editor",
                expected_revision=article.editorial_revision,
            ),
        )
    body = request_body(database, case, "analysis")
    response = admin_client.post(
        f"/v1/admin/articles/{case.article_id}/maintenance/retry", json=body
    )
    assert response.status_code == 409 and "Only published" in response.text
    with database() as session:
        assert session.scalar(select(func.count()).select_from(ArticleRefresh)) == 0
        assert session.get(Article, case.article_id).publication_status == "unpublished"
        assert session.scalar(select(func.count()).select_from(support.JOB_MODELS["analysis"])) == 1


def test_maintenance_route_fails_closed_while_rollout_gate_is_disabled(
    database,
    admin_client,
    published_retry_case,
    monkeypatch,
):
    case = published_retry_case
    monkeypatch.setattr(get_settings(), "published_article_refresh_enabled", False)
    response = admin_client.post(
        f"/v1/admin/articles/{case.article_id}/maintenance/retry",
        json=request_body(database, case, "analysis"),
    )
    assert response.status_code == 409 and "disabled" in response.text
    with database() as session:
        assert session.get(Article, case.article_id).publication_status == "published"
        assert session.scalar(select(func.count()).select_from(ArticleRefresh)) == 0


def test_withdrawal_between_maintenance_admission_and_command_wins_without_lock_inversion(
    database,
    admin_client,
    client,
    published_retry_case,
    monkeypatch,
):
    case = published_retry_case
    body = request_body(database, case, "analysis")
    real = pipeline.request_processing_retry

    def withdraw(session, article_id, **kwargs):
        with database.begin() as editor:
            editor.execute(text("SET LOCAL lock_timeout='1s'"))
            article = editor.get(Article, article_id)
            editorial.decide_article(
                editor,
                article_id,
                editorial.EditorialDecision(
                    action="unpublish",
                    actor="Editor",
                    expected_revision=article.editorial_revision,
                ),
            )
        return real(session, article_id, **kwargs)

    monkeypatch.setattr(pipeline, "request_processing_retry", withdraw)
    response = admin_client.post(
        f"/v1/admin/articles/{case.article_id}/maintenance/retry", json=body
    )
    assert response.status_code == 409
    with database() as session:
        article = session.get(Article, case.article_id)
        assert article.publication_status == "unpublished"
        assert article.editorial_revision == body["expected_revision"] + 1
        assert session.scalar(select(func.count()).select_from(ArticleRefresh)) == 0
        assert session.scalar(select(func.count()).select_from(support.JOB_MODELS["analysis"])) == 1
    assert client.get(case.path).status_code == 404
