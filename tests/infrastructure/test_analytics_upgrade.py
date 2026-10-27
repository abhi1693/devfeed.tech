"""Native cutover preserves retired facts through restart and fenced handoff."""

import asyncio
import copy
import json
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from devfeed_core import analytics_upgrade as upgrade

NOW = datetime(2026, 10, 1, tzinfo=UTC)
IDENTITY = "11111111-1111-1111-1111-111111111111"


class Result:
    def __init__(self, rows):
        self.rows = rows

    async def fetchone(self):
        return copy.deepcopy(self.rows[0]) if self.rows else None

    async def fetchall(self):
        return copy.deepcopy(self.rows)


class Connection:
    def __init__(self):
        self.checkpoint = None
        self.completed = None
        self.transactions = 0
        self.fail_fence = False
        self.jobs = []
        self.entities = []
        self.fail_queue = False
        self.dirty = []
        self.capture = NOW
        self.current = {}
        self.days = []
        self.calls = []

    @asynccontextmanager
    async def transaction(self):
        self.transactions += 1
        try:
            yield
        finally:
            self.transactions -= 1

    async def execute(self, sql, parameters=None):
        self.calls.append((sql, parameters))
        if "JOIN jsonb_to_recordset" in sql:
            table = re.search(r"FROM (\w+) p", sql)[1]
            return Result([{"record": row} for row in self.current.get(table, [])])
        if sql.startswith("SELECT day,metrics"):
            return Result(self.days)
        if sql.startswith("SELECT coalesce((SELECT"):
            return Result([{"captured": self.capture}])
        if sql.startswith("SELECT cursor"):
            return Result([self.checkpoint] if self.checkpoint else [])
        if sql.startswith("SELECT capture_started_at"):
            return Result([{"capture_started_at": self.capture}])
        if sql.startswith("INSERT INTO analytics_upgrade_checkpoint"):
            self.checkpoint = dict(cursor=copy.deepcopy(parameters[0].obj), complete=parameters[1])
        elif sql.startswith("UPDATE analytics_pipeline_state"):
            self.completed = parameters
        elif sql.startswith("SELECT id,attempts"):
            return Result(self.jobs)
        elif sql.startswith("SELECT id FROM"):
            return Result(self.entities)
        elif sql.startswith("INSERT INTO analytics_fact_refresh"):
            if self.fail_queue:
                raise RuntimeError("dirty queue failed")
            self.dirty.append(parameters)
        else:
            raise AssertionError(sql)
        return Result([])


async def fence(connection):
    assert connection.transactions == 1
    if connection.fail_fence:
        raise RuntimeError("lease superseded")


class Client:
    def __init__(self, connection):
        self.connection = connection
        self.settings = SimpleNamespace(
            analytics_rollup_interval_seconds=30,
            clickhouse_timeout_seconds=30,
            clickhouse_max_execution_seconds=20,
        )
        self.tables = {}
        self.records = {}
        self.writes = []
        self.engine = "Atomic"
        self.wrong_rename = False
        self.lose_rename = False
        self.context = {}
        self.queries = []
        self.truncates = []
        self.audit = []
        self.evidence = []

    def source(self, name, rows=(), *, view=True):
        self.tables[name] = dict(name=name, uuid="uuid-" + name, engine="ReplacingMergeTree")
        self.records[name] = list(rows)
        if view and name.endswith("_versions"):
            name = name.removesuffix("_versions")
            self.tables[name] = dict(name=name, uuid="view-" + name, engine="View")

    async def query(self, sql, parameters=None):
        assert self.connection.transactions == 0
        self.queries.append((sql, parameters))
        if sql.startswith("SELECT record,operation"):
            records = [
                row
                for row in self.audit
                if row["source_table"] == parameters["source"]
                and row["entity_key"] == parameters["entity"]
                and row["event_id"] < parameters["before"]
                and (
                    row["operation"] == "D"
                    or json.loads(row["record"]).get("status") in {"succeeded", "failed"}
                )
            ]
            return sorted(records, key=lambda row: row["event_id"], reverse=True)[:1]
        if sql.startswith("SELECT entity_key,finished_at"):
            wanted = {
                (value, parameters[name.replace("id_", "at_")])
                for name, value in parameters.items()
                if name.startswith("id_")
            }
            return [
                row
                for row in self.evidence
                if row["source_table"] == parameters["source"]
                and (row["entity_key"], row["finished_at"]) in wanted
            ]
        if "FROM system.tables" in sql:
            return copy.deepcopy(list(self.tables.values()))
        if "FROM system.databases" in sql:
            return [{"engine": self.engine}]
        if "SELECT capture_started_at" in sql:
            return self.records.get(sql.split("`")[1], [])
        if "SELECT source_table" in sql:
            return self.truncates
        if "SELECT article_id" in sql:
            return self.context.get(sql.split("`")[1], [])
        name = sql.split("`")[1]
        rows = self.records.get(name, [])
        rows = [
            row
            for row in rows
            if not row.get("_deleted") and row["_version"] >= parameters["truncate"]
        ]
        table = name.removeprefix("analytics_v1_archive_").removesuffix("_versions")
        keys = upgrade.V1_KEYS[table]
        rows = sorted(rows, key=lambda row: tuple(row[key] for key in keys))
        if "key0" in parameters:
            after = tuple(parameters["key" + str(i)] for i in range(len(keys)))
            rows = [
                row
                for row in rows
                if tuple(
                    upgrade._datetime(row[key]) if key == "opened_hour" else row[key]
                    for key in keys
                )
                > after
            ]
        if "key_0_" in " ".join(parameters):
            selected = []
            for row in rows:
                for index in range(len(parameters)):
                    if f"key_{index}_{keys[0]}" not in parameters:
                        break
                    if all(
                        (upgrade._datetime(row[key]) if key == "opened_hour" else row[key])
                        == parameters[f"key_{index}_{key}"]
                        for key in keys
                    ):
                        selected.append(row)
                        break
            return copy.deepcopy(selected)
        return copy.deepcopy(rows[: parameters["batch"]])

    async def execute(self, sql):
        assert self.connection.transactions == 0
        names = re.findall(r"`([^`]+)`", sql)
        if sql.startswith(("SYSTEM STOP MERGES", "ALTER TABLE")):
            assert names[0] in self.tables
        elif sql.startswith("RENAME"):
            source, target = names
            meta = self.tables.pop(source)
            if not self.lose_rename:
                self.tables[target] = dict(
                    meta, name=target, uuid="wrong" if self.wrong_rename else meta["uuid"]
                )
                self.records[target] = self.records.pop(source)
        elif sql.startswith("CREATE TABLE"):
            assert upgrade.PENDING_ARCHIVE in sql or upgrade.ATTEMPT_EVIDENCE in sql
        else:
            assert sql.startswith("DROP VIEW")
            del self.tables[names[0]]

    async def insert(self, table, rows):
        assert self.connection.transactions == 0
        self.writes.append((table, copy.deepcopy(rows)))
        if table == upgrade.PENDING_ARCHIVE:
            self.audit.extend(copy.deepcopy(rows))
        if table == upgrade.ATTEMPT_EVIDENCE:
            self.evidence.extend(copy.deepcopy(rows))


def runtime():
    connection = Connection()
    client = Client(connection)
    importer = upgrade.NativeUpgrade(SimpleNamespace(analytics_backfill_batch_size=1), client)
    return connection, client, importer


async def finish(connection, importer):
    for _ in range(200):
        if not await importer.once(connection, 20, fence=fence):
            return
    raise AssertionError("Migration failed to converge")


def run(coroutine):
    return asyncio.run(coroutine)


def test_fresh_install_completes_without_archiving_or_changing_capture_boundary():
    connection, client, importer = runtime()
    run(finish(connection, importer))
    assert connection.checkpoint["complete"] and connection.completed == (None, None, None)
    assert not client.writes


@pytest.mark.parametrize("archived_status", [False, True])
def test_cutover_retains_every_physical_row_original_boundary_and_expired_reader_receipt(
    archived_status,
):
    connection, client, importer = runtime()
    source = upgrade.ARCHIVES["_analytics_status"] if archived_status else "_analytics_status"
    captured = NOW - timedelta(days=60)
    client.source(source, [{"capture_started_at": captured.isoformat()}], view=False)
    client.source(
        "article_opens_versions",
        [
            dict(
                article_id=IDENTITY,
                viewer_key="anonymous",
                opened_hour=(NOW - timedelta(days=90)).isoformat(),
                _version=8,
                _deleted=0,
            ),
            dict(
                article_id=IDENTITY,
                viewer_key="deleted",
                opened_hour=(NOW - timedelta(days=90)).isoformat(),
                _version=9,
                _deleted=1,
            ),
            dict(
                article_id=IDENTITY,
                viewer_key="truncated",
                opened_hour=(NOW - timedelta(days=90)).isoformat(),
                _version=2,
                _deleted=0,
            ),
        ],
    )
    original = copy.deepcopy(client.records["article_opens_versions"])
    client.source("_analytics_truncates", view=False)
    client.truncates = [{"source_table": "article_opens", "version": 5}]
    run(finish(connection, importer))
    archive = upgrade.ARCHIVES["article_opens_versions"]
    assert client.records[archive] == original
    assert client.tables[archive]["uuid"] == "uuid-article_opens_versions"
    assert "article_opens" not in client.tables
    facts = [row for name, rows in client.writes if name == "analytics_fact_events" for row in rows]
    assert len(facts) == 1 and facts[0]["opens"] == 1
    assert facts[0]["generation"] == 0 and facts[0]["event_id"] == 8
    assert facts[0]["captured_at"] == captured
    assert connection.completed == (
        captured.isoformat(),
        (captured.date() - timedelta(days=29)).isoformat(),
        captured.isoformat(),
    )


def test_restart_after_rename_before_checkpoint_reuses_identical_archived_table():
    connection, client, importer = runtime()
    client.source("articles_versions")
    run(importer.once(connection, 20, fence=fence))
    run(importer.once(connection, 20, fence=fence))
    connection.fail_fence = True
    with pytest.raises(RuntimeError, match="superseded"):
        run(importer.once(connection, 20, fence=fence))
    assert upgrade.ARCHIVES["articles_versions"] in client.tables
    connection.fail_fence = False
    restarted = upgrade.NativeUpgrade(importer.settings, client)
    run(finish(connection, restarted))
    assert connection.checkpoint["complete"]


@pytest.mark.parametrize(
    "failure", ["conflict", "engine", "identity", "wrong-rename", "lost-rename"]
)
def test_archive_refuses_ambiguous_or_changed_storage_identity(failure):
    connection, client, importer = runtime()
    client.source("articles_versions")
    run(importer.once(connection, 20, fence=fence))
    if failure == "conflict":
        client.source(upgrade.ARCHIVES["articles_versions"], view=False)
    elif failure == "engine":
        client.tables["articles_versions"]["engine"] = "Memory"
    else:
        run(importer.once(connection, 20, fence=fence))
        if failure == "identity":
            client.tables["articles_versions"]["uuid"] = "changed"
        client.wrong_rename = failure == "wrong-rename"
        client.lose_rename = failure == "lost-rename"
    with pytest.raises(RuntimeError):
        run(importer.once(connection, 20, fence=fence))
    assert not connection.checkpoint["complete"] and connection.completed is None


def test_non_atomic_database_is_rejected_before_mutation():
    connection, client, importer = runtime()
    client.source("articles_versions")
    client.engine = "Ordinary"
    with pytest.raises(RuntimeError, match="Atomic"):
        run(importer.once(connection, 20, fence=fence))
    assert "articles_versions" in client.tables and connection.checkpoint is None


@pytest.mark.parametrize("capture", [None, NOW])
def test_empty_legacy_status_preserves_pg_capture_boundary(capture):
    connection, client, importer = runtime()
    connection.capture = capture
    client.source("_analytics_status", view=False)
    run(finish(connection, importer))
    if capture:
        assert connection.completed[0] == capture.isoformat()
    else:
        assert connection.completed == (None, None, None)


def test_full_import_path_enriches_publications_jobs_and_daily_archive():
    connection, client, importer = runtime()
    client.source(
        "articles_versions",
        [
            dict(
                id=IDENTITY,
                published_to_feed_at=NOW.isoformat(),
                discovered_at=(NOW - timedelta(seconds=3)).isoformat(),
                _version=0,
                _deleted=0,
            )
        ],
    )
    client.source(
        "ingestion_jobs_versions",
        [
            dict(
                id=IDENTITY, status="succeeded", finished_at=NOW.isoformat(), _version=0, _deleted=0
            )
        ],
    )
    client.source(
        "overview_daily_versions",
        [dict(day="2026-09-30", metrics={"opens": 5}, _version=0, _deleted=0)],
    )
    connection.jobs = [dict(id=UUID(IDENTITY), attempts=2, finished_at=NOW, status="succeeded")]
    run(finish(connection, importer))
    facts = [row for name, rows in client.writes if name == "analytics_fact_events" for row in rows]
    assert {row["domain"] for row in facts} == {"publishing", "processing"}
    assert any(
        name == "analytics_audience_archive_daily" and rows[0]["opens"] == 5
        for name, rows in client.writes
        if rows
    )


def test_current_entity_with_corrected_timestamp_does_not_reimport_its_old_cohort():
    connection, client, importer = runtime()
    connection.entities = [{"id": UUID(IDENTITY)}]
    client.source(
        "articles_versions",
        [
            dict(
                id=IDENTITY,
                published_to_feed_at=(NOW - timedelta(days=30)).isoformat(),
                _version=5,
                _deleted=0,
            )
        ],
    )
    run(finish(connection, importer))
    assert client.records[upgrade.ARCHIVES["articles_versions"]][0]["_version"] == 5
    assert not any(rows for name, rows in client.writes if name == "analytics_fact_events")
    assert connection.checkpoint["complete"]


def test_insert_failure_replays_same_fact_without_advancing_source_checkpoint():
    connection, client, importer = runtime()
    client.source(
        "article_opens_versions",
        [
            dict(
                article_id=IDENTITY,
                viewer_key="anonymous",
                opened_hour=NOW.isoformat(),
                _version=2,
                _deleted=0,
            )
        ],
    )
    for _ in range(100):
        run(importer.once(connection, 20, fence=fence))
        if connection.checkpoint["cursor"]["phase"] == "import":
            break
    previous = copy.deepcopy(connection.checkpoint)
    connection.fail_queue = True
    with pytest.raises(RuntimeError, match="dirty queue failed"):
        run(importer.once(connection, 20, fence=fence))
    assert connection.checkpoint == previous
    connection.fail_queue = False
    run(finish(connection, importer))
    facts = [rows for name, rows in client.writes if name == "analytics_fact_events" and rows]
    assert len(facts) == 2 and facts[0] == facts[1]
    assert connection.dirty and connection.dirty[0][2] == ["audience"]


def test_retirement_refuses_live_tables_and_unknown_checkpoint():
    connection, client, importer = runtime()
    connection.checkpoint = dict(cursor=dict(phase="retire", index=0), complete=False)
    client.tables["articles"] = dict(name="articles", engine="MergeTree")
    with pytest.raises(RuntimeError, match="non-view"):
        run(importer.once(connection, 20, fence=fence))
    connection.checkpoint["cursor"]["phase"] = "unknown"
    with pytest.raises(RuntimeError, match="Unknown"):
        run(importer.once(connection, 20, fence=fence))


@pytest.mark.parametrize(
    "table,after,kind",
    [
        ("article_opens", [NOW.isoformat(), IDENTITY, "anonymous"], "DateTime64"),
        ("overview_daily", ["2026-01-01"], "Date"),
        ("topic_decision_calls", [IDENTITY, 2], "Int64"),
        ("articles", [IDENTITY], "String"),
    ],
)
def test_keyset_pages_bind_frozen_v1_identity_types(table, after, kind):
    _, _, importer = runtime()
    sql, params = importer._page(table, dict(after=after))
    assert kind in sql and "LIMIT {batch:UInt32}" in sql
    assert "AND tuple(" in sql
    assert params["key0"] == (NOW if table == "article_opens" else after[0])
    assert params["batch"] == 1


def test_closed_daily_archive_preserves_unknowns_and_does_not_double_add_capture_day():
    _, client, importer = runtime()
    run(
        importer._archive_days(
            [
                dict(
                    day="2026-09-30",
                    metrics='{"opens":4,"readers":null,"accounts":2,"published":3}',
                ),
                dict(day="2026-09-29", metrics={"readers": 5}),
                dict(day="2026-10-01", metrics={"opens": 100}),
            ],
            NOW,
        )
    )
    name, rows = client.writes[0]
    assert name == "analytics_audience_archive_daily" and len(rows) == 2
    assert rows[0]["opens"] == 4 and rows[0]["readers"] is None and rows[0]["version"] == 0
    assert rows[1]["opens"] is None


def test_attempt_metadata_is_used_only_for_matching_retained_completion():
    connection, _, importer = runtime()
    connection.jobs = [dict(id=UUID(IDENTITY), attempts=3, finished_at=NOW, status="succeeded")]
    rows = [dict(id=IDENTITY, finished_at=NOW.isoformat()), dict(id="missing", finished_at=None)]
    run(importer._attempts(connection, "ingestion_jobs", rows))
    assert rows[0]["attempts"] == 3 and "attempts" not in rows[1]
    rows = [dict(id=IDENTITY, finished_at=(NOW - timedelta(seconds=1)).isoformat())]
    run(importer._attempts(connection, "ingestion_jobs", rows))
    assert "attempts" not in rows[0]
    connection.jobs[0]["status"] = "queued"
    rows = [dict(id=IDENTITY, finished_at=NOW.isoformat())]
    run(importer._attempts(connection, "ingestion_jobs", rows))
    assert "attempts" not in rows[0]


def pending(source="inference_calls", *, identity=IDENTITY, at=NOW, operation="I", **record):
    return dict(
        id=17,
        source_table=source,
        format_version=1,
        operation=operation,
        created_at=NOW,
        before_record=None,
        record=dict(id=identity, started_at=at.isoformat(), **record),
    )


def native_facts(events):
    from devfeed_core.analytics_transform import FactTransformer

    return [fact for event in events for fact in FactTransformer(None).normalize(event, 40)]


def test_native_events_bypass_the_finite_pending_upgrade_entirely():
    connection, client, importer = runtime()
    events = [dict(format_version=2), dict(format_version=3)]
    assert run(importer.normalize_pending(connection, events, 40)) is events
    assert not connection.calls and not client.queries and not client.writes


@pytest.mark.parametrize("event", [{}, {"format_version": 0}, {"format_version": 4}])
def test_pending_upgrade_rejects_missing_or_unknown_capture_format_without_a_compatibility_default(
    event,
):
    connection, client, importer = runtime()
    with pytest.raises(ValueError, match="Unsupported analytical capture format"):
        run(importer.normalize_pending(connection, [event], 40))
    assert not connection.calls and not client.queries and not client.writes


@pytest.mark.parametrize("archived", [False, True])
def test_pending_update_hydrates_current_and_cancels_original_and_cold_cohorts(archived):
    connection, client, importer = runtime()
    event = pending()
    connection.current["inference_calls"] = [
        dict(event["record"], started_at=(NOW + timedelta(hours=3)).isoformat(), tokens={})
    ]
    table = "inference_calls_versions"
    name = upgrade.ARCHIVES[table] if archived else table
    client.source(
        name,
        [dict(event["record"], started_at=(NOW - timedelta(hours=2)).isoformat(), _version=2)],
        view=False,
    )
    native = run(importer.normalize_pending(connection, [event], 40))
    facts = native_facts(native)
    assert sorted((row["bucket_hour"].hour, row["deleted"]) for row in facts) == [
        (0, 1),
        (3, 0),
        (22, 1),
    ]
    assert all(row["generation"] == 40 for row in facts)
    assert len([sql for sql, _ in connection.calls if "jsonb_to_recordset" in sql]) == 1
    archived_events = [rows for table, rows in client.writes if table == upgrade.PENDING_ARCHIVE]
    assert json.loads(archived_events[0][0]["record"]) == event["record"]
    assert archived_events[0][0]["event_id"] == 17
    # The audit table is initialized once; replay only repeats immutable rows.
    client.execute = lambda _: (_ for _ in ()).throw(AssertionError("DDL repeated"))
    assert run(importer.normalize_pending(connection, [event], 40)) == native


def test_pending_deleted_entity_cancels_both_pending_and_future_cold_import_keys():
    connection, client, importer = runtime()
    event = pending(operation="D")
    client.source(
        "inference_calls_versions",
        [dict(event["record"], started_at=(NOW - timedelta(hours=2)).isoformat(), _version=8)],
    )
    native = run(importer.normalize_pending(connection, [event], 40))
    facts = native_facts(native)
    assert len(facts) == 2 and all(row["deleted"] for row in facts)
    assert {row["bucket_hour"] for row in facts} == {NOW, NOW - timedelta(hours=2)}


def test_pending_current_cohort_is_never_tombstoned_by_an_equal_version():
    connection, _, importer = runtime()
    event = pending()
    connection.current["inference_calls"] = [event["record"]]
    native = run(importer.normalize_pending(connection, [event], 40))
    facts = native_facts(native)
    assert len(facts) == 1 and facts[0]["deleted"] == 0


@pytest.mark.parametrize("status", ["succeeded", "queued", None])
def test_pending_jobs_retain_old_attempt_and_map_only_matching_current_terminal_completion(status):
    connection, client, importer = runtime()
    older = pending(
        "ingestion_jobs", status="failed", finished_at=(NOW - timedelta(hours=2)).isoformat()
    )
    latest = pending("ingestion_jobs", status="succeeded", finished_at=NOW.isoformat())
    queued = pending("ingestion_jobs", status="queued", finished_at=None)
    queued["id"] = 18
    latest["id"] = 19
    if status:
        connection.current["ingestion_jobs"] = [dict(latest["record"], attempts=2, status=status)]
    client.source("ingestion_jobs_versions", [dict(latest["record"], _version=8)])
    native = run(importer.normalize_pending(connection, [older, queued, latest], 40))
    facts = native_facts(native)
    assert any(
        row["bucket_hour"] == NOW - timedelta(hours=2)
        and not row["deleted"]
        and row["attempt"] == 0
        for row in facts
    )
    latest_positive = [row for row in facts if row["bucket_hour"] == NOW and not row["deleted"]]
    assert bool(latest_positive) == (status == "succeeded")
    assert {row["attempt"] for row in latest_positive} == ({2} if status == "succeeded" else set())
    if status == "succeeded":
        assert any(row["bucket_hour"] == NOW and row["deleted"] for row in facts)


def test_pending_job_delete_retires_known_unknown_attempt_keys():
    connection, client, importer = runtime()
    event = pending("ingestion_jobs", operation="D", status="failed", finished_at=NOW.isoformat())
    client.source("ingestion_jobs_versions", [dict(event["record"], _version=8)])
    facts = native_facts(run(importer.normalize_pending(connection, [event], 40)))
    assert facts and all(row["deleted"] and row["attempt"] == 0 for row in facts)


def test_pending_job_timestamp_correction_does_not_invent_a_prior_attempt():
    connection, client, importer = runtime()
    event = pending("ingestion_jobs", status="succeeded", finished_at=NOW.isoformat())
    corrected = dict(
        event["record"], finished_at=(NOW + timedelta(hours=2)).isoformat(), attempts=1
    )
    connection.current["ingestion_jobs"] = [corrected]
    client.source("ingestion_jobs_versions", [dict(event["record"], _version=3)])
    facts = native_facts(run(importer.normalize_pending(connection, [event], 40)))
    positive = [row for row in facts if not row["deleted"]]
    assert len(positive) == 1 and positive[0]["attempt"] == 1
    assert positive[0]["bucket_hour"] == NOW + timedelta(hours=2)
    assert all(row["deleted"] for row in facts if row["bucket_hour"] == NOW)


def test_pending_retry_evidence_survives_delivery_page_and_process_boundaries():
    connection, client, importer = runtime()
    first = pending("ingestion_jobs", status="failed", finished_at=NOW.isoformat())
    current = dict(
        first["record"],
        status="succeeded",
        attempts=2,
        finished_at=(NOW + timedelta(hours=2)).isoformat(),
    )
    connection.current["ingestion_jobs"] = [current]
    old_facts = native_facts(run(importer.normalize_pending(connection, [first], 40)))
    assert all(row["deleted"] for row in old_facts if row["bucket_hour"] == NOW)
    restarted = upgrade.NativeUpgrade(importer.settings, client)
    queued = dict(first, id=18, record=dict(first["record"], status="queued"))
    facts = native_facts(run(restarted.normalize_pending(connection, [queued], 41)))
    prior = [row for row in facts if row["bucket_hour"] == NOW and not row["deleted"]]
    assert len(prior) == 1 and prior[0]["attempt"] == 0
    assert prior[0]["event_id"] == 18 and prior[0]["generation"] == 40
    probe = next((sql, p) for sql, p in client.queries if sql.startswith("SELECT record,operation"))
    assert "ORDER BY event_id DESC LIMIT 1" in probe[0]
    assert probe[1] == {"source": "ingestion_jobs", "entity": IDENTITY, "before": 18}


@pytest.mark.parametrize("prior_operation,finish", [("D", NOW), ("I", None)])
def test_queued_transition_requires_a_preceding_terminal_completion(prior_operation, finish):
    connection, client, importer = runtime()
    first = pending(
        "ingestion_jobs",
        operation=prior_operation,
        status="failed",
        finished_at=finish.isoformat() if finish else None,
    )
    queued = dict(first, id=18, operation="I", record=dict(first["record"], status="queued"))
    native = run(importer.normalize_pending(connection, [first, queued], 40))
    assert not any(not row["deleted"] for row in native_facts(native))
    assert not client.evidence


def test_queued_without_a_prior_completion_does_not_create_retry_evidence():
    connection, client, importer = runtime()
    event = pending("ingestion_jobs", status="queued", finished_at=None)
    assert run(importer.normalize_pending(connection, [event], 40))[0]["operation"] == "D"
    assert not client.evidence


def test_delete_keeps_a_proven_earlier_attempt_but_retires_the_latest_completion():
    connection, client, importer = runtime()
    earlier = pending("ingestion_jobs", status="failed", finished_at=NOW.isoformat())
    queued = dict(earlier, id=18, record=dict(earlier["record"], status="queued"))
    run(importer.normalize_pending(connection, [earlier, queued], 40))
    client.source("ingestion_jobs_versions", [dict(earlier["record"], _version=3)])
    latest = dict(
        earlier,
        id=19,
        operation="D",
        record=dict(
            earlier["record"],
            status="succeeded",
            finished_at=(NOW + timedelta(hours=2)).isoformat(),
        ),
    )
    facts = native_facts(run(importer.normalize_pending(connection, [latest], 41)))
    assert any(row["bucket_hour"] == NOW and not row["deleted"] for row in facts)
    assert all(row["deleted"] for row in facts if row["bucket_hour"] == NOW + timedelta(hours=2))


@pytest.mark.parametrize("operation", ["I", "D"])
def test_pending_opens_preserve_retention_but_respect_explicit_deletion(operation):
    connection, _, importer = runtime()
    event = pending(
        "article_opens",
        operation=operation,
        article_id=IDENTITY,
        viewer_key="anonymous",
        opened_hour=NOW.isoformat(),
    )
    facts = native_facts(run(importer.normalize_pending(connection, [event], 40)))
    assert len(facts) == 1 and facts[0]["deleted"] == int(operation == "D")


@pytest.mark.parametrize("present", [False, True])
def test_pending_dimension_hydrates_current_or_retires_deleted_identity(present):
    connection, _, importer = runtime()
    event = pending("sources", name="old", approval_status="approved", enabled=True)
    if present:
        connection.current["sources"] = [dict(event["record"], name="new")]
    native = run(importer.normalize_pending(connection, [event], 40))
    assert len(native) == 1
    assert native[0]["operation"] == ("I" if present else "D")
    assert native[0]["record"]["name"] == ("new" if present else "old")


def test_pending_truncates_retired_sources_and_before_images_are_archived_without_replay():
    connection, client, importer = runtime()
    event = pending(operation="T")
    old = pending("user_recommendations")
    old["id"] = 18
    old["before_record"] = {"rich": [1, {"future": True}]}
    current = dict(event, id=19, format_version=2)
    assert run(importer.normalize_pending(connection, [event, old, current], 40)) == [current]
    rows = client.writes[0][1]
    assert len(rows) == 2 and json.loads(rows[1]["before_record"]) == old["before_record"]
    assert not connection.calls


def test_pending_cold_probe_honors_truncate_boundary_and_composite_identity():
    connection, client, importer = runtime()
    event = pending("topic_decision_calls", proposal_id=IDENTITY, ordinal=4, tokens=17)
    client.source("topic_decision_calls_versions", [dict(event["record"], _version=2)])
    client.source(upgrade.ARCHIVES["_analytics_truncates"], view=False)
    client.truncates = [{"source_table": "topic_decision_calls", "version": 3}]
    native = run(importer.normalize_pending(connection, [event], 40))
    assert len(native) == 1 and native[0]["operation"] == "D"
    probe = next((sql, p) for sql, p in client.queries if "key_0_ordinal" in sql)
    assert probe[1]["key_0_ordinal"] == 4 and probe[1]["truncate"] == 3


def test_pending_receipt_probe_binds_timestamp_identity_and_jsonb_keyset_once():
    connection, client, importer = runtime()
    event = pending(
        "article_opens", article_id=IDENTITY, viewer_key="anon", opened_hour=NOW.isoformat()
    )
    client.source("article_opens_versions", [dict(event["record"], _version=2)])
    run(importer.normalize_pending(connection, [event, dict(event, id=18)], 40))
    _, parameters = next((sql, p) for sql, p in client.queries if "key_0_opened_hour" in sql)
    assert parameters["key_0_opened_hour"] == NOW
    assert "key_1_opened_hour" not in parameters
    probe = next((sql, p) for sql, p in connection.calls if "jsonb_to_recordset" in sql)
    assert "TIMESTAMP WITH TIME ZONE" in probe[0] and "UUID" in probe[0]
    assert len(probe[1][0].obj) == 1


def test_pending_daily_observation_hydrates_latest_pg_snapshot_with_delivery_version():
    connection, client, importer = runtime()
    event = pending("overview_daily", day="2026-09-30", metrics={"opens": 2})
    connection.days = [dict(day="2026-09-30", metrics={"opens": 9, "published": 3})]
    assert run(importer.normalize_pending(connection, [event], 47)) == []
    rows = next(
        rows for table, rows in client.writes if table == "analytics_audience_archive_daily"
    )
    assert rows == [
        dict(
            day="2026-09-30", opens=9, readers=None, accounts_created=None, published=3, version=47
        )
    ]


def test_pending_missing_pg_day_retains_original_snapshot_and_rejects_unknown_boundary():
    connection, client, importer = runtime()
    event = pending("overview_daily", day="2026-09-30", metrics={"opens": 2})
    run(importer.normalize_pending(connection, [event], 48))
    rows = next(
        rows for table, rows in client.writes if table == "analytics_audience_archive_daily"
    )
    assert rows[0]["opens"] == 2
    connection.capture = None
    with pytest.raises(RuntimeError, match="capture boundary"):
        run(importer.normalize_pending(connection, [event], 49))


def test_publication_context_uses_archived_children_and_bounded_article_identifiers():
    _, client, importer = runtime()
    state = dict(
        archived={
            name + "_versions": "uuid"
            for name in ("article_origins", "article_topics", "article_reviews")
        }
    )
    for table, value in (
        ("article_origins", [IDENTITY]),
        ("article_topics", [IDENTITY]),
        ("article_reviews", 1),
    ):
        client.context[upgrade.ARCHIVES[table + "_versions"]] = [
            dict(article_id=IDENTITY, value=value)
        ]
    result = run(
        importer._publication_context(
            [dict(id=IDENTITY, published_to_feed_at=NOW.isoformat())], state
        )
    )
    assert result[IDENTITY] == dict(source_ids=[IDENTITY], topic_ids=[IDENTITY], automated=True)
    assert all(params["ids"] == [IDENTITY] for _, params in client.queries)
    assert (
        "created_at <=" in client.queries[-1][0]
        and "countIf(automation='{}'" in client.queries[-1][0]
    )
    assert run(importer._publication_context([dict(id=IDENTITY)], dict(archived={}))) == {
        IDENTITY: dict(source_ids=[], topic_ids=[], automated=False)
    }


@pytest.mark.integration
def test_real_cutover_preserves_expired_receipts_and_cold_versions(database):
    import os
    import uuid

    import psycopg
    from devfeed_core.analytics import AnalyticsClient
    from devfeed_core.analytics_worker import postgres_dsn
    from devfeed_core.config import Settings
    from psycopg.rows import dict_row

    origin = os.environ.get("DEVFEED_TEST_CLICKHOUSE_URL")
    if not origin:
        pytest.skip("Set DEVFEED_TEST_CLICKHOUSE_URL for native upgrade integration tests")
    name = os.environ.get("DEVFEED_TEST_CLICKHOUSE_DATABASE", "devfeed_test")
    if not name.endswith("_test"):
        pytest.fail("Disposable ClickHouse database must end with _test")
    settings = Settings(
        _env_file=None,
        database_url=os.environ["DEVFEED_TEST_DATABASE_URL"],
        redis_url=os.environ["DEVFEED_TEST_REDIS_URL"],
        clickhouse_url=origin,
        clickhouse_database=name,
        clickhouse_username=os.environ.get("DEVFEED_TEST_CLICKHOUSE_USERNAME", "default"),
        clickhouse_password=os.environ.get("DEVFEED_TEST_CLICKHOUSE_PASSWORD", ""),
        analytics_backfill_batch_size=1,
    )
    target = "upgrade_" + uuid.uuid4().hex[:12] + "_test"

    async def verify():
        async with AnalyticsClient(settings) as admin:
            await admin.execute(f"CREATE DATABASE `{target}` ENGINE=Atomic")
            try:
                async with AnalyticsClient(
                    settings.model_copy(update={"clickhouse_database": target})
                ) as client:
                    await client.initialize()
                    client.insert_settings = {"async_insert_busy_timeout_ms": 1}
                    await client.execute(
                        "CREATE TABLE article_opens_versions(article_id String,viewer_key String, "
                        "opened_hour DateTime64(6,'UTC'),_version UInt64,_deleted UInt8) "
                        "ENGINE=ReplacingMergeTree(_version) "
                        "ORDER BY(opened_hour,article_id,viewer_key) "
                        "SETTINGS max_bytes_to_merge_at_max_space_in_pool=0"
                    )
                    await client.execute(
                        "CREATE VIEW article_opens AS SELECT * FROM article_opens_versions FINAL"
                    )
                    for version in (1, 2):
                        await client.execute(
                            "INSERT INTO article_opens_versions VALUES "
                            "({id:String},'retained-reader',{at:DateTime64(6,'UTC')},{v:UInt64},0)",
                            {"id": IDENTITY, "at": NOW - timedelta(days=90), "v": version},
                        )
                    before = await client.query(
                        "SELECT toString(uuid) AS uuid FROM system.tables "
                        "WHERE database=currentDatabase() AND name='article_opens_versions'"
                    )
                    async with await psycopg.AsyncConnection.connect(
                        postgres_dsn(settings), autocommit=True, row_factory=dict_row
                    ) as connection:
                        await connection.execute(
                            "UPDATE analytics_pipeline_state SET capture_started_at=%s, "
                            "native_capture_started_at=NULL,native_import_complete=false",
                            (NOW,),
                        )
                        importer = upgrade.NativeUpgrade(settings, client)

                        async def real_fence(conn):
                            assert await (
                                await conn.execute(
                                    "SELECT singleton FROM analytics_pipeline_state "
                                    "WHERE singleton FOR UPDATE"
                                )
                            ).fetchone()

                        for _ in range(100):
                            if not await importer.once(connection, 20, fence=real_fence):
                                break
                        else:
                            pytest.fail("Bounded native upgrade did not complete")
                        state = await (
                            await connection.execute(
                                "SELECT native_import_complete,native_capture_started_at "
                                "FROM analytics_pipeline_state"
                            )
                        ).fetchone()
                        assert state["native_import_complete"]
                        assert state["native_capture_started_at"] == NOW
                        await connection.execute(
                            "UPDATE analytics_fact_refresh SET available_at=clock_timestamp()"
                        )
                        assert await importer.rollups.refresh_one(connection, 30, fence=real_fence)
                    table = upgrade.ARCHIVES["article_opens_versions"]
                    after = await client.query(
                        "SELECT toString(uuid) AS uuid FROM system.tables "
                        "WHERE database=currentDatabase() AND name={table:String}",
                        {"table": table},
                    )
                    assert after == before
                    assert await client.query(f"SELECT count() AS n FROM `{table}`") == [{"n": 2}]
                    assert await client.query(
                        "SELECT sum(opens) AS opens FROM analytics_fact_events"
                    ) == [{"opens": 1}]
                    assert await client.query(
                        "SELECT sum(opens) AS opens FROM analytics_audience_hourly"
                    ) == [{"opens": 1}]
            finally:
                await admin.execute(f"DROP DATABASE `{target}` SYNC")

    run(verify())
