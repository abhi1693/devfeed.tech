"""Real origin deduplication, owned commits and schedule reconciliation."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from devfeed_aggregator import tasks
from devfeed_cli.main import run
from devfeed_core import jobs
from devfeed_core.config import get_settings
from devfeed_core.feeds.fetcher import FeedError, FetchResult
from devfeed_core.models import Article, ArticleOrigin, IngestionJob, Source
from devfeed_core.polling_reconciliation import reconcile_polling
from sqlalchemy import func, select
from test_adaptive_polling import NOW

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def adaptive_clock(monkeypatch):
    monkeypatch.setattr(get_settings(), "adaptive_source_polling_enabled", True)
    monkeypatch.setattr(tasks, "utcnow", lambda: NOW)
    monkeypatch.setattr(jobs, "utcnow", lambda: NOW)


def seed(database, **values):
    with database.begin() as session:
        source = Source(
            name="Adaptive",
            feed_url=f"https://example.com/{uuid.uuid4()}",
            source_type="publisher",
            approval_status="approved",
            enabled=True,
            polling_mode="adaptive",
            poll_interval_seconds=43200,
            polling_state={
                "observed_at": (NOW - timedelta(hours=1)).isoformat(),
                "interval": 3600,
                "rate": 1 / 3600,
            },
            **values,
        )
        session.add(source)
        session.flush()
        return source.id


def enqueue(database, source_id, automatic=True):
    with database.begin() as session:
        source = session.scalar(select(Source).where(Source.id == source_id).with_for_update())
        return jobs.request_ingestion(session, source, automatic=automatic).id


def test_cross_source_duplicates_count_once_and_survive_restart(database, rss_bytes, monkeypatch):
    monkeypatch.setattr(tasks, "fetch_feed", lambda *a: FetchResult(200, rss_bytes, a[0]))
    first, second = seed(database), seed(database)
    for source_id in [first, second]:
        identifier = enqueue(database, source_id)
        tasks.ingest(str(identifier))
        tasks.ingest(str(identifier))
    with database() as session:
        assert session.scalar(select(func.count()).select_from(Article)) == 2
        assert session.scalar(select(func.count()).select_from(ArticleOrigin)) == 4
        records = session.scalars(select(IngestionJob)).all()
        assert sorted(job.new_source_entries for job in records) == [2, 2]
        assert sum(job.articles_created for job in records) == 2
        state = session.get(Source, first).polling_state
        assert state["observed_at"] == NOW.isoformat()
        # These fixture entries are historical: count origins, not current velocity.
        assert state["reason"] == "quiet"
        assert session.get(Source, first).poll_interval_seconds == 43200
    identifier = enqueue(database, first)
    tasks.ingest(str(identifier))
    with database() as session:
        assert session.get(IngestionJob, identifier).new_source_entries == 0
        assert session.get(Source, first).polling_state == state  # same clock, no double training


@pytest.mark.parametrize(
    "status,body",
    [(304, b""), (200, b'<rss version="2.0"><channel><title>Empty</title></channel></rss>')],
)
def test_successful_empty_and_unchanged_train_quiet(database, monkeypatch, status, body):
    monkeypatch.setattr(tasks, "fetch_feed", lambda *a: FetchResult(status, body, a[0]))
    source_id = seed(database)
    identifier = enqueue(database, source_id)
    tasks.ingest(str(identifier))
    with database() as session:
        source = session.get(Source, source_id)
        assert session.get(IngestionJob, identifier).status == "succeeded"
        assert source.polling_state["quiet_seconds"] == 3600
        assert source.polling_state["interval"] > 3600
        assert source.next_fetch_at <= NOW + timedelta(days=1)


@pytest.mark.parametrize("status", [429, 503])
def test_retry_after_does_not_train_or_allow_manual_bypass(database, monkeypatch, status):
    source_id = seed(database)
    identifier = enqueue(database, source_id)
    with database() as session:
        before = session.get(Source, source_id).polling_state

    def fail(*args):
        raise FeedError("Cooldown", status=status, retry_after=86400, retryable=True)

    monkeypatch.setattr(tasks, "fetch_feed", fail)
    tasks.ingest(str(identifier))
    assert enqueue(database, source_id, automatic=False) == identifier
    with database() as session:
        assert session.get(Source, source_id).polling_state == before
        job = session.get(IngestionJob, identifier)
        assert job.available_at >= NOW + timedelta(days=1)


def test_lost_lease_rolls_back_evidence_and_learning(database, rss_bytes, monkeypatch):
    source_id = seed(database)
    identifier = enqueue(database, source_id)

    def fetch(*args):
        with database.begin() as session:
            job = session.get(IngestionJob, identifier)
            job.lease_token = uuid.uuid4()
        return FetchResult(200, rss_bytes, args[0])

    monkeypatch.setattr(tasks, "fetch_feed", fetch)
    tasks.ingest(str(identifier))
    with database() as session:
        assert session.scalar(select(func.count()).select_from(ArticleOrigin)) == 0
        assert session.get(Source, source_id).polling_state["observed_at"] != NOW.isoformat()
        assert session.get(IngestionJob, identifier).status == "running"


def test_concurrent_reconciliation_is_bounded_and_preserves_jobs_and_cooldowns(
    database, monkeypatch
):
    ids = [
        seed(
            database,
            last_success_at=NOW - timedelta(hours=13),
            next_fetch_at=NOW + timedelta(days=1),
            scheduled_polling_mode="adaptive",
        )
        for _ in range(7)
    ]
    active_id = enqueue(database, ids[-1])
    with database.begin() as session:
        session.get(Source, ids[-2]).consecutive_failures = 1
    monkeypatch.setattr(get_settings(), "adaptive_source_polling_enabled", False)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reconcile_polling(database, 2, NOW), range(2)))
    assert sum(results) == 4
    assert reconcile_polling(database, 2, NOW) == 1
    assert reconcile_polling(database, 2, NOW) == 0
    with database() as session:
        for source_id in ids[:-2]:
            source = session.get(Source, source_id)
            assert source.scheduled_polling_mode == "fixed"
            assert NOW <= source.next_fetch_at < NOW + timedelta(minutes=1)
        assert session.get(IngestionJob, active_id).status == "queued"
        assert session.get(Source, ids[-2]).next_fetch_at == NOW + timedelta(days=1)
        assert session.scalar(select(func.count()).select_from(IngestionJob)) == 1


@pytest.mark.parametrize("channel", ["admin", "cli"])
def test_operator_mode_and_effective_gate(database, admin_client, capsys, monkeypatch, channel):
    source_id = seed(database, last_success_at=NOW - timedelta(hours=1))
    monkeypatch.setattr(get_settings(), "adaptive_source_polling_enabled", False)
    if channel == "admin":
        response = admin_client.patch(
            f"/v1/admin/sources/{source_id}", json={"polling_mode": "fixed"}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["effective_interval_seconds"] == body["poll_interval_seconds"] == 43200
        assert body["adaptive_polling_enabled"] is False
        assert body["effective_polling_mode"] == "fixed"
        assert (
            admin_client.patch(
                f"/v1/admin/sources/{source_id}", json={"polling_mode": "invalid"}
            ).status_code
            == 422
        )
    else:
        assert run(["sources", "update", str(source_id), "--polling-mode", "fixed"]) == 0
        capsys.readouterr()
        with pytest.raises(SystemExit):
            run(["sources", "update", str(source_id), "--polling-mode", "invalid"])
        capsys.readouterr()
    with database() as session:
        source = session.get(Source, source_id)
        assert source.polling_mode == "fixed" and source.poll_interval_seconds == 43200
        assert source.next_fetch_at == NOW + timedelta(hours=11)


def test_migration_preserves_configured_interval_and_job_data(database):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    identifier = seed(database)
    job_id = enqueue(database, identifier)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    try:
        command.downgrade(config, "0022")
        with database() as session:
            assert (
                session.scalar(
                    text("SELECT poll_interval_seconds FROM sources WHERE id=:id"),
                    {"id": identifier},
                )
                == 43200
            )
            assert (
                session.scalar(
                    text("SELECT status FROM ingestion_jobs WHERE id=:id"), {"id": job_id}
                )
                == "queued"
            )
    finally:
        command.upgrade(config, "head")
    with database() as session:
        source = session.get(Source, identifier)
        assert source.polling_mode == "fixed" and source.polling_state == {}
        assert source.poll_interval_seconds == source.effective_interval_seconds == 43200
        assert session.get(IngestionJob, job_id).new_source_entries == 0


def test_learning_and_origins_roll_back_if_scheduling_fails(database, rss_bytes, monkeypatch):
    source_id = seed(database)
    identifier = enqueue(database, source_id)
    monkeypatch.setattr(tasks, "fetch_feed", lambda *a: FetchResult(200, rss_bytes, a[0]))

    def interrupted(*args):
        raise RuntimeError("interrupted after learning")

    monkeypatch.setattr(tasks, "schedule", interrupted)
    tasks.ingest(str(identifier))
    with database() as session:
        assert session.scalar(select(func.count()).select_from(ArticleOrigin)) == 0
        assert session.get(Source, source_id).polling_state["observed_at"] != NOW.isoformat()
        assert session.get(IngestionJob, identifier).status == "queued"


@pytest.mark.parametrize(
    "date", ["", "not a date", "Sun, 11 Oct 2026 01:00:00 GMT", "Sat, 10 Oct 2026 00:00:00 GMT"]
)
def test_new_entries_with_missing_invalid_future_or_fresh_dates_accelerate(
    database, monkeypatch, date
):
    body = f"""<rss version="2.0"><channel><title>Activity</title><item>
    <title>Fresh engineering entry</title><link>https://example.com/article</link>
    <guid>new-identity</guid><pubDate>{date}</pubDate></item></channel></rss>""".encode()
    monkeypatch.setattr(tasks, "fetch_feed", lambda *a: FetchResult(200, body, a[0]))
    source_id = seed(database)
    with database.begin() as session:
        source = session.get(Source, source_id)
        source.polling_state = {
            "observed_at": (NOW - timedelta(hours=1)).isoformat(),
            "interval": 43200,
            "rate": 1 / 3600,
        }
    identifier = enqueue(database, source_id)
    tasks.ingest(str(identifier))
    with database() as session:
        source = session.get(Source, source_id)
        assert session.get(IngestionJob, identifier).new_source_entries == 1
        assert source.polling_state["reason"] == "new_entries"
        assert source.polling_state["interval"] == 21600


def test_terminal_failure_cooldown_survives_new_manual_job(database):
    source_id = seed(database, consecutive_failures=1, next_fetch_at=NOW + timedelta(days=1))
    identifier = enqueue(database, source_id, automatic=False)
    with database() as session:
        assert session.get(IngestionJob, identifier).available_at == NOW + timedelta(days=1)
        assert session.get(Source, source_id).next_fetch_at >= NOW + timedelta(days=1)


def test_real_scheduler_reconciles_without_starving_due_fixed_source(database, monkeypatch):
    from devfeed_aggregator import scheduler

    overdue = seed(database, next_fetch_at=NOW - timedelta(hours=1))
    with database.begin() as session:
        session.get(Source, overdue).polling_mode = "fixed"
    slow = [
        seed(
            database,
            last_success_at=NOW - timedelta(hours=13),
            next_fetch_at=NOW + timedelta(days=1),
            scheduled_polling_mode="adaptive",
        )
        for _ in range(3)
    ]
    monkeypatch.setattr(get_settings(), "adaptive_source_polling_enabled", False)
    monkeypatch.setattr(get_settings(), "scheduler_batch_size", 1)
    monkeypatch.setattr(scheduler, "utcnow", lambda: NOW)
    assert scheduler.tick()["scheduled"] == 1
    with database() as session:
        job = session.scalar(select(IngestionJob).where(IngestionJob.source_id == overdue))
        assert job is not None and job.automatic is True
        assert (
            sum(
                session.get(Source, source_id).scheduled_polling_mode == "fixed"
                for source_id in slow
            )
            == 1
        )


def test_long_queue_window_uses_elapsed_time_without_burst(database, monkeypatch):
    from devfeed_core.polling import observe

    identifier = seed(database)
    with database.begin() as session:
        source = session.get(Source, identifier)
        observe(source, NOW + timedelta(days=8), 10000, automatic=True)
    with database() as session:
        state = session.get(Source, identifier).polling_state
        assert state["reason"] == "stale_probe" and state["interval"] == 3600
        assert state["rate"] == 0


def test_feed_replacement_discards_in_flight_response_without_training(
    database, rss_bytes, monkeypatch
):
    source_id = seed(database)
    identifier = enqueue(database, source_id)

    def replaced(*args):
        with database.begin() as session:
            source = session.get(Source, source_id)
            source.feed_url = "https://replacement.example/feed"
            source.polling_state = {}
        return FetchResult(200, rss_bytes, args[0])

    monkeypatch.setattr(tasks, "fetch_feed", replaced)
    tasks.ingest(str(identifier))
    with database() as session:
        source = session.get(Source, source_id)
        assert source.polling_state == {} and source.consecutive_failures == 0
        assert session.get(IngestionJob, identifier).status == "failed"
        assert session.scalar(select(func.count()).select_from(ArticleOrigin)) == 0
