"""Operator command adapters preserve authority and explicit intent across service boundaries."""

import copy
import uuid
from datetime import timedelta
from unittest.mock import Mock

import pytest
from devfeed_admin_api import article_pipeline as pipeline
from devfeed_admin_api import partner_accounts, sources, topics
from devfeed_admin_api.auth import actor
from devfeed_core.article_lifecycle import derive_publication_readiness
from devfeed_core.models import IngestionJob, PartnerAccount, PartnerProduct, Topic, TopicRelation
from devfeed_core.services import OperationConflict
from devfeed_core.topics import RelationWrite
from fastapi import HTTPException

from tests.support.admin_editorial import identity, operator_api
from tests.support.admin_editorial import source_policy as source_policy
from tests.support.article_readiness import processing_case as processing_case
from tests.support.source_readiness import NOW, job


@pytest.mark.parametrize("operation", ["update", "review", "fetch"])
def test_source_commands_delegate_validated_intent_and_authenticated_review(
    source_policy, monkeypatch, operation
):
    api = source_policy
    name = {"update": "update_source", "review": "review_source", "fetch": "fetch_source"}[
        operation
    ]
    result = (
        job(
            IngestionJob,
            source_id=api.source.id,
            automatic=False,
            new_source_entries=0,
            entries_seen=0,
            articles_created=0,
            entries_skipped=0,
        )
        if operation == "fetch"
        else api.source
    )
    command = Mock(return_value=result)
    monkeypatch.setattr(sources.services, name, command)
    if operation == "update":
        response = api.client.patch(
            f"/v1/admin/sources/{api.source.id}", json={"poll_interval_seconds": 3600}
        )
    else:
        response = api.client.post(
            f"/v1/admin/sources/{api.source.id}/{operation}",
            json={"decision": "approved", "note": "Reviewed engineering source"}
            if operation == "review"
            else None,
        )
    assert response.status_code == (202 if operation == "fetch" else 200), response.text
    assert command.call_args.args[:2] == (api.session, api.source.id)
    if operation == "review":
        decision = command.call_args.args[2]
        assert (
            decision.actor == api.admin.subject and decision.note == "Reviewed engineering source"
        )
        assert decision.decision == "approved"
    elif operation == "update":
        patch = command.call_args.args[2]
        assert (
            patch.model_fields_set == {"poll_interval_seconds"}
            and patch.poll_interval_seconds == 3600
        )
    else:
        assert response.json()["id"] == str(result.id) and response.json()["status"] == "queued"
    api.session.commit.assert_called_once_with()


@pytest.mark.parametrize("kind", ["enrichment", "missing-analysis"])
def test_pipeline_advance_uses_real_readiness_to_choose_one_explicit_recovery(
    processing_case, monkeypatch, kind
):
    article, evidence = processing_case
    article.review_status = "pending"
    if kind == "enrichment":
        article.summary = ""
    else:
        evidence["analysis"] = None
    readiness = derive_publication_readiness(article, **evidence)
    assert "advance" in pipeline._actions(readiness)
    monkeypatch.setattr(
        pipeline, "approved_sources", Mock(return_value=[article.origins[0].source_id])
    )
    monkeypatch.setattr(pipeline, "record", lambda *args, **kw: article)
    monkeypatch.setattr(pipeline, "publication_readiness", lambda *_: readiness)
    enrich, reconcile, detail = Mock(), Mock(), object()
    monkeypatch.setattr(pipeline, "request_article_enrichment", enrich)
    monkeypatch.setattr(pipeline, "reconcile_processing", reconcile)
    monkeypatch.setattr(pipeline, "_detail", lambda *_: detail)
    session = Mock()
    result = pipeline.advance(
        article.id, pipeline.AdvancePipeline(expected_revision=2), session, identity()
    )
    assert result is detail
    if kind == "enrichment":
        enrich.assert_called_once_with(session, article.id)
        reconcile.assert_not_called()
    else:
        reconcile.assert_called_once_with(session, article)
        enrich.assert_not_called()
    assert article.publication_status == "unpublished" and article.review_status == "pending"
    session.commit.assert_called_once_with()
    session.close.assert_called_once_with()


@pytest.mark.parametrize("barrier", ["stale", "complete"])
def test_pipeline_advance_refuses_stale_revision_or_completed_processing(
    processing_case, monkeypatch, barrier
):
    article, evidence = processing_case
    readiness = derive_publication_readiness(article, **evidence)
    monkeypatch.setattr(pipeline, "approved_sources", Mock())
    monkeypatch.setattr(pipeline, "record", lambda *args, **kw: article)
    monkeypatch.setattr(pipeline, "publication_readiness", lambda *_: readiness)
    command = Mock()
    monkeypatch.setattr(pipeline, "reconcile_processing", command)
    session = Mock()
    with pytest.raises(
        OperationConflict, match="reload" if barrier == "stale" else "No processing step"
    ):
        pipeline.advance(
            article.id,
            pipeline.AdvancePipeline(expected_revision=1 if barrier == "stale" else 2),
            session,
            identity(),
        )
    command.assert_not_called()
    session.commit.assert_not_called()


@pytest.mark.parametrize("conflict", [False, True])
def test_pipeline_retry_keeps_target_job_revision_and_actor_bound_to_the_request(
    processing_case, monkeypatch, conflict
):
    article, evidence = processing_case
    article.review_status = "pending"
    failed = evidence["analysis"]
    failed.id = uuid.uuid4()
    failed.status, failed.outcome, failed.error = "failed", None, "provider_unavailable"
    failed.editorial_revision, failed.attempts, failed.finished_at = 2, 3, NOW
    requested_id = evidence["analysis"].id
    command = Mock(
        return_value=article, side_effect=OperationConflict("Job changed") if conflict else None
    )
    monkeypatch.setattr(pipeline, "request_processing_retry", command)
    detail, session, admin = object(), Mock(), identity()
    monkeypatch.setattr(pipeline, "_detail", lambda *_: detail)
    body = pipeline.RetryPipeline(
        target="analysis", expected_revision=2, expected_job_id=requested_id
    )
    if conflict:
        with pytest.raises(OperationConflict, match="Job changed"):
            pipeline.retry(article.id, body, session, admin)
        session.commit.assert_not_called()
    else:
        assert pipeline.retry(article.id, body, session, admin) is detail
        session.commit.assert_called_once_with()
        session.close.assert_called_once_with()
    command.assert_called_once_with(
        session,
        article.id,
        target="analysis",
        expected_revision=2,
        expected_job_id=requested_id,
        actor=actor(admin),
    )


def topic_model(**changes):
    return Topic(
        **{
            "id": uuid.uuid4(),
            "name": "Angular",
            "slug": "angular",
            "kind": "framework",
            "aliases": [],
            "keywords": [],
            "description": "A framework for web applications.",
            "ai_description": None,
            "website_url": "https://angular.dev/",
            "logo_url": None,
            "managed_logo": {},
            "facts": [],
            "status": "active",
            "created_at": NOW - timedelta(days=1),
            "updated_at": NOW,
            **changes,
        }
    )


@pytest.mark.parametrize("protected", [False, True])
def test_topic_deactivation_checks_existing_classifications_before_delegating_save(
    monkeypatch, protected
):
    current = topic_model()
    output = copy.deepcopy(current)
    output.status = "rejected"
    monkeypatch.setattr(topics, "lock_topics", Mock())
    monkeypatch.setattr(topics, "record", lambda *_: current)
    references = Mock(
        side_effect=OperationConflict("Linked article classifications exist") if protected else None
    )
    monkeypatch.setattr(topics, "prohibit_references", references)
    command = Mock(return_value=output)
    monkeypatch.setattr(topics, "save_topic", command)
    with operator_api(topics) as api:
        body = {name: getattr(output, name) for name in topics.AdminTopicWrite.model_fields}
        response = api.client.put(f"/v1/admin/topics/{current.id}", json=body)
        assert response.status_code == (409 if protected else 200), response.text
        references.assert_called_once()
        if protected:
            command.assert_not_called()
            api.session.commit.assert_not_called()
        else:
            assert response.json()["status"] == "rejected"
            assert command.call_args.args[2] == current.id
            api.session.commit.assert_called_once_with()


@pytest.mark.parametrize("replacement", [None, "topic", "proposal"])
def test_topic_delete_delegates_replacement_and_authenticated_attribution(monkeypatch, replacement):
    identifier, substitute = uuid.uuid4(), uuid.uuid4()
    command = Mock()
    monkeypatch.setattr(topics, "delete_topic", command)
    with operator_api(topics) as api:
        params = {f"replacement_{replacement}_id": str(substitute)} if replacement else {}
        response = api.client.delete(f"/v1/admin/topics/{identifier}", params=params)
        assert response.status_code == 204 and response.content == b""
        command.assert_called_once_with(
            api.session,
            identifier,
            actor(api.admin),
            substitute if replacement == "topic" else None,
            substitute if replacement == "proposal" else None,
        )
        api.session.commit.assert_called_once_with()


@pytest.mark.parametrize("existing", [False, True])
def test_relationship_create_rejects_existing_identity_without_rewriting_its_evidence(
    monkeypatch, existing
):
    body = RelationWrite(
        topic_id=uuid.uuid4(),
        related_topic_id=uuid.uuid4(),
        relation="depends_on",
        evidence_url="https://docs.example/dependency",
    )
    current = TopicRelation(**body.model_dump())
    command = Mock(return_value=current)
    monkeypatch.setattr(topics, "relate_topics", command)
    with operator_api(topics) as api:
        api.session.get.return_value = current if existing else None
        response = api.client.post("/v1/admin/topic-relations", json=body.model_dump(mode="json"))
        assert response.status_code == (409 if existing else 201)
        if existing:
            command.assert_not_called()
            api.session.commit.assert_not_called()
        else:
            assert response.json()["evidence_url"] == body.evidence_url
            api.session.commit.assert_called_once_with()


def product_detail(account, product):
    return partner_accounts.ProductDetailOut(
        id=product.id,
        name=product.name,
        technologies=[],
        status="pending",
        excluded=False,
        globally_excluded=False,
        product_url=product.product_url,
        tagline=product.tagline,
        pricing="unknown",
        assessment={},
        revision=3,
        source="manual",
        editable=True,
        active=True,
        manual_active=True,
        account_id=account.id,
        account_name=account.name,
        effective_link_rel=["sponsored"],
        configurable_link_rel=False,
        link_rel_options=["sponsored"],
    )


@pytest.fixture
def products(monkeypatch):
    account = PartnerAccount(
        id=uuid.uuid4(), name="Engineering sponsor", status="active", tier="gold"
    )
    product = PartnerProduct(
        id=uuid.uuid4(),
        name="Developer toolkit",
        product_url="https://toolkit.example/",
        tagline="Tools for software engineers",
        status="pending",
        revision=3,
    )
    access = Mock(return_value=account)
    monkeypatch.setattr(partner_accounts, "account_access", access)
    scoped = Mock(return_value=(account, product))
    monkeypatch.setattr(partner_accounts, "write_manual_account", scoped)
    limiter = Mock()
    monkeypatch.setattr(partner_accounts, "limit_product_write", limiter)
    redis = object()
    monkeypatch.setattr(partner_accounts, "get_redis", lambda: redis)
    with operator_api(partner_accounts) as api:
        api.account, api.product, api.access, api.scoped, api.limiter, api.redis = (
            account,
            product,
            access,
            scoped,
            limiter,
            redis,
        )
        yield api


@pytest.mark.parametrize("operation", ["create", "update", "archive"])
def test_manual_product_commands_require_superuser_before_any_account_or_rate_work(
    products, monkeypatch, operation
):
    api = products
    api.admin.roles = ["admin"]
    base = f"/v1/admin/partner-accounts/{api.account.id}/products"
    if operation == "create":
        response = api.client.post(
            base,
            json={
                "name": api.product.name,
                "product_url": api.product.product_url,
                "tagline": api.product.tagline,
            },
        )
    elif operation == "update":
        response = api.client.patch(base + f"/{api.product.id}", json={"active": False})
    else:
        response = api.client.delete(base + f"/{api.product.id}")
    assert response.status_code == 403 and response.json()["detail"] == "Superuser access required"
    api.access.assert_not_called()
    api.limiter.assert_not_called()
    api.session.commit.assert_not_called()


@pytest.mark.parametrize("operation", ["create", "update", "archive"])
def test_manual_product_commands_forward_scoped_identity_and_rate_limit_before_dispatch(
    products, monkeypatch, operation
):
    api = products
    output = product_detail(api.account, api.product)
    name = {
        "create": "create_manual_product",
        "update": "update_manual_product",
        "archive": "archive_manual_product",
    }[operation]
    command = Mock(return_value=output)
    monkeypatch.setattr(partner_accounts, name, command)
    base = f"/v1/admin/partner-accounts/{api.account.id}/products"
    if operation == "create":
        output.revision = api.product.revision = 1
        api.scoped.return_value = (api.account, None)
        response = api.client.post(
            base,
            json={
                "name": api.product.name,
                "product_url": api.product.product_url,
                "tagline": api.product.tagline,
            },
        )
        api.scoped.assert_called_once_with(
            api.session, api.admin, api.account.id, url=api.product.product_url
        )
        assert command.call_args.args[2].product_url == api.product.product_url
    elif operation == "update":
        response = api.client.patch(
            base + f"/{api.product.id}", json={"name": api.product.name, "expected_revision": 3}
        )
        assert command.call_args.args[:3] == (api.session, api.account, api.product)
        assert command.call_args.args[3].expected_revision == 3
    else:
        response = api.client.delete(base + f"/{api.product.id}")
        command.assert_called_once_with(api.session, api.account, api.product)
    assert response.status_code == (
        201 if operation == "create" else 204 if operation == "archive" else 200
    ), response.text
    api.limiter.assert_called_once_with(api.redis, api.admin, api.account.id)
    api.session.commit.assert_called_once_with()


def test_product_command_rate_limit_is_preserved_without_reading_or_dispatching_manual_identity(
    products, monkeypatch
):
    api = products
    api.limiter.side_effect = HTTPException(
        429, "Too many product changes", headers={"Retry-After": "17"}
    )
    command = Mock()
    monkeypatch.setattr(partner_accounts, "archive_manual_product", command)
    response = api.client.delete(
        f"/v1/admin/partner-accounts/{api.account.id}/products/{api.product.id}"
    )
    assert response.status_code == 429 and response.headers["Retry-After"] == "17"
    api.scoped.assert_not_called()
    command.assert_not_called()
    api.session.commit.assert_not_called()
