"""Qualification renews before expiry without interrupting valid manual placements."""

import uuid
from datetime import timedelta

import pytest
from devfeed_aggregator import partner_sync_tasks
from devfeed_core import partner_connections
from devfeed_core.config import get_settings
from devfeed_core.models import (
    PartnerAccount,
    PartnerArticleMatch,
    PartnerConnection,
    PartnerListing,
    PartnerManualProduct,
    PartnerPipelineJob,
    PartnerProduct,
    utcnow,
)
from devfeed_core.partner_promotions import ensure_automatic_assets
from devfeed_core.partner_tools import eligible
from sqlalchemy import delete, func, select

from tests.support.partner_placements import catalog as catalog
from tests.support.partner_placements import deliver, finish_matching, query

pytestmark = pytest.mark.integration


@pytest.fixture
def clock(monkeypatch):
    # Model defaults retain their original clock; newly queued jobs must already be due.
    value = [utcnow() + timedelta(seconds=5)]
    monkeypatch.setattr(partner_sync_tasks, "utcnow", lambda: value[0])
    monkeypatch.setattr(partner_connections, "utcnow", lambda: value[0])
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    return value


@pytest.fixture
def manual_catalog(database, catalog):
    with database.begin() as session:
        for account, key in (
            ("free", "free_product"),
            ("paid", "paid_product"),
            ("paid", "unrelated"),
        ):
            product = session.get(PartnerProduct, catalog[key])
            product.assessment_revision = product.revision
            product.assessment = {"state": "qualified", "reason": "Verified capabilities."}
            session.add(PartnerManualProduct(account_id=catalog[account], product_id=product.id))
        session.flush()
        session.execute(delete(PartnerListing))
        session.execute(delete(PartnerConnection))
        session.execute(delete(PartnerPipelineJob))
        ensure_automatic_assets(session)
    finish_matching(database)
    return catalog


def age_product(database, product_id, now, age):
    with database.begin() as session:
        session.get(PartnerProduct, product_id).verified_at = now - age


def assessment_jobs(database, product_id=None):
    with database() as session:
        statement = select(PartnerPipelineJob).where(PartnerPipelineJob.operation == "assess")
        if product_id is not None:
            statement = statement.where(PartnerPipelineJob.product_id == product_id)
        return session.scalars(statement.order_by(PartnerPipelineJob.created_at)).all()


def qualify(database, job_id, *, decision="qualified"):
    with database() as session:
        job = session.get(PartnerPipelineJob, job_id)
        product = session.get(PartnerProduct, job.product_id)
        page = {"final_url": product.product_url, "text": product.evidence[0]["quote"]}
        answer = {
            "decision": decision,
            "reason": "Fresh official website evidence.",
            "technologies": product.technologies if decision == "qualified" else [],
            "evidence": product.evidence if decision == "qualified" else [],
        }
    partner_sync_tasks.process_pipeline(
        str(job_id),
        factory=database,
        page_fetcher=lambda *_: page,
        assessor=lambda *_: answer,
    )


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (timedelta(days=83) - timedelta(microseconds=1), False),
        (timedelta(days=83), True),
        (timedelta(days=84), True),
        (timedelta(days=91), True),
    ],
    ids=["fresh", "renewal-boundary", "renewal-due", "already-expired"],
)
def test_manual_qualification_renews_before_expiry(database, manual_catalog, clock, age, expected):
    product_id = manual_catalog["free_product"]
    age_product(database, product_id, clock[0], age)
    partner_sync_tasks.schedule_partner_syncs(database)
    jobs = assessment_jobs(database)
    assert len(jobs) == int(expected)
    if expected:
        assert jobs[0].product_id == product_id and jobs[0].provider is None
        assert jobs[0].status == "queued" and jobs[0].payload["revision"] == 1


def test_queued_renewal_preserves_approval_and_prepared_delivery(database, manual_catalog, clock):
    product_id = manual_catalog["free_product"]
    age_product(database, product_id, clock[0], timedelta(days=84))
    finish_matching(database)
    fields = (
        "status",
        "verified_at",
        "revision",
        "assessment_revision",
        "updated_at",
        "assessment",
        "technologies",
        "evidence",
    )
    with database() as session:
        product = session.get(PartnerProduct, product_id)
        before = {field: getattr(product, field) for field in fields}
        match = session.get(PartnerArticleMatch, (product_id, manual_catalog["article"]))
        assert match is not None and match.relevant
        prepared = (match.product_revision, match.product_updated_at, match.updated_at)
    before_names = {item["name"] for item in deliver(database, query(manual_catalog))}
    assert "free_product" in before_names
    partner_sync_tasks.schedule_partner_syncs(database)
    partner_sync_tasks.schedule_partner_syncs(database)
    assert len(assessment_jobs(database, product_id)) == 1
    with database() as session:
        product = session.get(PartnerProduct, product_id)
        assert {field: getattr(product, field) for field in fields} == before
        assert eligible(product, clock[0])
        match = session.get(PartnerArticleMatch, (product_id, manual_catalog["article"]))
        assert match is not None
        assert (match.product_revision, match.product_updated_at, match.updated_at) == prepared
    assert {item["name"] for item in deliver(database, query(manual_catalog))} == before_names


def test_expired_manual_qualification_recovers_through_worker(database, manual_catalog, clock):
    product_id = manual_catalog["free_product"]
    age_product(database, product_id, clock[0], timedelta(days=91))
    assert "free_product" not in {item["name"] for item in deliver(database, query(manual_catalog))}
    partner_sync_tasks.schedule_partner_syncs(database)
    jobs = assessment_jobs(database, product_id)
    assert len(jobs) == 1, "Expired manual products need a renewal without an import connection"
    qualify(database, jobs[0].id)
    with database() as session:
        product = session.get(PartnerProduct, product_id)
        assert product.status == "approved" and product.verified_at == clock[0]
        assert product.assessment_revision == product.revision == 1
        assert eligible(product, clock[0])
        assert session.get(PartnerPipelineJob, jobs[0].id).status == "succeeded"
        assert session.scalar(select(func.count()).select_from(PartnerConnection)) == 0
    finish_matching(database)
    assert "free_product" in {item["name"] for item in deliver(database, query(manual_catalog))}
    partner_sync_tasks.schedule_partner_syncs(database)
    assert len(assessment_jobs(database, product_id)) == 1


@pytest.mark.parametrize("decision", ["irrelevant", "uncertain"])
def test_negative_renewal_revokes_delivery(database, manual_catalog, clock, decision):
    product_id = manual_catalog["free_product"]
    age_product(database, product_id, clock[0], timedelta(days=84))
    partner_sync_tasks.schedule_partner_syncs(database)
    jobs = assessment_jobs(database, product_id)
    assert len(jobs) == 1
    qualify(database, jobs[0].id, decision=decision)
    with database() as session:
        product = session.get(PartnerProduct, product_id)
        assert product.status == ("rejected" if decision == "irrelevant" else "pending")
        assert product.verified_at is None and not eligible(product, clock[0])
        assert product.evidence == [] and product.technologies == []
    assert "free_product" not in {item["name"] for item in deliver(database, query(manual_catalog))}
    partner_sync_tasks.schedule_partner_syncs(database)
    assert len(assessment_jobs(database, product_id)) == 1


@pytest.mark.parametrize(
    "unavailable", ["archived", "excluded", "merged", "withdrawn", "rejected", "paused"]
)
def test_unavailable_manual_products_do_not_receive_renewal(
    database, manual_catalog, clock, unavailable
):
    product_id = manual_catalog["free_product"]
    with database.begin() as session:
        product = session.get(PartnerProduct, product_id)
        product.verified_at = clock[0] - timedelta(days=91)
        if unavailable == "archived":
            session.get(PartnerManualProduct, (manual_catalog["free"], product_id)).active = False
        elif unavailable == "excluded":
            product.excluded = True
        elif unavailable == "merged":
            product.merged_into_id = manual_catalog["unrelated"]
        else:
            product.status = unavailable
    partner_sync_tasks.schedule_partner_syncs(database)
    assert assessment_jobs(database, product_id) == []


def test_renewal_failures_keep_valid_approval_and_retry_after_cooldown(
    database, manual_catalog, clock
):
    product_id = manual_catalog["free_product"]
    age_product(database, product_id, clock[0], timedelta(days=84))
    partner_sync_tasks.schedule_partner_syncs(database)
    jobs = assessment_jobs(database, product_id)
    assert len(jobs) == 1
    job_id = jobs[0].id

    def unavailable(*_):
        raise RuntimeError("The official website is temporarily unavailable")

    for attempt in range(1, 4):
        partner_sync_tasks.process_pipeline(str(job_id), factory=database, page_fetcher=unavailable)
        with database() as session:
            job = session.get(PartnerPipelineJob, job_id)
            product = session.get(PartnerProduct, product_id)
            assert product.status == "approved" and eligible(product, clock[0])
            assert job.attempts == attempt
            assert job.status == ("queued" if attempt < 3 else "failed")
            if attempt < 3:
                clock[0] = job.available_at + timedelta(microseconds=1)
            else:
                failed_at = job.finished_at
        partner_sync_tasks.schedule_partner_syncs(database)
        assert len(assessment_jobs(database, product_id)) == 1

    clock[0] = failed_at + timedelta(hours=24) - timedelta(microseconds=1)
    partner_sync_tasks.schedule_partner_syncs(database)
    assert len(assessment_jobs(database, product_id)) == 1
    clock[0] += timedelta(microseconds=1)
    partner_sync_tasks.schedule_partner_syncs(database)
    jobs = assessment_jobs(database, product_id)
    assert len(jobs) == 2 and jobs[1].status == "queued"
    qualify(database, jobs[1].id)
    with database() as session:
        product = session.get(PartnerProduct, product_id)
        assert product.verified_at == clock[0] and eligible(product, clock[0])


def test_imported_products_share_scheduled_qualification_renewal(database, catalog, clock):
    with database.begin() as session:
        for connection in session.scalars(select(PartnerConnection)):
            connection.next_sync_at = clock[0] + timedelta(days=1)
        for product in session.scalars(select(PartnerProduct)):
            product.assessment_revision = product.revision
        session.get(PartnerProduct, catalog["free_product"]).verified_at = clock[0] - timedelta(
            days=84
        )
        session.execute(delete(PartnerPipelineJob))
    partner_sync_tasks.schedule_partner_syncs(database)
    jobs = assessment_jobs(database)
    assert len(jobs) == 1 and jobs[0].product_id == catalog["free_product"]
    assert jobs[0].provider == "free"


def test_changed_revision_bypasses_previous_revision_failure_cooldown(
    database, manual_catalog, clock
):
    product_id = manual_catalog["free_product"]
    with database.begin() as session:
        product = session.get(PartnerProduct, product_id)
        product.verified_at = clock[0] - timedelta(days=84)
        session.add(
            PartnerPipelineJob(
                operation="assess",
                product_id=product_id,
                status="failed",
                payload={"revision": product.revision},
                finished_at=clock[0],
            )
        )
        product.revision += 1
    partner_sync_tasks.schedule_partner_syncs(database)
    jobs = assessment_jobs(database, product_id)
    assert len(jobs) == 2
    assert jobs[1].status == "queued" and jobs[1].payload["revision"] == 2
    with database() as session:
        product = session.get(PartnerProduct, product_id)
        assert product.status == "pending" and product.verified_at is None
        assert product.assessment_revision == 0


def test_running_renewal_is_not_queued_again(database, manual_catalog, clock):
    product_id = manual_catalog["free_product"]
    age_product(database, product_id, clock[0], timedelta(days=84))
    partner_sync_tasks.schedule_partner_syncs(database)
    jobs = assessment_jobs(database, product_id)
    assert len(jobs) == 1
    with database.begin() as session:
        job = session.get(PartnerPipelineJob, jobs[0].id)
        job.status = "running"
        job.lease_token = uuid.uuid4()
        job.lease_until = clock[0] + timedelta(minutes=5)
    partner_sync_tasks.schedule_partner_syncs(database)
    jobs = assessment_jobs(database, product_id)
    assert len(jobs) == 1 and jobs[0].status == "running"


def test_disabled_ai_keeps_renewal_queued_and_existing_approval(
    database, manual_catalog, clock, monkeypatch
):
    product_id = manual_catalog["free_product"]
    age_product(database, product_id, clock[0], timedelta(days=84))
    monkeypatch.setattr(get_settings(), "ai_enabled", False)
    partner_sync_tasks.schedule_partner_syncs(database)
    jobs = assessment_jobs(database, product_id)
    assert len(jobs) == 1
    partner_sync_tasks.process_pipeline(
        str(jobs[0].id),
        factory=database,
        page_fetcher=lambda *_: pytest.fail("Disabled AI must not fetch qualification evidence"),
    )
    with database() as session:
        job = session.get(PartnerPipelineJob, jobs[0].id)
        product = session.get(PartnerProduct, product_id)
        assert job.status == "queued" and job.attempts == 0
        assert product.status == "approved" and eligible(product, clock[0])
        assert product.verified_at == clock[0] - timedelta(days=84)


def test_queued_renewal_does_not_extend_the_ninety_day_serving_window(
    database, manual_catalog, clock
):
    product_id = manual_catalog["free_product"]
    age_product(database, product_id, clock[0], timedelta(days=84))
    finish_matching(database)
    partner_sync_tasks.schedule_partner_syncs(database)
    assert len(assessment_jobs(database, product_id)) == 1
    placement = query(manual_catalog, exclude_product_ids=[manual_catalog["paid_product"]])
    clock[0] += timedelta(days=6)
    with database() as session:
        assert eligible(session.get(PartnerProduct, product_id), clock[0])
    assert "free_product" in {item["name"] for item in deliver(database, placement, now=clock[0])}
    clock[0] += timedelta(microseconds=1)
    with database() as session:
        product = session.get(PartnerProduct, product_id)
        assert product.status == "approved" and not eligible(product, clock[0])
    assert "free_product" not in {
        item["name"] for item in deliver(database, placement, now=clock[0])
    }
    partner_sync_tasks.schedule_partner_syncs(database)
    assert len(assessment_jobs(database, product_id)) == 1


def test_renewal_batches_are_bounded_and_do_not_starve_remaining_products(database, clock):
    product_ids = []
    with database.begin() as session:
        account = PartnerAccount(name="Independent catalog", tier="gold")
        session.add(account)
        session.flush()
        for index in range(105):
            product = PartnerProduct(
                id=uuid.uuid4(),
                name=f"Manual product {index}",
                product_url=f"https://tools.example/independent-{index}",
                tagline="A developer product with reviewed capabilities.",
                status="approved",
                verified_at=clock[0] - timedelta(days=84),
                revision=1,
                assessment_revision=1,
            )
            session.add(product)
            session.flush()
            session.add(PartnerManualProduct(account_id=account.id, product_id=product.id))
            product_ids.append(product.id)
    partner_sync_tasks.schedule_partner_syncs(database)
    assert len(assessment_jobs(database)) == 100
    partner_sync_tasks.schedule_partner_syncs(database)
    jobs = assessment_jobs(database)
    assert len(jobs) == 105 and {job.product_id for job in jobs} == set(product_ids)
    partner_sync_tasks.schedule_partner_syncs(database)
    assert len(assessment_jobs(database)) == 105
