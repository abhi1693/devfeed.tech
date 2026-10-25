"""Report contribution accounting and bounded serving contracts without a database."""

from collections import defaultdict
from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest
from devfeed_core import reporting_facts as facts
from devfeed_core.models import ReportingArticleFact, ReportingJobFact
from sqlalchemy.dialects import postgresql

NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)
ORIGINAL_BUDGET = facts.batch_budget
IDENTITY = UUID(int=1)
SOURCE = UUID(int=2)
TOPIC = UUID(int=3)


@pytest.fixture(autouse=True)
def isolate_connection_deadlines(monkeypatch):
    monkeypatch.setattr(facts, "batch_budget", lambda _: nullcontext())


def deltas():
    return defaultdict(lambda: defaultdict(Decimal))


def sql(statement):
    return str(statement.compile(dialect=postgresql.dialect()))


def test_job_totals_count_zero_tokens_as_reported_and_keep_unreported_duration():
    output = deltas()
    for kind, tokens, duration in [
        ("article_analysis", Decimal(0), 3),
        ("article_analysis", None, 7),
        ("topic_analysis", Decimal(11), 5),
    ]:
        facts._job_contribution(
            output,
            SimpleNamespace(kind=kind, finished_at=NOW, tokens=tokens, duration_ms=duration),
            1,
        )
    article = output[(NOW.date(), "job.article_analysis", facts.ZERO_ID)]
    assert dict(article) == {
        "count": 2,
        "tokens": 0,
        "reported": 1,
        "unreported": 1,
        "duration_ms": 10,
    }
    assert output[(NOW.date(), "job.topic_analysis", facts.ZERO_ID)]["tokens"] == 11
    facts._job_contribution(output, None, -1)


def test_source_failures_do_not_create_inference_or_job_token_usage():
    output = deltas()
    facts._job_contribution(
        output, SimpleNamespace(kind="ingestion", finished_at=NOW, source_id=SOURCE), 1
    )
    assert dict(output[(NOW.date(), "source.failed", SOURCE)]) == {"count": 1}
    assert len(output) == 1


@pytest.mark.parametrize("published", [True, False])
def test_unpublishing_preserves_first_publication_but_removes_current_inventory(published):
    output = deltas()
    fact = SimpleNamespace(
        discovered_at=NOW - timedelta(days=1), published_at=NOW, published=published, automatic=True
    )
    facts._article_contribution(output, fact, {SOURCE}, {TOPIC}, 1)
    assert output[(NOW.date(), "article.published", facts.ZERO_ID)]["automatic"] == 1
    assert ((NOW.date(), "source.published", SOURCE) in output) is published
    assert ((date.min, "topic.total", TOPIC) in output) is published
    facts._article_contribution(output, fact, {SOURCE}, {TOPIC}, -1)
    assert all(not any(metrics.values()) for metrics in output.values())
    facts._article_contribution(output, None, set(), set(), 1)


def test_unpublished_articles_have_discovery_but_no_publication_contribution():
    output = deltas()
    facts._article_contribution(
        output,
        SimpleNamespace(discovered_at=NOW, published_at=None, published=False),
        {SOURCE},
        {TOPIC},
        1,
    )
    assert set(output) == {
        (NOW.date(), "article.discovered", facts.ZERO_ID),
        (NOW.date(), "source.discovered", SOURCE),
    }


@pytest.mark.parametrize(
    "kind",
    [
        "job.article_analysis",
        "job.topic_analysis",
        "job.research_verification",
        "source.failed",
        "article.discovered",
        "article.published",
        "source.discovered",
        "source.published",
        "topic.published",
    ],
)
def test_serving_buckets_read_only_compact_facts_and_partition_partial_days(kind):
    start = NOW - timedelta(days=2, hours=1)
    query = facts.bucket_query(kind, start, NOW)
    compiled = query.select().compile(dialect=postgresql.dialect())
    rendered = str(compiled)
    assert "reporting_daily_facts" in rendered
    assert "reporting_job_facts" in rendered or "reporting_article_facts" in rendered
    assert "article_reviews" not in rendered and "article_analysis_jobs" not in rendered
    assert "usage" not in rendered and "articles." not in rendered
    assert "UNION ALL" in rendered
    params = compiled.params
    assert start in params.values() and NOW in params.values()
    assert start.date() + timedelta(days=1) in params.values()
    assert datetime.combine(NOW.date(), datetime.min.time(), tzinfo=UTC) in params.values()


@pytest.mark.parametrize("limit", [0, -1, 201])
def test_invalid_batch_budget_is_rejected_before_opening_any_connection(limit):
    def forbidden():
        raise AssertionError("Invalid work budgets must not open a database")

    with pytest.raises(ValueError, match="1..200"):
        facts.refresh_reporting_facts(forbidden, limit=limit)


@pytest.mark.parametrize(
    "complete,oldest,available",
    [
        (4, None, False),
        (5, None, True),
        (5, NOW - timedelta(seconds=59), True),
        (5, NOW - timedelta(seconds=61), False),
    ],
)
def test_readiness_distinguishes_initial_coverage_from_bounded_pending_changes(
    monkeypatch, complete, oldest, available
):
    responses = iter([complete, oldest])
    session = SimpleNamespace(scalar=lambda query: next(responses))
    monkeypatch.setattr(facts, "utcnow", lambda: NOW)
    if available:
        facts.require_reporting_facts(session)
    else:
        with pytest.raises(facts.ReportingFactsUnavailable):
            facts.require_reporting_facts(session)


def test_future_queue_timestamp_does_not_report_negative_lag():
    session = SimpleNamespace(scalar=lambda _: NOW + timedelta(seconds=1))
    assert facts.reporting_fact_lag(session, now=NOW) == 0


def test_busy_projector_lock_returns_without_touching_contributions():
    statements = []
    session = SimpleNamespace(
        begin=lambda: nullcontext(),
        execute=lambda query: statements.append(sql(query)),
        scalar=lambda _: False,
    )
    assert facts.refresh_reporting_facts(lambda: nullcontext(session)) == 0
    assert len(statements) == 2
    assert "REPEATABLE READ" in statements[0]


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, None),
        ("", None),
        ("0", Decimal(0)),
        ("999999999999999999", Decimal("999999999999999999")),
        ("9999999999999999999", None),
        ("-1", None),
        ("1.5", None),
        ("１２", None),
    ],
)
def test_job_projection_validates_telemetry_once_and_preserves_cumulative_values(raw, expected):
    additions, statements = [], []
    row = {
        "id": IDENTITY,
        "finished_at": NOW,
        "source_id": None,
        "raw_tokens": raw,
        "duration_ms": 17,
    }

    def execute(query):
        statements.append(sql(query))
        return SimpleNamespace(mappings=lambda: [row])

    session = SimpleNamespace(
        scalars=lambda _: [],
        execute=execute,
        flush=lambda: None,
        add_all=lambda rows: additions.extend(rows),
    )
    output = deltas()
    facts._replace_jobs(session, "article_analysis", [IDENTITY], output)
    assert len(additions) == 1 and additions[0].tokens == expected
    assert additions[0].duration_ms == 17
    assert output[(NOW.date(), "job.article_analysis", facts.ZERO_ID)]["reported"] == int(
        expected is not None
    )
    assert "left(" in statements[0]


def test_identical_job_replay_produces_no_fact_or_bucket_writes():
    original = ReportingJobFact(
        kind="article_analysis",
        entity_id=IDENTITY,
        finished_at=NOW,
        tokens=Decimal(5),
        duration_ms=4,
        source_id=None,
    )
    statements, additions = [], []

    def execute(query):
        statements.append(sql(query))
        return SimpleNamespace(
            mappings=lambda: [
                {
                    "id": IDENTITY,
                    "finished_at": NOW,
                    "source_id": None,
                    "raw_tokens": "5",
                    "duration_ms": 4,
                }
            ]
        )

    session = SimpleNamespace(
        scalars=lambda _: [original],
        execute=execute,
        flush=lambda: None,
        add_all=lambda rows: additions.extend(rows),
    )
    output = deltas()
    facts._replace_jobs(session, "article_analysis", [IDENTITY], output)
    assert not additions and not output
    assert len(statements) == 1 and statements[0].startswith("SELECT")


def test_job_delete_reverses_exact_previous_contribution():
    original = ReportingJobFact(
        kind="article_analysis",
        entity_id=IDENTITY,
        finished_at=NOW,
        tokens=Decimal(5),
        duration_ms=4,
        source_id=None,
    )
    session = SimpleNamespace(
        scalars=lambda _: [original],
        execute=lambda _: SimpleNamespace(mappings=lambda: []),
        flush=lambda: None,
        add_all=lambda _: None,
    )
    output = deltas()
    facts._replace_jobs(session, "article_analysis", [IDENTITY], output)
    assert dict(output[(NOW.date(), "job.article_analysis", facts.ZERO_ID)]) == {
        "count": -1,
        "tokens": -5,
        "reported": -1,
        "unreported": 0,
        "duration_ms": -4,
    }


def test_bucket_deltas_skip_noops_and_batch_many_dimensions():
    output = deltas()
    for index in range(401):
        output[(NOW.date(), "source.published", UUID(int=index + 1))]["count"] = Decimal(1)
    output[(NOW.date(), "article.discovered", facts.ZERO_ID)]["count"] = Decimal(0)
    statements = []
    facts._apply_deltas(
        SimpleNamespace(execute=lambda query: statements.append(sql(query))), output
    )
    assert len(statements) == 6
    assert all("ON CONFLICT" in statement for statement in statements[::2])
    assert all("DELETE" in statement for statement in statements[1::2])


def article_session(*, current=True, original=True, changed_sources=False, published=True):
    fact = ReportingArticleFact(
        article_id=IDENTITY,
        discovered_at=NOW - timedelta(seconds=10),
        published_at=NOW if published else None,
        published=published,
        automatic=published,
        latency_seconds=Decimal(10) if published else None,
        content_type="article",
    )
    statements, additions = [], []

    def execute(query):
        statement = sql(query)
        statements.append(statement)
        if statement.startswith("SELECT"):
            if "FROM reporting_article_sources" in statement:
                return [(IDENTITY, SOURCE)] if original else []
            if "FROM reporting_article_topics" in statement:
                return [(IDENTITY, TOPIC)] if original else []
            if "FROM article_origins" in statement:
                return [(IDENTITY, UUID(int=4) if changed_sources else SOURCE)] if current else []
            if "FROM article_topics" in statement:
                return [(IDENTITY, TOPIC)] if current else []
            return SimpleNamespace(
                mappings=lambda: (
                    [
                        {
                            "id": IDENTITY,
                            "discovered_at": fact.discovered_at,
                            "published_to_feed_at": fact.published_at,
                            "publication_status": "published" if published else "unpublished",
                            "automatic": published,
                            "content_type": "article",
                        }
                    ]
                    if current
                    else []
                )
            )
        return None

    session = SimpleNamespace(
        scalars=lambda _: [fact] if original else [],
        execute=execute,
        flush=lambda: None,
        add_all=lambda rows: additions.extend(rows),
    )
    return session, statements, additions


def test_identical_article_replay_and_duplicate_origin_changes_do_not_rewrite_facts():
    session, statements, additions = article_session()
    output = deltas()
    facts._replace_articles(session, [IDENTITY], output)
    assert not additions and not output
    assert all(statement.startswith("SELECT") for statement in statements)


@pytest.mark.parametrize("published", [True, False])
def test_first_article_projection_preserves_latency_and_unpublished_discovery(published):
    session, _, additions = article_session(original=False, published=published)
    output = deltas()
    facts._replace_articles(session, [IDENTITY], output)
    fact = next(item for item in additions if isinstance(item, ReportingArticleFact))
    assert fact.latency_seconds == (10 if published else None)
    assert output[(NOW.date(), "article.discovered", facts.ZERO_ID)]["count"] == 1
    assert ((NOW.date(), "article.published", facts.ZERO_ID) in output) is published


def test_removed_article_reverses_bucket_memberships_and_deletes_contributions():
    session, statements, additions = article_session(current=False)
    output = deltas()
    facts._replace_articles(session, [IDENTITY], output)
    assert not additions
    assert output[(NOW.date(), "article.published", facts.ZERO_ID)]["count"] == -1
    assert output[(date.min, "topic.total", TOPIC)]["count"] == -1
    assert sum(statement.startswith("DELETE") for statement in statements) == 3


def test_source_membership_delta_preserves_unchanged_article_and_topic_facts():
    session, statements, additions = article_session(changed_sources=True)
    output = deltas()
    facts._replace_articles(session, [IDENTITY], output)
    assert output[(NOW.date(), "source.published", SOURCE)]["count"] == -1
    assert output[(NOW.date(), "source.published", UUID(int=4))]["count"] == 1
    assert all(not isinstance(item, ReportingArticleFact) for item in additions)
    writes = [statement for statement in statements if not statement.startswith("SELECT")]
    assert len(writes) == 1 and "DELETE FROM reporting_article_sources" in writes[0]
    assert len(additions) == 1


def test_bootstrap_acknowledges_only_observed_dirty_generation(monkeypatch):
    statements, projected = [], []
    state = SimpleNamespace(kind="article", cursor_id=None, complete=False, updated_at=NOW)
    dirty = SimpleNamespace(kind="article", entity_id=IDENTITY, generation=7)
    scalar_results = iter([True, state])
    pages = iter([[IDENTITY], [dirty]])
    session = SimpleNamespace(
        begin=lambda: nullcontext(),
        scalar=lambda _: next(scalar_results),
        scalars=lambda _: next(pages),
        execute=lambda query: statements.append(query),
    )
    monkeypatch.setattr(
        facts,
        "_replace_articles",
        lambda session, identifiers, deltas: projected.extend(identifiers),
    )
    monkeypatch.setattr(facts, "_apply_deltas", lambda *_: None)
    assert facts.refresh_reporting_facts(lambda: nullcontext(session), limit=2) == 1
    assert projected == [IDENTITY] and state.complete and state.cursor_id == IDENTITY
    acknowledgement = next(query for query in statements if sql(query).startswith("DELETE"))
    assert "reporting_dirty.generation =" in sql(acknowledgement)
    assert 7 in acknowledgement.compile().params.values()
    assert any(sql(query).startswith("UPDATE reporting_fact_states") for query in statements)


def test_observation_exposes_generation_and_pending_fact_lag():
    responses = iter([NOW, NOW - timedelta(seconds=3), 12])
    session = SimpleNamespace(scalar=lambda _: next(responses))
    assert facts.reporting_fact_observation(session)["generation"] == 12


def test_ingestion_projection_records_only_source_failure_contribution():
    additions = []
    session = SimpleNamespace(
        scalars=lambda _: [],
        execute=lambda _: SimpleNamespace(
            mappings=lambda: [
                {
                    "id": IDENTITY,
                    "finished_at": NOW,
                    "source_id": SOURCE,
                    "raw_tokens": None,
                    "duration_ms": 0,
                }
            ]
        ),
        flush=lambda: None,
        add_all=lambda rows: additions.extend(rows),
    )
    output = deltas()
    facts._replace_jobs(session, "ingestion", [IDENTITY], output)
    assert dict(output[(NOW.date(), "source.failed", SOURCE)]) == {"count": 1}
    assert len(additions) == 1 and additions[0].source_id == SOURCE


@pytest.mark.parametrize("phase", ["bootstrap-empty", "bootstrap-job", "dirty-job", "idle"])
def test_projector_resumes_keyset_and_dirty_work_but_idle_does_not_rewrite_state(
    monkeypatch, phase
):
    statements, projected, requests = [], [], []
    job_id = UUID(int=10)
    state = (
        SimpleNamespace(
            kind="article_analysis", cursor_id=UUID(int=9), complete=False, updated_at=NOW
        )
        if phase.startswith("bootstrap")
        else None
    )
    dirty = SimpleNamespace(kind="article_analysis", entity_id=job_id, generation=1)
    scalar_results = iter([True, state])
    pages = iter(
        [[job_id], []]
        if phase == "bootstrap-job"
        else [[], []]
        if phase == "bootstrap-empty"
        else [[dirty]]
        if phase == "dirty-job"
        else [[]]
    )

    def scalars(query):
        requests.append(sql(query))
        return next(pages)

    session = SimpleNamespace(
        begin=lambda: nullcontext(),
        scalar=lambda _: next(scalar_results),
        scalars=scalars,
        execute=lambda query: statements.append(sql(query)),
    )
    monkeypatch.setattr(
        facts, "_replace_jobs", lambda session, kind, ids, changes: projected.extend(ids)
    )
    monkeypatch.setattr(facts, "_apply_deltas", lambda *_: None)
    result = facts.refresh_reporting_facts(lambda: nullcontext(session), limit=2)
    assert result == int(phase in {"bootstrap-job", "dirty-job"})
    assert projected == ([job_id] if result else [])
    writes_state = any(
        statement.startswith("UPDATE reporting_fact_states") for statement in statements
    )
    assert writes_state is (phase != "idle")
    if phase.startswith("bootstrap"):
        assert "article_analysis_jobs.id >" in requests[0]
        assert state.complete


def test_latency_queries_exact_event_samples_instead_of_averaging_daily_medians():
    responses, statements = iter([5, None]), []

    def execute(query):
        statements.append(sql(query))
        return SimpleNamespace(one=lambda: (5, 4, Decimal("8.2")))

    session = SimpleNamespace(scalar=lambda _: next(responses), execute=execute)
    assert facts.publication_latency(
        session, NOW - timedelta(days=1), NOW - timedelta(days=2), NOW
    ) == (5, 4, Decimal("8.2"))
    assert statements[0].count("WITHIN GROUP") == 3
    assert "reporting_article_facts" in statements[0]
    assert "reporting_daily_facts" not in statements[0]


@pytest.mark.parametrize("expired", [False, True])
def test_whole_batch_deadline_limits_last_statement_and_removes_hook(monkeypatch, expired):
    callbacks, removed, commands = [], [], []
    ticks = iter([100.0, 131.0 if expired else 125.0])
    monkeypatch.setattr(facts.monotonic_time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(
        facts.event, "listen", lambda connection, name, callback: callbacks.append(callback)
    )
    monkeypatch.setattr(
        facts.event, "remove", lambda connection, name, callback: removed.append(callback)
    )
    session = SimpleNamespace(connection=lambda: object())
    cursor = SimpleNamespace(execute=lambda command: commands.append(command))
    with ORIGINAL_BUDGET(session):
        if expired:
            with pytest.raises(facts.DatabaseTimeout, match="batch exceeded"):
                callbacks[0](None, cursor, None, None, None, None)
        else:
            callbacks[0](None, cursor, None, None, None, None)
    assert removed == callbacks
    assert commands == ([] if expired else ["SET LOCAL statement_timeout = '5000ms'"])


def test_legacy_published_inventory_without_event_timestamp_keeps_all_time_topic_total():
    output = deltas()
    facts._article_contribution(
        output,
        SimpleNamespace(
            discovered_at=NOW,
            published_at=None,
            published=True,
        ),
        {SOURCE},
        {TOPIC},
        1,
    )
    assert output[(date.min, "topic.total", TOPIC)]["count"] == 1
    assert all(
        kind not in {"article.published", "source.published", "topic.published"}
        for _, kind, _ in output
    )
