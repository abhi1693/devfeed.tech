"""Promoted artifacts become ordinary facts; private owner histories stay isolated."""

import copy
import uuid
from datetime import timedelta

import pytest
from devfeed_core import analysis, analytics_article_cards, article_lifecycle, job_retries
from devfeed_core.article_refresh_jobs import ordinary_job_condition
from devfeed_core.config import get_settings
from devfeed_core.models import (
    Article,
    ArticleAnalysisJob,
    ArticleEnrichmentJob,
    ArticleImageJob,
    ArticleOrigin,
    ArticleRefresh,
    Source,
    utcnow,
)
from devfeed_core.services import OperationConflict
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integration
LANES = (
    ("analysis", ArticleAnalysisJob, None),
    ("enrichment", ArticleEnrichmentJob, None),
    ("discovery", ArticleImageJob, "discover"),
    ("storage", ArticleImageJob, "store"),
)
IMAGE = "https://publisher.example/approved.png"


def article(session):
    url = f"https://publisher.example/{uuid.uuid4()}"
    source = Source(
        name="Approved ownership publisher",
        feed_url=url + "/feed",
        source_type="publisher",
        approval_status="approved",
        enabled=True,
    )
    value = Article(
        canonical_url=url,
        url_hash=uuid.uuid4().hex,
        title="Approved public runtime guide",
        summary="The current approved article preview.",
        image_url=IMAGE,
        publication_status="published",
        review_status="approved",
        published_to_feed_at=utcnow() - timedelta(days=3),
        origins=[ArticleOrigin(source=source, entry_key="ownership", original_url=url)],
    )
    session.add(value)
    session.flush()
    return value, source


def owner(session, value, status):
    refresh = ArticleRefresh(
        article_id=value.id,
        target="analysis",
        status=status,
        base_revision=value.editorial_revision,
        base_hash="a" * 64,
        payload={"private": "Not ordinary processing evidence."},
        requested_by={"subject": "ownership-test"},
        image_url=IMAGE,
    )
    session.add(refresh)
    session.flush()
    return refresh


def job(session, lane, value, *, refresh=None, status="succeeded", age=0):
    slot, model, operation = lane
    values = {
        "article_id": value.id,
        "refresh_id": refresh.id if refresh is not None else None,
        "status": status,
        "created_at": utcnow() + timedelta(seconds=age),
    }
    if operation is not None:
        values.update(
            operation=operation,
            image_url=IMAGE if operation == "store" else None,
            storage={"input_url": value.canonical_url} if operation == "discover" else {},
        )
    result = model(**values)
    session.add(result)
    session.flush()
    if refresh is not None:
        setattr(refresh, f"{slot}_job_id", result.id)
    return result


@pytest.mark.parametrize("status", ["active", "review", "cancelled", "superseded", "promoted"])
def test_only_selected_promoted_jobs_enter_ordinary_latest_and_readiness(database, status):
    with database.begin() as session:
        value, _ = article(session)
        refresh = owner(session, value, status)
        old, bound = {}, {}
        for lane in LANES:
            old[lane[0]] = job(session, lane, value, status="failed", age=-60).id
            state = (
                "queued" if status in {"active", "review"} and lane[0] != "storage" else "succeeded"
            )
            bound[lane[0]] = job(session, lane, value, refresh=refresh, status=state).id
        identifier, refresh_id = value.id, refresh.id
    expected = bound if status == "promoted" else old

    def assert_current(session):
        value = session.get(Article, identifier)
        facts = article_lifecycle._processing_facts(session, value, {"topics": [], "tags": []})
        for slot, model, operation in LANES:
            current = analytics_article_cards._latest(
                session, model, [identifier], operation=operation, lightweight=True
            )
            assert current[identifier].id == expected[slot]
            assert facts[slot].id == expected[slot]
            visible = set(
                session.scalars(
                    select(model.id).where(
                        model.article_id == identifier, ordinary_job_condition(model)
                    )
                )
            )
            assert old[slot] in visible
            assert (bound[slot] in visible) == (status == "promoted")

    with database() as session:
        assert_current(session)
    with database.begin() as session:
        session.execute(
            update(ArticleRefresh)
            .where(ArticleRefresh.id == refresh_id)
            .values(payload={}, payload_pruned_at=utcnow())
        )
    with database() as session:
        assert_current(session)
    # Compact owner identity must survive body pruning, never turn its jobs unbound.
    with pytest.raises(IntegrityError), database.begin() as session:
        session.execute(delete(ArticleRefresh).where(ArticleRefresh.id == refresh_id))
    with database() as session:
        assert_current(session)


@pytest.mark.parametrize("lane", LANES, ids=[lane[0] for lane in LANES])
def test_private_retry_uses_its_selected_owner_not_other_job_histories(database, lane):
    with database.begin() as session:
        value, source = article(session)
        refresh = owner(session, value, "active")
        selected = job(session, lane, value, refresh=refresh, status="failed", age=-60)
        foreign = owner(session, value, "cancelled")
        job(session, lane, value, refresh=foreign, age=60)
        job(session, lane, value, age=120)
        identifier, source_id, refresh_id, job_id = value.id, source.id, refresh.id, selected.id
        original_storage = copy.deepcopy(selected.storage) if lane[1] is ArticleImageJob else None
    _, model, operation = lane

    def eligible():
        with database() as session:
            return (
                session.scalar(
                    select(model.id).where(model.id == job_id, job_retries.retry_candidate(model))
                )
                is not None
            )

    assert eligible()
    for status, expected in (
        ("review", True),
        ("cancelled", False),
        ("superseded", False),
        ("promoted", False),
        ("active", True),
    ):
        with database.begin() as session:
            session.execute(
                update(ArticleRefresh).where(ArticleRefresh.id == refresh_id).values(status=status)
            )
        assert eligible() is expected
    with database.begin() as session:
        session.get(Source, source_id).approval_status = "rejected"
    assert not eligible()
    with database.begin() as session:
        session.get(Source, source_id).approval_status = "approved"
        session.get(Article, identifier).editorial_revision += 1
    assert not eligible()
    with database.begin() as session:
        session.get(Article, identifier).editorial_revision -= 1
        session.execute(
            update(ArticleRefresh)
            .where(ArticleRefresh.id == refresh_id)
            .values(**{f"{lane[0]}_job_id": None})
        )
    assert not eligible()
    with database.begin() as session:
        session.execute(
            update(ArticleRefresh)
            .where(ArticleRefresh.id == refresh_id)
            .values(**{f"{lane[0]}_job_id": job_id})
        )
        session.get(Article, identifier).publication_status = "unpublished"
    assert not eligible()
    with database.begin() as session:
        session.get(Article, identifier).publication_status = "published"
    assert eligible()
    if operation == "store":
        with database.begin() as session:
            session.execute(
                update(ArticleRefresh)
                .where(ArticleRefresh.id == refresh_id)
                .values(image_url=IMAGE + "?new")
            )
        assert not eligible()
    elif operation == "discover":
        with database.begin() as session:
            session.get(ArticleImageJob, job_id).storage = {
                "input_url": "https://other.example/article"
            }
        assert not eligible()
        with database.begin() as session:
            session.get(ArticleImageJob, job_id).storage = original_storage
        assert eligible()


@pytest.mark.parametrize("lane", LANES, ids=[lane[0] for lane in LANES])
def test_a_promoted_owner_cannot_make_a_different_articles_job_ordinary(database, lane):
    with database.begin() as session:
        approved, _ = article(session)
        unrelated, _ = article(session)
        promoted = owner(session, approved, "promoted")
        original = job(session, lane, unrelated, age=-60)
        mismatched = job(session, lane, unrelated, refresh=promoted)
        identifier, original_id, mismatched_id = unrelated.id, original.id, mismatched.id
    _, model, operation = lane
    with database() as session:
        assert (
            session.scalar(
                select(model.id).where(model.id == mismatched_id, ordinary_job_condition(model))
            )
            is None
        )
        assert (
            analytics_article_cards._latest(session, model, [identifier], operation=operation)[
                identifier
            ].id
            == original_id
        )


def test_image_discovery_requires_its_own_promoted_slot(database):
    with database.begin() as session:
        value, _ = article(session)
        promoted = owner(session, value, "promoted")
        original = job(session, LANES[2], value, age=-60)
        candidate = job(session, LANES[2], value, refresh=promoted)
        promoted.discovery_job_id, promoted.storage_job_id = None, candidate.id
        identifier, original_id, candidate_id = value.id, original.id, candidate.id
    with database() as session:
        assert (
            session.scalar(
                select(ArticleImageJob.id).where(
                    ArticleImageJob.id == candidate_id, ordinary_job_condition(ArticleImageJob)
                )
            )
            is None
        )
        assert (
            analytics_article_cards._latest(
                session, ArticleImageJob, [identifier], operation="discover"
            )[identifier].id
            == original_id
        )


def test_promotion_replaces_ordinary_failure_facts_without_reopening_published_work(database):
    with database.begin() as session:
        value, _ = article(session)
        previous = job(session, LANES[0], value, status="failed", age=-60)
        refresh = owner(session, value, "active")
        replacement = job(session, LANES[0], value, refresh=refresh)
        identifier, previous_id, replacement_id, refresh_id = (
            value.id,
            previous.id,
            replacement.id,
            refresh.id,
        )
    with database() as session:
        assert (
            analytics_article_cards._latest(session, ArticleAnalysisJob, [identifier])[
                identifier
            ].id
            == previous_id
        )
    with database.begin() as session:
        session.execute(
            update(ArticleRefresh).where(ArticleRefresh.id == refresh_id).values(status="promoted")
        )
    with database.begin() as session:
        assert (
            analytics_article_cards._latest(session, ArticleAnalysisJob, [identifier])[
                identifier
            ].id
            == replacement_id
        )
        assert (
            session.scalar(
                select(ArticleAnalysisJob.id).where(
                    ArticleAnalysisJob.id == previous_id,
                    job_retries.retry_candidate(ArticleAnalysisJob),
                )
            )
            is None
        )
        count = session.scalar(select(func.count()).select_from(ArticleAnalysisJob))
        article_before = session.get(Article, identifier)
        revision = article_before.editorial_revision
        with pytest.raises(OperationConflict):
            job_retries.retry_failed_job(
                session,
                session.get(ArticleAnalysisJob, previous_id),
                "analysis",
                {"subject": "retry-test"},
            )
        assert session.scalar(select(func.count()).select_from(ArticleAnalysisJob)) == count
        assert (
            article_before.publication_status == "published"
            and article_before.editorial_revision == revision
        )


def test_cancelled_private_success_cannot_hide_or_supply_an_ordinary_retry(database, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    with database.begin() as session:
        value, _ = article(session)
        failed = job(session, LANES[0], value, status="failed", age=-60)
        cancelled = owner(session, value, "cancelled")
        private = job(session, LANES[0], value, refresh=cancelled, age=60)
        snapshot = analysis.source_snapshot(value, None)
        candidates = analysis.analysis_candidates(analysis.catalog(session), snapshot)
        private.input_snapshot, private.input_hash = snapshot, analysis.snapshot_hash(snapshot)
        private.catalog_snapshot = candidates
        private.catalog_hash = analysis.snapshot_hash(candidates)
        private.prompt_version = analysis.PROMPT_VERSION
        private.editorial_revision = value.editorial_revision
        private.outcome = "applied"
        private.result = {"private": "Never reuse this unapproved candidate output."}
        value.publication_status, value.review_status = "unpublished", "pending"
        identifier, failed_id, private_id = value.id, failed.id, private.id
    with database.begin() as session:
        assert (
            session.scalar(
                select(ArticleAnalysisJob.id).where(
                    ArticleAnalysisJob.id == failed_id,
                    job_retries.retry_candidate(ArticleAnalysisJob),
                )
            )
            == failed_id
        )
        successor = job_retries.retry_failed_job(
            session,
            session.get(ArticleAnalysisJob, failed_id),
            "analysis",
            {"subject": "ordinary-retry"},
        )
        assert successor.id not in {failed_id, private_id}
        assert successor.refresh_id is None and successor.status == "queued"
        assert not successor.result and not successor.steps
    with database() as session:
        assert session.get(Article, identifier).publication_status == "unpublished"
        assert session.get(ArticleAnalysisJob, private_id).refresh_id is not None
        assert session.get(ArticleAnalysisJob, private_id).result == {
            "private": "Never reuse this unapproved candidate output."
        }


@pytest.mark.parametrize(
    "status,selected",
    [
        ("active", True),
        ("review", True),
        ("cancelled", True),
        ("superseded", True),
        ("promoted", False),
        ("promoted", True),
    ],
)
def test_ordinary_automation_and_paywall_policy_use_only_public_job_history(
    database, admin_client, monkeypatch, status, selected
):
    from devfeed_admin_api import automation as recovery
    from devfeed_core import analytics_blockers, article_automation, publication_policy
    from devfeed_core.models import ArticlePublicationDecision

    from tests.admin.articles import test_article_pipeline as pipeline_fixture

    settings = get_settings()
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "full_automation", True)
    monkeypatch.setattr(settings, "ai_content_not_before", None)
    promoted = status == "promoted" and selected
    with database.begin() as session:
        value = pipeline_fixture.make_article(session, state="review")
        value.content_type, value.content_format = "tutorial", "article"
        value.automation_next_check_at = utcnow() - timedelta(seconds=1)
        ordinary = session.scalar(
            select(ArticleAnalysisJob).where(ArticleAnalysisJob.article_id == value.id)
        )
        ordinary_enrichment = session.scalar(
            select(ArticleEnrichmentJob).where(ArticleEnrichmentJob.article_id == value.id)
        )
        for previous in (ordinary, ordinary_enrichment):
            previous.created_at = utcnow() - timedelta(minutes=2)
        refresh = owner(session, value, status)
        private_analysis = job(
            session, LANES[0], value, refresh=refresh, status="succeeded" if promoted else "failed"
        )
        private_enrichment = job(session, LANES[1], value, refresh=refresh)
        private_enrichment.outcome = "enriched"
        private_enrichment.result = {
            "input_url": value.canonical_url,
            "image_url": None,
            "paywalled": not promoted,
        }
        private_analysis.error = None if promoted else "private_replacement_failed"
        for field in (
            "input_hash",
            "input_snapshot",
            "catalog_hash",
            "catalog_snapshot",
            "prompt_version",
            "editorial_revision",
            "model",
            "result",
            "outcome",
        ):
            setattr(private_analysis, field, copy.deepcopy(getattr(ordinary, field)))
        if promoted:
            value.classification_provenance = {
                **value.classification_provenance,
                "analysis_id": str(private_analysis.id),
            }
        if not selected:
            refresh.analysis_job_id = refresh.enrichment_job_id = None
        identifier = value.id
        expected = private_analysis.id if promoted else ordinary.id
    with database() as session:
        value = session.get(Article, identifier)
        ordinary_job = session.get(ArticleAnalysisJob, expected)
        decision = publication_policy.evaluate_publication(session, value, ordinary_job)
        assert decision["status"] == "would_publish", decision
        blockers = analytics_blockers.automation_blocker_rows(session, [identifier])
        codes = {row["code"] for row in blockers.get(identifier, [])}
        assert not {"paywalled_content", "analysis_failed"} & codes
    assert article_automation.reject_known_paywalled_articles(database) == 0
    evaluated = []

    def capture_policy(_session, _article, selected_job):
        evaluated.append(selected_job.id)
        return {"status": "blocked", "reasons": ["boundary-check"]}

    with monkeypatch.context() as endpoint:
        endpoint.setattr(recovery, "apply_publication_policy", capture_policy)
        response = admin_client.post(
            f"/v1/admin/automation/articles/{identifier}/evaluate",
            json={"expected_revision": 0},
        )
        assert response.status_code == 200, response.text
        assert evaluated == [expected]
    result = article_automation.schedule_article_automation(database)
    assert result["articles_published"] == 1
    with database() as session:
        assert session.get(Article, identifier).publication_status == "published"
        decisions = session.scalars(
            select(ArticlePublicationDecision).where(
                ArticlePublicationDecision.article_id == identifier
            )
        ).all()
        assert decisions and all(
            item.decision["analysis_id"] == str(expected) for item in decisions
        )


@pytest.mark.parametrize(
    "status,selected",
    [
        ("active", True),
        ("review", True),
        ("cancelled", True),
        ("superseded", True),
        ("promoted", False),
        ("promoted", True),
    ],
)
def test_enrichment_backfill_ignores_private_attempts_but_keeps_promoted_selected_base(
    database, status, selected
):
    from devfeed_core.article_jobs import backfill_articles

    with database.begin() as session:
        value, _ = article(session)
        value.publication_status, value.review_status = "unpublished", "pending"
        refresh = owner(session, value, status)
        private = job(session, LANES[1], value, refresh=refresh)
        if not selected:
            refresh.enrichment_job_id = None
        identifier, private_id = value.id, private.id
    with database.begin() as session:
        submitted = backfill_articles(session, 100)
        if status == "promoted" and selected:
            assert submitted == []
        else:
            assert len(submitted) == 1
            assert submitted[0].article_id == identifier
            assert submitted[0].refresh_id is None and submitted[0].id != private_id


@pytest.mark.parametrize("status", ["active", "review", "cancelled", "superseded", "promoted"])
def test_private_latest_analysis_cannot_hide_an_ordinary_negative_publication_decision(
    database, monkeypatch, status
):
    from devfeed_core import publication_policy

    from tests.admin.articles import test_article_pipeline as pipeline_fixture

    settings = get_settings()
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "full_automation", True)
    with database.begin() as session:
        value = pipeline_fixture.make_article(session, state="review")
        value.content_type, value.content_format = "tutorial", "article"
        current = session.scalar(
            select(ArticleAnalysisJob).where(ArticleAnalysisJob.article_id == value.id)
        )
        current.created_at = utcnow() - timedelta(minutes=2)
        current.result = {**current.result, "developer_relevance": "unrelated"}
        refresh = owner(session, value, status)
        private = job(session, LANES[0], value, refresh=refresh, status="failed")
        if status == "promoted":
            refresh.analysis_job_id = None
        expected = current.id
        decision = publication_policy.apply_publication_policy(session, value, current)
        assert decision["status"] == "rejected", decision
        assert decision["analysis_id"] == str(expected)
        assert value.review_status == "rejected" and private.id != expected
