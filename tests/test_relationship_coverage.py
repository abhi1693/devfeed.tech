"""Durable, incremental coverage against real PostgreSQL transactions."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from devfeed_aggregator import scheduler
from devfeed_aggregator.queue import get_queue
from devfeed_core import relationship_coverage
from devfeed_core.analysis import fail_analysis
from devfeed_core.automation_scheduler import schedule_automation
from devfeed_core.config import get_settings
from devfeed_core.job_lifecycle import finish_job
from devfeed_core.models import Topic, TopicAnalysisJob, TopicRelationshipScan, utcnow
from devfeed_core.topic_analysis import request_topic_analysis
from devfeed_core.topic_relationships import (
    RelationshipAnalysisRequest,
    request_relationship_analysis,
    topic_snapshot,
)
from sqlalchemy import delete, func, insert, select
from test_topic_analysis import pending as pending
from test_topic_analysis import prepare_relationship_catalog, run_metadata_research
from test_topic_relationships import execute

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def automatic(monkeypatch, unit_test_settings):
    monkeypatch.setenv("DEVFEED_AI_ENABLED", "true")
    monkeypatch.setenv("DEVFEED_AUTO_RESEARCH_RELATIONSHIPS", "true")
    monkeypatch.setenv("DEVFEED_RELATIONSHIP_RESEARCH_BATCH_SIZE", "1")
    monkeypatch.setenv("DEVFEED_RELATIONSHIP_RESEARCH_MAX_PENDING", "2")
    monkeypatch.setenv("DEVFEED_CODEX_APP_SERVER_URL", "ws://127.0.0.1:4555")
    monkeypatch.setenv("DEVFEED_CODEX_MODEL", "test-model")
    get_settings.cache_clear()


def add_topic(database, name, *, number=None, status="active"):
    with database.begin() as session:
        topic = Topic(
            id=uuid.UUID(int=number) if number else uuid.uuid4(),
            name=name,
            slug=name.lower(),
            kind="technology",
            status=status,
        )
        session.add(topic)
        session.flush()
        return topic.id


def jobs(database, status=None):
    with database() as session:
        query = select(TopicAnalysisJob).where(TopicAnalysisJob.topic_id.is_not(None))
        if status:
            query = query.where(TopicAnalysisJob.status == status)
        return session.scalars(query.order_by(TopicAnalysisJob.created_at)).all()


def finish(database, identifier):
    with database.begin() as session:
        job = session.get(TopicAnalysisJob, identifier)
        job.result = {"relationships": [], "proposal_ids": []}
        finish_job(job, "no_additions", utcnow())


def test_first_topic_and_later_arrivals_get_coverage_without_manual_requests(database, monkeypatch):
    first = add_topic(database, "React", number=100)
    assert schedule_automation(database)["relationship_scans_completed"] == 1
    second = add_topic(database, "JavaScript", number=200)
    assert schedule_automation(database)["relationship_jobs_scheduled"] == 1
    original = jobs(database, "queued")[0]
    assert {row[0] for row in original.input_snapshot["catalog"]} == {str(first), str(second)}
    # This arrival is behind the old UUID cursor and absent from the queued job.
    third = add_topic(database, "TypeScript", number=1)
    execute(monkeypatch, original.id, [])
    for _ in range(8):
        schedule_automation(database)
        for job in jobs(database, "queued"):
            execute(monkeypatch, job.id, [])
    schedule_automation(database)
    covered = [frozenset(row[0] for row in job.input_snapshot["catalog"]) for job in jobs(database)]
    assert len(covered) == len(set(covered)) == 3
    assert frozenset((str(first), str(third))) in covered
    assert frozenset((str(second), str(third))) in covered
    assert schedule_automation(database)["relationship_jobs_scheduled"] == 0


def test_bounded_jobs_and_cursors_survive_scheduler_restarts(database):
    for index in range(5):
        add_topic(database, f"Topic-{index}")
    seen = set()
    for _ in range(20):
        schedule_automation(database)
        active = jobs(database, "queued")
        assert len(active) <= 2
        for job in active:
            assert len(job.input_snapshot["catalog"]) == 2
            pair = frozenset(row[0] for row in job.input_snapshot["catalog"])
            assert pair not in seen
            seen.add(pair)
            finish(database, job.id)
    assert len(seen) == 10
    with database() as session:
        assert all(scan.finished_at for scan in session.scalars(select(TopicRelationshipScan)))


def test_topic_changes_are_atomic_and_resume_after_disabled_period(database, monkeypatch):
    first = add_topic(database, "React")
    second = add_topic(database, "JavaScript")
    schedule_automation(database)
    finish(database, jobs(database, "queued")[0].id)
    schedule_automation(database)
    with database() as session:
        generation = session.get(TopicRelationshipScan, first).generation
    with database.begin() as session:
        session.get(Topic, first).logo_url = "https://react.dev/logo.png"
    with database() as session:
        assert session.get(TopicRelationshipScan, first).generation == generation
    with database() as session:
        session.get(Topic, first).description = "Rolled back"
        session.flush()
        session.rollback()
    with database() as session:
        assert session.get(TopicRelationshipScan, first).generation == generation
    monkeypatch.setenv("DEVFEED_AUTO_RESEARCH_RELATIONSHIPS", "false")
    get_settings.cache_clear()
    with database.begin() as session:
        session.get(Topic, first).description = "A library with updated relationship evidence."
    assert schedule_automation(database)["relationship_jobs_scheduled"] == 0
    monkeypatch.setenv("DEVFEED_AUTO_RESEARCH_RELATIONSHIPS", "true")
    get_settings.cache_clear()
    assert schedule_automation(database)["relationship_jobs_scheduled"] == 1
    replacement = jobs(database, "queued")[0]
    assert replacement.topic_id == first
    assert str(second) in replacement.input_snapshot["snapshots"]


def test_backfill_is_bounded_and_does_not_need_metadata_research(database, monkeypatch):
    for index in range(3):
        add_topic(database, f"Existing-{index}")
    with database.begin() as session:
        session.execute(delete(TopicRelationshipScan))
    monkeypatch.setenv("DEVFEED_AUTOMATION_BATCH_SIZE", "1")
    get_settings.cache_clear()
    schedule_automation(database)
    with database() as session:
        assert session.scalar(select(func.count()).select_from(TopicRelationshipScan)) == 1
    for _ in range(12):
        schedule_automation(database)
        for job in jobs(database, "queued"):
            finish(database, job.id)
    assert len(jobs(database)) == 3


def test_failed_batch_retries_with_backoff_without_advancing(database, monkeypatch):
    add_topic(database, "React")
    owner = add_topic(database, "JavaScript")
    schedule_automation(database)
    for failures in (1, 2):
        job = jobs(database, "queued")[0]
        with database.begin() as session:
            value = session.get(TopicAnalysisJob, job.id)
            value.attempts = 3
            fail_analysis(value, "codex_timeout")
        before = utcnow()
        schedule_automation(database)
        with database() as session:
            scan = session.get(TopicRelationshipScan, owner)
            assert scan.after_topic_id is None and scan.failures == failures
            assert scan.next_run_at >= before + timedelta(hours=2 ** (failures - 1))
        assert not jobs(database, "queued")
        assert schedule_automation(database)["relationship_jobs_scheduled"] == 0
        with database.begin() as session:
            session.get(TopicRelationshipScan, owner).next_run_at = utcnow() - timedelta(seconds=1)
        assert schedule_automation(database)["relationship_jobs_scheduled"] == 1
    retried = jobs(database, "queued")[0]
    finish(database, retried.id)
    schedule_automation(database)
    with database() as session:
        scan = session.get(TopicRelationshipScan, owner)
        assert scan.finished_at and scan.failures == 0 and scan.last_error is None


def test_concurrent_ticks_coalesce_work_and_manual_jobs_are_respected(database):
    add_topic(database, "React")
    second = add_topic(database, "JavaScript")
    with database.begin() as session:
        manual = request_relationship_analysis(
            session, second, RelationshipAnalysisRequest(), {"subject": "operator"}
        ).id
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: schedule_automation(database), range(2)))
    assert sum(item["relationship_jobs_scheduled"] for item in results) == 0
    assert len(jobs(database)) == 1
    finish(database, manual)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: schedule_automation(database), range(2)))
    assert sum(item["relationship_jobs_scheduled"] for item in results) == 1


def test_changed_focal_job_is_superseded_before_spending_inference(database, monkeypatch):
    add_topic(database, "React")
    owner = add_topic(database, "JavaScript")
    schedule_automation(database)
    old = jobs(database, "queued")[0]
    with database.begin() as session:
        session.get(Topic, owner).aliases = ["ECMAScript"]
    assert schedule_automation(database)["relationship_jobs_scheduled"] == 0
    assert execute(monkeypatch, old.id, []) == []
    assert schedule_automation(database)["relationship_jobs_scheduled"] == 1
    new = jobs(database, "queued")[0]
    assert new.input_snapshot["topic"]["aliases"] == ["ECMAScript"]


def test_changed_peer_during_inference_gets_its_own_new_scan(database, monkeypatch):
    first = add_topic(database, "React")
    second = add_topic(database, "JavaScript")
    schedule_automation(database)
    old = jobs(database, "queued")[0]

    def change():
        with database.begin() as session:
            session.get(Topic, first).description = "Updated during inference"

    execute(monkeypatch, old.id, [], during=change)
    assert schedule_automation(database)["relationship_jobs_scheduled"] == 1
    new = jobs(database, "queued")[0]
    assert new.topic_id == first
    assert str(second) in new.input_snapshot["snapshots"]


def test_inactive_peers_are_skipped_and_reactivation_restarts_coverage(database, monkeypatch):
    first = add_topic(database, "React")
    add_topic(database, "JavaScript")
    add_topic(database, "Unapproved", status="proposed")
    schedule_automation(database)
    old = jobs(database, "queued")[0]
    with database.begin() as session:
        session.get(Topic, first).status = "proposed"
    assert execute(monkeypatch, old.id, []) == []
    assert schedule_automation(database)["relationship_jobs_scheduled"] == 0
    with database.begin() as session:
        session.get(Topic, first).status = "active"
    assert schedule_automation(database)["relationship_jobs_scheduled"] == 1
    assert jobs(database, "queued")[0].topic_id == first


def test_coverage_batches_support_catalogs_above_manual_limit(database):
    now = utcnow()
    topics = [
        Topic(
            id=uuid.uuid4(),
            name=f"Topic-{i}",
            slug=f"topic-{i}",
            kind="technology",
            status="active",
        )
        for i in range(5002)
    ]
    with database.begin() as session:
        session.execute(
            insert(Topic),
            [
                {"id": t.id, "name": t.name, "slug": t.slug, "kind": t.kind, "status": t.status}
                for t in topics
            ],
        )
        session.execute(
            insert(TopicRelationshipScan),
            [
                {
                    "topic_id": t.id,
                    "topic_snapshot": topic_snapshot(t),
                    "next_run_at": now,
                    "finished_at": now,
                }
                for t in topics[:-1]
            ],
        )
        relationship_coverage.reset_scan(session, session.get(Topic, topics[-1].id))
    assert schedule_automation(database)["relationship_jobs_scheduled"] == 1
    assert len(jobs(database, "queued")[0].input_snapshot["catalog"]) == 2


def test_full_response_drains_batch_before_advancing(database, monkeypatch):
    peers = [add_topic(database, f"Peer-{i}") for i in range(4)]
    owner = add_topic(database, "Focal")
    with database.begin() as session:
        for peer in peers:
            session.get(TopicRelationshipScan, peer).finished_at = utcnow()
    monkeypatch.setenv("DEVFEED_RELATIONSHIP_RESEARCH_BATCH_SIZE", "4")
    get_settings.cache_clear()
    schedule_automation(database)
    first = jobs(database, "queued")[0]
    suggestions = [
        {
            "topic_id": str(owner),
            "related_topic_id": str(peer),
            "relation": kind,
            "explanation": "A documented connection",
            "evidence_url": "https://example.com/docs",
            "evidence_title": "Project documentation",
            "evidence_quote": "A documented connection",
        }
        for peer in peers
        for kind in ("uses_language", "depends_on", "implements", "part_of", "related_to")
    ]
    execute(monkeypatch, first.id, suggestions)
    assert schedule_automation(database)["relationship_jobs_scheduled"] == 1
    second = jobs(database, "queued")[0]
    assert len(second.input_snapshot["excluded_edges"]) == 20
    assert second.input_snapshot["catalog"] == first.input_snapshot["catalog"]
    execute(monkeypatch, second.id, [])
    assert schedule_automation(database)["relationship_scans_completed"] == 1


def test_automatic_coverage_can_apply_verified_relationships(database, monkeypatch):
    first = add_topic(database, "React")
    second = add_topic(database, "JavaScript")
    monkeypatch.setenv("DEVFEED_AUTO_APPROVE_TOPIC_RELATIONSHIPS", "true")
    get_settings.cache_clear()
    schedule_automation(database)
    job = jobs(database, "queued")[0]
    execute(
        monkeypatch,
        job.id,
        [
            {
                "topic_id": str(first),
                "related_topic_id": str(second),
                "relation": "uses_language",
                "explanation": "React uses JavaScript",
                "evidence_url": "https://react.dev",
                "evidence_title": "React documentation",
                "evidence_quote": "The library for web and native user interfaces",
            }
        ],
    )
    with database() as session:
        from devfeed_core.models import TopicRelation

        assert session.get(TopicRelation, (first, second, "uses_language")) is not None
    assert schedule_automation(database)["relationship_scans_completed"] == 1


def test_metadata_followups_use_the_bounded_scheduler(admin_client, database, pending, monkeypatch):
    prepare_relationship_catalog(database, pending)
    run_metadata_research(admin_client, pending, monkeypatch)
    assert jobs(database) == []
    assert schedule_automation(database)["relationship_jobs_scheduled"] == 1
    assert jobs(database)[0].requested_by == relationship_coverage.ACTOR


def test_prompt_budget_shrinks_batch_without_skipping_unprocessed_peers(database, monkeypatch):
    monkeypatch.setenv("DEVFEED_RELATIONSHIP_RESEARCH_BATCH_SIZE", "500")
    get_settings.cache_clear()
    now = utcnow()
    peers = [
        Topic(
            id=uuid.uuid4(),
            name="😀" * 100,
            slug="p" * 97 + f"{i:03}",
            kind="technology",
            status="active",
        )
        for i in range(500)
    ]
    with database.begin() as session:
        session.execute(
            insert(Topic),
            [
                {"id": t.id, "name": t.name, "slug": t.slug, "kind": t.kind, "status": t.status}
                for t in peers
            ],
        )
        session.execute(
            insert(TopicRelationshipScan),
            [
                {
                    "topic_id": t.id,
                    "topic_snapshot": topic_snapshot(t),
                    "next_run_at": now,
                    "finished_at": now,
                }
                for t in peers
            ],
        )
    add_topic(database, "Focal")
    assert schedule_automation(database)["relationship_jobs_scheduled"] == 1
    job = jobs(database)[0]
    peer_count = len(job.input_snapshot["catalog"]) - 1
    assert 0 < peer_count < 500
    assert job.input_snapshot["coverage"]["through_topic_id"] == str(
        sorted(peer.id for peer in peers)[peer_count - 1]
    )


def test_scheduler_routes_relationships_independently_of_metadata_backlog(
    database, pending, monkeypatch
):
    monkeypatch.setenv("DEVFEED_SCHEDULER_BATCH_SIZE", "1")
    get_settings.cache_clear()
    add_topic(database, "React")
    add_topic(database, "JavaScript")
    with database.begin() as session:
        metadata = request_topic_analysis(session, uuid.UUID(pending), {"subject": "operator"})
        metadata.available_at = utcnow() - timedelta(days=1)
        metadata_id = metadata.id
    counts = scheduler.tick()
    assert counts["topic_analyses_dispatched"] == 2
    analysis, relationships = get_queue("analysis"), get_queue("relationships")
    try:
        assert analysis.count == relationships.count == 1
        assert analysis.fetch_job(analysis.job_ids[0]).args == [str(metadata_id)]
        relationship = relationships.fetch_job(relationships.job_ids[0])
        assert relationship.args == [str(jobs(database, "queued")[0].id)]
        assert scheduler.tick()["topic_analyses_dispatched"] == 0
    finally:
        analysis.connection.close()
        relationships.connection.close()
