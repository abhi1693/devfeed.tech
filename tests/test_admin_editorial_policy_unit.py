"""Operator HTTP commands preserve revisions, editorial ownership and safe recovery gates.

Domain commands and preloaded repository reads are boundaries. These tests do not
simulate SQL eligibility, locks or transaction atomicity.
"""

import logging
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_admin_api import articles, automation, dependencies, sources
from devfeed_admin_api.auth import AdminIdentity, require_admin
from devfeed_core.models import ArticleEnrichmentJob, ArticleReview, SourcePublicationPolicyReview
from devfeed_core.schemas import SourceOut
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_http.errors import register_error_handlers
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from test_article_readiness_unit import processing_case as processing_case
from test_source_readiness_unit import NOW, job, source


def identity():
    return AdminIdentity(
        subject="editor-id",
        issuer="https://identity.example",
        organization_id="editorial-org",
        roles=["superuser"],
        name="Editor",
        expires_at=4_102_444_800,
        csrf_token="session-token",
    )


@contextmanager
def operator_api(*modules):
    session, admin = Mock(spec=Session), identity()
    app = FastAPI()
    register_error_handlers(app, logging.getLogger(__name__), admin=True)

    @app.middleware("http")
    async def request_identity(request, call_next):
        request.state.request_id = "unit-operator-request"
        return await call_next(request)

    for module in modules:
        app.include_router(module.router)
    app.dependency_overrides[require_admin] = lambda: admin
    app.dependency_overrides[dependencies.get_session] = lambda: session
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, session=session, admin=admin, app=app)


@pytest.fixture
def editorial(processing_case, monkeypatch):
    article, evidence = processing_case
    article.origins[0].source.slug = "engineering-publisher"
    article.origins[0].source.managed_logo = {}
    for link in article.topic_links:
        link.evidence, link.origin = "Angular application routing", "manual"
    monkeypatch.setattr(articles, "record", lambda *_args, **_kwargs: article)
    with operator_api(articles, automation) as api:
        api.article, api.evidence = article, evidence
        api.session.scalar.return_value = None
        yield api


def edit_body(article, **changes):
    return {
        **{name: getattr(article, name) for name in articles.ArticleFields.model_fields},
        "expected_revision": article.editorial_revision,
        **changes,
    }


def test_content_edit_invalidates_generated_prose_and_approval_and_records_authenticated_audit(
    editorial,
):
    article = editorial.article
    before = article.editorial_revision
    response = editorial.client.put(
        f"/v1/admin/articles/{article.id}",
        json=edit_body(article, title="A corrected source headline"),
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["title"] == "A corrected source headline"
    assert data["editorial_revision"] == before + 1
    assert data["review_status"] == "pending" and data["publication_status"] == "unpublished"
    assert data["ai_title"] is data["ai_summary"] is data["ai_description"] is None
    assert data["classification_provenance"] == {}
    audit = editorial.session.add.call_args.args[0]
    assert isinstance(audit, ArticleReview)
    assert (audit.article_id, audit.action, audit.actor, audit.revision) == (
        article.id,
        "edit",
        editorial.admin.subject,
        before + 1,
    )
    editorial.session.commit.assert_called_once_with()


def test_saving_unchanged_fields_keeps_approval_prose_and_revision(editorial):
    article = editorial.article
    before = (article.editorial_revision, article.ai_summary)
    response = editorial.client.put(f"/v1/admin/articles/{article.id}", json=edit_body(article))
    assert response.status_code == 200
    assert (article.editorial_revision, article.ai_summary) == before
    assert response.json()["review_status"] == "approved"
    editorial.session.scalar.assert_not_called()
    editorial.session.add.assert_not_called()


@pytest.mark.parametrize("barrier", ["revision", "enrichment"])
def test_stale_or_inflight_content_edit_cannot_overwrite_worker_or_operator_changes(
    editorial, barrier
):
    article = editorial.article
    body = edit_body(article, title="A pending edit")
    if barrier == "revision":
        body["expected_revision"] -= 1
    else:
        active = job(ArticleEnrichmentJob, article_id=article.id, status="running")
        editorial.session.scalar.return_value = active.id
    response = editorial.client.put(f"/v1/admin/articles/{article.id}", json=body)
    assert response.status_code == 409
    assert "reload" in response.json()["detail"]
    assert article.title == "Angular routing" and article.review_status == "approved"
    editorial.session.add.assert_not_called()
    editorial.session.commit.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", "   "),
        ("published_at", "2026-10-22T12:00:00"),
        ("image_url", "http://127.0.0.1/private-secret"),
    ],
)
def test_invalid_article_evidence_is_rejected_before_repository_access(
    editorial, monkeypatch, field, value
):
    record = Mock()
    monkeypatch.setattr(articles, "record", record)
    response = editorial.client.put(
        f"/v1/admin/articles/{editorial.article.id}",
        json=edit_body(editorial.article, **{field: value}),
    )
    assert response.status_code == 422
    assert "private-secret" not in response.text
    record.assert_not_called()
    editorial.session.commit.assert_not_called()


@pytest.mark.parametrize("action", ["approve", "reject", "publish", "unpublish"])
def test_editorial_decisions_forward_revision_and_oidc_actor_to_the_domain_command(
    editorial, monkeypatch, action
):
    command = Mock(return_value=editorial.article)
    monkeypatch.setattr(articles, "decide_article", command)
    response = editorial.client.post(
        f"/v1/admin/articles/{editorial.article.id}/review",
        json={"action": action, "expected_revision": 2, "note": "Reviewed source evidence"},
    )
    assert response.status_code == 200
    session, identifier, decision = command.call_args.args
    assert session is editorial.session and identifier == editorial.article.id
    assert decision.model_dump() == {
        "action": action,
        "expected_revision": 2,
        "note": "Reviewed source evidence",
        "actor": editorial.admin.subject,
    }
    editorial.session.commit.assert_called_once_with()


def classification(**changes):
    return {
        "developer_relevance": "relevant",
        "page_kind": "article",
        "language": "en",
        "content_type": "tutorial",
        "content_format": "article",
        "topics": [],
        "tags": [],
        "expected_revision": 2,
        **changes,
    }


def test_manual_classification_cannot_impersonate_the_audit_actor(editorial, monkeypatch):
    command = Mock(return_value=editorial.article)
    monkeypatch.setattr(articles, "classify_manually", command)
    response = editorial.client.post(
        f"/v1/admin/articles/{editorial.article.id}/classify",
        json=classification(actor="forged-operator"),
    )
    assert response.status_code == 422
    command.assert_not_called()


@pytest.mark.parametrize(
    "failure,status",
    [
        (None, 200),
        (ValueError("Choose an active primary topic"), 422),
        (OperationConflict("Article changed"), 409),
        (RecordNotFound("Article not found"), 404),
    ],
)
def test_classification_preserves_domain_error_semantics_and_authenticated_attribution(
    editorial, monkeypatch, failure, status
):
    command = Mock(return_value=editorial.article, side_effect=failure)
    monkeypatch.setattr(articles, "classify_manually", command)
    response = editorial.client.post(
        f"/v1/admin/articles/{editorial.article.id}/classify", json=classification()
    )
    assert response.status_code == status, response.text
    body = command.call_args.args[2]
    assert body.actor == editorial.admin.subject and body.expected_revision == 2
    assert body.language == "en" and body.page_kind == "article"
    if failure:
        assert response.json()["detail"] == str(failure)
        editorial.session.commit.assert_not_called()
    else:
        editorial.session.commit.assert_called_once_with()


@pytest.mark.parametrize("barrier", ["published", "active-jobs", "campaign", "asset", None])
def test_article_deletion_preserves_publication_work_and_sponsorship_history(
    editorial, monkeypatch, barrier
):
    article = editorial.article
    if barrier == "published":
        article.publication_status, article.published_to_feed_at = "published", NOW
    references = Mock(
        side_effect=OperationConflict("Linked active jobs exist")
        if barrier == "active-jobs"
        else None
    )
    monkeypatch.setattr(articles, "prohibit_references", references)
    editorial.session.scalar.side_effect = [barrier == "campaign", barrier == "asset"]
    response = editorial.client.delete(f"/v1/admin/articles/{article.id}")
    assert response.status_code == (204 if barrier is None else 409)
    if barrier is None:
        assert response.content == b""
        editorial.session.commit.assert_called_once_with()
    else:
        editorial.session.execute.assert_not_called()
        editorial.session.commit.assert_not_called()
        assert (
            "Unpublish"
            if barrier == "published"
            else "active jobs"
            if barrier == "active-jobs"
            else "sponsorship history"
        ) in response.json()["detail"]


@pytest.fixture
def source_policy(monkeypatch):
    current = source(
        approval="approved",
        publication_policy="manual",
        publication_policy_revision=3,
        submission_channel="api",
        consecutive_failures=0,
        polling_mode="fixed",
        polling_state={},
        poll_interval_seconds=3600,
        next_fetch_at=NOW + timedelta(hours=1),
    )
    # Persisted source output, including a fixed creation timestamp, uses real schema validation.
    SourceOut.model_validate(current)
    monkeypatch.setattr(sources, "record", lambda *_args, **_kwargs: current)
    with operator_api(sources) as api:
        api.source = current
        yield api


@pytest.mark.parametrize(
    "old,new", [("manual", "preview"), ("preview", "auto"), ("auto", "manual")]
)
def test_publication_policy_changes_require_deliberate_staging_and_attributed_history(
    source_policy, old, new
):
    current, api = source_policy.source, source_policy
    current.publication_policy = old
    response = api.client.put(
        f"/v1/admin/sources/{current.id}/publication-policy",
        json={"mode": new, "expected_revision": 3},
    )
    assert response.status_code == 200, response.text
    assert current.publication_policy == new and current.publication_policy_revision == 4
    audit = api.session.add.call_args.args[0]
    assert isinstance(audit, SourcePublicationPolicyReview)
    assert (audit.mode, audit.revision, audit.actor, audit.source_id) == (
        new,
        4,
        api.admin.subject,
        current.id,
    )
    api.session.commit.assert_called_once_with()


@pytest.mark.parametrize("barrier", ["stale", "unapproved", "skip-preview"])
def test_publication_policy_refuses_stale_or_unreviewed_automation_changes(source_policy, barrier):
    current, api = source_policy.source, source_policy
    if barrier == "unapproved":
        current.approval_status, current.enabled = "pending", False
    response = api.client.put(
        f"/v1/admin/sources/{current.id}/publication-policy",
        json={
            "mode": "auto" if barrier == "skip-preview" else "preview",
            "expected_revision": 2 if barrier == "stale" else 3,
        },
    )
    assert response.status_code == 409
    assert current.publication_policy == "manual" and current.publication_policy_revision == 3
    api.session.add.assert_not_called()
    api.session.commit.assert_not_called()


def test_saving_the_current_publication_policy_does_not_create_a_revision(source_policy):
    api = source_policy
    response = api.client.put(
        f"/v1/admin/sources/{api.source.id}/publication-policy",
        json={"mode": "manual", "expected_revision": 3},
    )
    assert response.status_code == 200 and api.source.publication_policy_revision == 3
    api.session.add.assert_not_called()
    api.session.commit.assert_called_once_with()
