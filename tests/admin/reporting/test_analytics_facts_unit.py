"""Typed ingestion contracts preserve useful history without copying entity graphs."""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from devfeed_core.analytics_facts import dirty_buckets, empty_fact, initialization_sql
from devfeed_core.analytics_schema import TABLES
from devfeed_core.analytics_transform import (
    JOB_KINDS,
    moment,
    normalize_event,
    number,
    transform_events,
)

NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)
IDENTITY = str(uuid.uuid4())
PARENT = str(uuid.uuid4())


def event(source, record, *, before=None, operation="I", identity=7):
    return dict(
        id=identity,
        source_table=source,
        record=record,
        before_record=before,
        operation=operation,
        created_at=NOW,
    )


@pytest.mark.parametrize(
    "raw,expected",
    [
        (0, 0),
        (42, 42),
        ("00042", 42),
        ("999999999999", 999999999999),
        (True, 0),
        (False, 0),
        (None, 0),
        (-1, 0),
        (1.5, 0),
        (" 1", 0),
        ("-1", 0),
        ("1.0", 0),
        ("1000000000000", 0),
        ("١٢", 0),
        ({}, 0),
        ([], 0),
    ],
)
def test_token_grammar_distinguishes_unknown_values_without_losing_large_exact_counts(
    raw, expected
):
    assert number(raw) == expected
    assert number("9" * 18, digits=18) == 999999999999999999


def test_invocation_normalizes_tokens_once_and_keeps_subset_counters_separate():
    record = dict(
        id=IDENTITY,
        job_id=PARENT,
        started_at=NOW.isoformat(),
        finished_at=NOW,
        operation="source_relevance",
        model="model",
        reasoning_effort="low",
        status="failed",
        tokens=json.dumps(
            dict(inputTokens=100, cachedInputTokens=200, outputTokens=20, reasoningOutputTokens=50)
        ),
        web_searches=-1,
        duration_ms=-1,
    )
    row = normalize_event(event("inference_calls", record), 3)[0]
    assert row["input_tokens"] + row["output_tokens"] == 120
    assert row["cached_input_tokens"] == 100 and row["reasoning_tokens"] == 20
    assert row["failed"] == row["calls"] == 1
    assert row["has_duration"] == row["web_searches"] == 0
    assert row["entity_id"] == IDENTITY and row["parent_id"] == PARENT
    assert row["event_time"] == row["ended_at"] == NOW
    unreported = normalize_event(
        event("inference_calls", {**record, "tokens": {}, "duration_ms": 0}), 4
    )[0]
    assert unreported["unreported"] == unreported["has_duration"] == 1


@pytest.mark.parametrize("source", list(JOB_KINDS))
def test_completion_is_per_attempt_and_later_retries_do_not_erase_failures(source):
    before = dict(
        id=IDENTITY,
        status="failed",
        attempts=1,
        finished_at=NOW,
        duration_ms=120,
        usage=json.dumps({"totalTokens": "000123"}),
        created_at=NOW - timedelta(minutes=2),
    )
    failed = normalize_event(event(source, before), 3)[0]
    assert failed["failed"] == 1 and failed["job_tokens"] == 123
    assert failed["has_job_tokens"] == 1 and failed["attempt"] == 1
    queued = dict(before, status="queued", finished_at=None)
    assert normalize_event(event(source, queued, before=before), 4) == []
    completed = dict(before, status="succeeded", attempts=2, outcome="applied")
    rows = normalize_event(event(source, completed, before=before), 5)
    assert len(rows) == 1 and rows[0]["fact_id"] != failed["fact_id"]
    assert rows[0]["completed"] == 1 and rows[0]["task"] == JOB_KINDS[source]
    assert rows[0]["useful"] == int(source == "article_analysis_jobs")
    assert normalize_event(event(source, completed, operation="D"), 6)[0]["deleted"] == 1


def test_source_completion_keeps_its_publisher_and_missing_usage_stays_unknown():
    record = dict(id=IDENTITY, source_id=PARENT, status="succeeded", attempts=1, finished_at=NOW)
    row = normalize_event(event("ingestion_jobs", record), 3)[0]
    assert row["source_ids"] == [PARENT]
    assert row["job_tokens"] == row["has_job_tokens"] == 0


def test_unknown_attempt_identity_keeps_proven_completions_and_corrects_explicit_timestamps():
    first = dict(id=IDENTITY, status="failed", attempts=0, finished_at=NOW)
    later = dict(first, finished_at=NOW + timedelta(minutes=1))
    observed = normalize_event(event("article_analysis_jobs", first), 3)[0]
    repeated = normalize_event(event("article_analysis_jobs", later), 4)[0]
    assert observed["attempt"] == repeated["attempt"] == 0
    assert observed["fact_id"] != repeated["fact_id"]
    corrected = normalize_event(event("article_analysis_jobs", later, before=first), 5)
    assert [row["deleted"] for row in corrected] == [1, 0]
    assert corrected[0]["fact_id"] == observed["fact_id"]
    assert corrected[1]["fact_id"] == repeated["fact_id"]


def test_correction_moves_old_hour_with_tombstone_but_same_hour_updates_only_once():
    before = dict(id=IDENTITY, started_at=NOW - timedelta(hours=1), status="running", tokens={})
    after = dict(before, started_at=NOW, status="returned", duration_ms=12)
    rows = normalize_event(event("inference_calls", after, before=before), 3)
    assert len(rows) == 2 and [row["deleted"] for row in rows] == [1, 0]
    assert rows[0]["bucket_hour"] == NOW - timedelta(hours=1)
    same = normalize_event(
        event("inference_calls", after, before={**after, "status": "running"}), 4
    )
    assert len(same) == 1 and not same[0]["deleted"]
    removed = normalize_event(event("inference_calls", after, before=after, operation="D"), 5)
    assert len(removed) == 1 and removed[0]["deleted"] == 1


def test_sixth_raw_decision_ledger_preserves_undated_calls_exact_charges_and_stages():
    maximum = 2**64 - 2
    call = dict(
        proposal_id=IDENTITY,
        ordinal=2,
        stage="verification",
        stage_call=2,
        started_at=None,
        tokens=maximum,
        charged_tokens=2**63 - 1,
        escalated=True,
    )
    row = normalize_event(event("topic_decision_calls", call), 3)[0]
    assert row["domain"] == "decisions" and row["stage"] == "verification"
    assert row["decision_tokens"] == maximum and row["charged_tokens"] == 2**63 - 1
    assert row["event_time"] is None and row["ordinal"] == row["stage_call"] == 2
    assert row["escalated"] == 1 and row["entity_id"] == IDENTITY
    assert dirty_buckets([row], 3) == []  # never double count invocation usage
    missing = normalize_event(event("topic_decision_calls", {**call, "charged_tokens": None}), 4)[0]
    assert missing["charged_tokens"] is None
    run = normalize_event(
        event(
            "topic_decision_runs",
            dict(proposal_id=IDENTITY, status="deferred", reason="rate_limit", updated_at=NOW),
        ),
        3,
    )[0]
    assert run["task"] == "decision-run" and run["outcome"] == "rate_limit"
    relation = normalize_event(
        event("topic_relation_proposals", dict(id=IDENTITY, status="approved", created_at=NOW)), 3
    )[0]
    assert relation["task"] == "relationship-proposal"


def test_reader_signup_and_review_facts_have_distinct_meaning_and_stable_identity():
    opened = dict(article_id=IDENTITY, viewer_key="anonymous", opened_hour=NOW)
    first = normalize_event(event("article_opens", opened), 3)[0]
    replay = normalize_event(event("article_opens", opened), 3)[0]
    assert first == replay and first["opens"] == 1 and first["task"] == "open"
    signup = normalize_event(event("user_accounts", dict(id=IDENTITY, created_at=NOW)), 3)[0]
    assert signup["accounts_created"] == 1 and signup["viewer_key"] == ""
    decision = normalize_event(
        event("topic_proposals", dict(id=IDENTITY, status="approved", reviewed_at=NOW)), 3
    )[0]
    assert decision["task"] == "topic-decision" and decision["useful"] == 1
    assert normalize_event(event("topic_proposals", dict(id=IDENTITY, status="pending")), 3) == []


def test_publication_arrays_deduplicate_and_bad_durations_do_not_enter_quantiles():
    record = dict(id=IDENTITY, published_to_feed_at=NOW, discovered_at=NOW - timedelta(seconds=10))
    context = {"source_ids": [PARENT, PARENT], "topic_ids": [PARENT, PARENT], "automated": True}
    row = normalize_event(event("articles", record), 3, context=context)[0]
    assert row["source_ids"] == row["topic_ids"] == [PARENT]
    assert row["has_delay"] == row["automated"] == row["published"] == 1
    assert row["delay_seconds"] == 10
    future = normalize_event(
        event("articles", dict(record, discovered_at=NOW + timedelta(seconds=1))), 3
    )[0]
    assert future["has_delay"] == 0
    assert {item["domain"] for item in dirty_buckets([row], 3)} == {
        "publishing",
        "sources",
        "topics",
    }


def test_bad_or_missing_timestamps_never_create_misleading_activity():
    assert moment("not-a-date") is None and moment(None) is None
    assert moment(NOW.replace(tzinfo=None)) == NOW
    assert moment(NOW.isoformat()) == NOW
    assert (
        normalize_event(event("inference_calls", dict(id=IDENTITY, started_at="infinity")), 3) == []
    )
    assert normalize_event(event("unknown", dict(id=IDENTITY)), 3) == []
    assert normalize_event(event("articles", {}), 3) == []


def test_archived_publications_enrich_without_reading_deleted_main_database_rows():
    async def verify():
        class NoDatabase:
            async def execute(self, *_args):
                pytest.fail("Archive import queried missing operational article")

        record = dict(
            id=IDENTITY, published_to_feed_at=NOW, discovered_at=NOW - timedelta(seconds=10)
        )
        rows = await transform_events(
            NoDatabase(),
            [event("articles", record)],
            0,
            publication_context={IDENTITY: {"source_ids": [PARENT], "automated": True}},
        )
        assert len(rows) == 1 and rows[0]["published"] == rows[0]["automated"] == 1
        assert rows[0]["generation"] == 0
        assert await transform_events(NoDatabase(), [event("sources", dict(id=IDENTITY))], 3) == []

    asyncio.run(verify())


def test_schema_keeps_rich_typed_facts_and_only_idempotent_state_materialization():
    schema = "\n".join(initialization_sql())
    assert "argMaxState(tuple(" in schema and "sumState(" not in schema
    assert "AggregateFunction(quantilesTDigest(0.5, 0.95), Float64)" in schema
    assert "AggregateFunction(uniqCombined64, String)" in schema
    assert "PARTITION BY toYYYYMM(bucket_hour)" in schema
    assert " TTL " not in schema  # raw analytical history is retained
    assert "user_recommendations" not in TABLES  # no 500-row cache replacement churn
    for table in TABLES.values():
        projection = table.record_sql("source")
        assert "to_jsonb(" not in projection  # never detoast complete source rows
        assert all(
            f'"{private}"' not in projection
            for private in ("email", "profile", "state", "result", "body", "request_hash")
        )
    row = empty_fact("audience", IDENTITY, NOW, event_id=1, captured_at=NOW, generation=1)
    assert row["charged_tokens"] is None and row["source_ids"] == []


def test_reserialized_fact_timestamps_keep_utc_cohorts_and_deduplicate_dirty_work():
    row = empty_fact(
        "audience",
        IDENTITY,
        "2026-10-26T15:14:05+03:00",
        event_id=1,
        captured_at=NOW,
        generation=1,
    )
    assert row["bucket_hour"] == NOW and row["event_time"].minute == 14
    naive = empty_fact(
        "audience",
        IDENTITY,
        NOW.replace(tzinfo=None),
        event_id=2,
        captured_at=NOW,
        generation=2,
    )
    assert naive["event_time"] == NOW
    assert dirty_buckets([row, {**row, "bucket_hour": NOW.isoformat()}], 3) == [
        dict(domain="audience", bucket=NOW, generation=3)
    ]


def test_publication_coalescing_uses_current_associations_and_cancels_every_old_hour():
    async def verify():
        current = dict(id=IDENTITY, discovered_at=NOW - timedelta(days=1), published_to_feed_at=NOW)
        batches = iter(
            [
                [{"record": current}],
                [{"article_id": IDENTITY, "identity": PARENT}] * 2,
                [{"article_id": IDENTITY, "identity": PARENT}] * 2,
                [{"id": IDENTITY, "automated": True}],
            ]
        )

        class Cursor:
            async def fetchall(self):
                return next(batches)

        class Connection:
            async def execute(self, *_args):
                return Cursor()

        first = dict(current, published_to_feed_at=NOW - timedelta(hours=2))
        second = dict(current, published_to_feed_at=NOW - timedelta(hours=1))
        changes = [
            event("articles", second, before=first, identity=1),
            event("articles", current, before=second, identity=2),
            event("article_reviews", dict(id=PARENT, article_id=IDENTITY), identity=3),
        ]
        rows = await transform_events(Connection(), changes, 4)
        assert len(rows) == 3
        live = [row for row in rows if not row["deleted"]]
        assert len(live) == 1 and live[0]["source_ids"] == live[0]["topic_ids"] == [PARENT]
        assert live[0]["automated"] == 1
        assert {row["bucket_hour"] for row in rows if row["deleted"]} == {
            NOW - timedelta(hours=1),
            NOW - timedelta(hours=2),
        }
        assert all(row["event_id"] == 3 for row in rows)

    asyncio.run(verify())


def test_deleted_publications_emit_tombstones_even_when_current_article_is_gone():
    async def verify():
        record = dict(id=IDENTITY, published_to_feed_at=NOW, discovered_at=NOW)
        rows = await transform_events(
            None, [event("articles", record, operation="D")], 4, publication_context={}
        )
        assert len(rows) == 1 and rows[0]["deleted"] == 1
        assert await transform_events(None, [event("articles", {}, identity=1)], 4) == []
        assert await transform_events(None, [event("article_opens", {}, operation="T")], 4) == []

    asyncio.run(verify())
