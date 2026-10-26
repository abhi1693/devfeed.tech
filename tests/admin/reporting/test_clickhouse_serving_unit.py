"""Current boards and independent chart reads use bounded native analytical serving."""

import asyncio
import json
import threading
import uuid
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from functools import wraps
from types import SimpleNamespace

import pytest
from devfeed_admin_api import analytics_runtime, article_pipeline, overview, source_pipeline
from devfeed_admin_api.analytics_current import CARD_KINDS, CurrentCards
from devfeed_admin_api.analytics_runtime import AnalyticsRuntime, get_analytics
from devfeed_admin_api.auth import require_admin
from devfeed_admin_api.dependencies import get_session, get_session_factory
from devfeed_admin_api.overview_charts import CHART_SECTIONS
from devfeed_admin_api.overview_windows import current_window, recent_window
from devfeed_core.analytics import AnalyticsUnavailable
from devfeed_core.analytics_card_types import PipelineBoard, PipelineColumn
from devfeed_core.analytics_observation import pipeline_policy_version
from devfeed_core.config import get_settings
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

NOW = datetime(2026, 10, 27, 10, tzinfo=UTC)
ID = "00000000-0000-0000-0000-000000000001"


def run_async(test):
    @wraps(test)
    def run(*args, **kwargs):
        return asyncio.run(test(*args, **kwargs))

    return run


class Client:
    def __init__(self, results=None):
        self.results = results or {}
        self.queries = []
        self.status_reads = 0
        self.failed = False
        self.ready = True
        self.closed = False
        self.policy = pipeline_policy_version()
        self.card_status = {
            "version": 1,
            "ready": 1,
            "policy": self.policy,
            "target_policy": self.policy,
            "generated_at": NOW,
            "updated_at": NOW,
            "catalog_revision": "[]",
            "catalog_target_revision": "[]",
            "lag_seconds": 0,
        }
        self.manifest_age = timedelta()
        self.missing_shard = False

    async def close(self):
        self.closed = True

    async def status(self):
        self.status_reads += 1
        await asyncio.sleep(0)
        if self.failed:
            raise AnalyticsUnavailable("offline")
        return {"version": 1, "ready": self.ready, "capture_started_at": NOW, "lag_seconds": 0}

    async def query(self, sql, parameters=None):
        self.queries.append((sql, deepcopy(parameters)))
        await asyncio.sleep(0)
        if self.failed:
            raise AnalyticsUnavailable("offline")
        if "admin:card-status" in sql:
            return [deepcopy(self.card_status)]
        if "admin:card-manifest" in sql:
            return [
                {
                    "kind": kind,
                    "shard": shard,
                    "generation": shard + 100,
                    "updated_at": NOW - self.manifest_age,
                    "item_count": 1,
                }
                for kind in parameters["kinds"]
                for shard in range(255 if self.missing_shard else 256)
            ]
        name = sql.split("/* ", 1)[1].split(" ", 1)[0]
        return deepcopy(self.results.get(name, []))


class InterleavingClient(Client):
    def __init__(self, statuses, manifest_bases, results=None):
        super().__init__(results)
        self.statuses = statuses
        self.manifest_bases = manifest_bases
        self.status_index = self.manifest_index = 0

    async def query(self, sql, parameters=None):
        rows = await super().query(sql, parameters)
        if "admin:card-status" in sql:
            selected = self.statuses[min(self.status_index, len(self.statuses) - 1)]
            self.status_index += 1
            return [deepcopy(selected)] if selected is not None else []
        if "admin:card-manifest" in sql:
            base = self.manifest_bases[min(self.manifest_index, len(self.manifest_bases) - 1)]
            self.manifest_index += 1
            for row in rows:
                row["generation"] = base + row["shard"]
        return rows


def runtime(client=None, *, clock=None, redis=None, **settings):
    return AnalyticsRuntime(
        get_settings().model_copy(update=settings),
        client=client or Client(),
        clock=clock or (lambda: NOW),
        redis=redis,
    )


def article_payload(**changes):
    return {
        "id": ID,
        "title": "C++ Einführung",
        "source_name": None,
        "source_id": None,
        "discovered_at": NOW.isoformat(),
        "stage": "analysis",
        "state": "waiting",
        "blockers": [],
        "next_check_at": None,
        "editorial_revision": 7,
        "jobs": {},
        **changes,
    }


@run_async
async def test_article_counts_and_pages_share_one_manifest_and_only_fetch_limited_payloads():
    client = Client(
        {
            "admin:article-counts": [{"stage": "analysis", "total": 98765}],
            "admin:article-page-keys": [{"kind": "article", "id": ID, "stage": "analysis"}],
            "admin:card-payloads": [
                {"kind": "article", "id": ID, "payload": json.dumps(article_payload())}
            ],
        }
    )
    result = await CurrentCards(client, now=NOW).article_board(
        q="C++ Einführung",
        normalized_q="c++ einführung",
        source_id=None,
        needs_attention=False,
        limit=1,
    )
    assert result.counts["analysis"] == 98765
    lane = next(column for column in result.columns if column.stage == "analysis")
    assert lane.next_cursor == 1 and lane.items[0].editorial_revision == 7
    assert len(client.queries) == 6
    bound = [params for sql, params in client.queries if "analytics_cards c" in sql]
    assert len(bound) == 3
    assert all(
        params["manifest_generations"] == bound[0]["manifest_generations"] for params in bound
    )
    assert len(bound[0]["manifest_shards"]) == 256
    assert bound[0]["needle"] == "c++ einführung"
    assert bound[-1]["ids"] == [ID]
    assert all("payload" not in sql for sql, _ in client.queries[:-1])
    for sql, _ in client.queries:
        if "analytics_cards c" in sql:
            assert "INNER JOIN" in sql
            assert "tuple(c.kind,c.shard,c.generation) IN arrayZip(" in sql
    assert "position<=" in client.queries[-2][0]
    assert "argMax" not in " ".join(sql for sql, _ in client.queries)


@pytest.mark.parametrize(
    "query,needle,literal", [("Введение C#", "введение c#", False), ("!!!", "!!!", True)]
)
@run_async
async def test_article_search_preserves_unicode_punctuation_and_per_field_boundaries(
    query,
    needle,
    literal,
):
    client = Client()
    await CurrentCards(client, now=NOW).article_board(
        q=query,
        normalized_q="" if literal else needle,
        source_id=None,
        needs_attention=False,
        limit=20,
    )
    sql, parameters = client.queries[3]
    assert parameters["needle"] == needle
    assert ("literal_search_text" in sql) == literal
    assert ("positionCaseInsensitiveUTF8" in sql) == literal
    if literal:
        assert "JSONExtract(c.literal_search_text,'Array(String)')" in sql


@run_async
async def test_grouped_board_reads_counts_and_narrow_source_groups_without_payloads():
    client = Client(
        {
            "admin:article-counts": [{"stage": "analysis", "total": 300}],
            "admin:article-source-groups": [
                {
                    "stage": "analysis",
                    "source_id": ID,
                    "source_name": "Source",
                    "total": 300,
                    "state_counts": {"running": 2, "blocked": 298},
                    "group_count": 4,
                }
            ],
        }
    )
    result = await CurrentCards(client, now=NOW).article_board(
        q=None,
        source_id=None,
        needs_attention=True,
        limit=2,
        group_by="source",
        stage="analysis",
    )
    assert result.columns[0].source_groups[0].state_counts == {"running": 2, "blocked": 298}
    assert result.columns[0].next_cursor == 2
    assert len(client.queries) == 5
    assert all("payload" not in sql for sql, _ in client.queries)


@pytest.mark.parametrize("reason", ["not-ready", "partial", "expired"])
@run_async
async def test_current_views_refuse_incomplete_or_expired_manifest_coverage(reason):
    client = Client()
    client.card_status["ready"] = reason != "not-ready"
    client.missing_shard = reason == "partial"
    client.manifest_age = timedelta(days=7) if reason == "expired" else timedelta()
    with pytest.raises(HTTPException) as caught:
        await CurrentCards(client, now=NOW).freeze(("source", "candidate"))
    assert caught.value.status_code == 503
    assert caught.value.detail["code"] == "analytics_preparing"
    assert not any("analytics_cards c" in sql for sql, _ in client.queries)


@pytest.mark.parametrize("after", ["reset", "missing"])
@run_async
async def test_reset_withdrawal_between_status_and_manifest_never_serves_an_empty_fresh_board(
    after,
):
    status = Client().card_status
    reset = {**status, "version": 2, "ready": 0}
    client = InterleavingClient([status, reset if after == "reset" else None], [900])
    with pytest.raises(HTTPException) as caught:
        await CurrentCards(client, now=NOW).article_board(
            q=None,
            source_id=None,
            needs_attention=False,
            limit=20,
        )
    assert caught.value.status_code == 503
    assert caught.value.detail["code"] == "analytics_preparing"
    assert client.status_index == 2
    assert not any("analytics_cards c" in sql for sql, _ in client.queries)


@pytest.mark.parametrize("transition", ["same-policy-reset", "heartbeat"])
@run_async
async def test_manifest_freeze_retries_changed_status_once_and_pins_all_reads_to_the_retry_vector(
    transition,
):
    current = Client().card_status
    previous = {**current, "generated_at": NOW - timedelta(seconds=30)}
    next_status = {**current, "version": 3}
    second_base = 100 if transition == "heartbeat" else 900
    client = InterleavingClient(
        [previous, next_status, next_status, next_status],
        [100, second_base],
        {
            "admin:article-counts": [{"stage": "analysis", "total": 1}],
            "admin:article-page-keys": [{"kind": "article", "id": ID, "stage": "analysis"}],
            "admin:card-payloads": [
                {"kind": "article", "id": ID, "payload": json.dumps(article_payload())}
            ],
        },
    )
    result = await CurrentCards(client, now=NOW).article_board(
        q=None,
        source_id=None,
        needs_attention=False,
        limit=20,
    )
    assert result.snapshot.status == "fresh" and result.snapshot.generated_at == NOW
    assert result.snapshot.policy_version == current["policy"]
    assert client.status_index == 4 and client.manifest_index == 2
    assert result.counts["analysis"] == 1
    assert next(column for column in result.columns if column.stage == "analysis").items[0].id == (
        uuid.UUID(ID)
    )
    bound = [parameters for sql, parameters in client.queries if "analytics_cards c" in sql]
    assert len(bound) == 3
    assert all(parameters["policy"] == current["policy"] for parameters in bound)
    assert all(
        parameters["manifest_generations"] == list(range(second_base, second_base + 256))
        for parameters in bound
    )


@pytest.mark.parametrize("policy", ["previous-serving", "unknown-derivation", "different-config"])
@run_async
async def test_current_cards_refuse_previous_or_unknown_policy_without_reading_payloads(policy):
    client = Client()
    if policy == "previous-serving":
        client.card_status["policy"] = "previous-policy"
    elif policy == "unknown-derivation":
        client.card_status.update(policy="previous-policy", target_policy="previous-policy")
    else:
        client.settings = get_settings().model_copy(update={"full_automation": True})
    with pytest.raises(HTTPException) as caught:
        await CurrentCards(client, now=NOW).freeze(("article",))
    assert caught.value.status_code == 503
    assert caught.value.detail["code"] == "analytics_preparing"
    assert len(client.queries) == 1
    assert "admin:card-status" in client.queries[0][0]


@run_async
async def test_continually_changing_status_stops_after_two_freeze_attempts():
    status = Client().card_status
    client = InterleavingClient(
        [{**status, "version": version} for version in range(1, 5)], [100, 900]
    )
    with pytest.raises(HTTPException) as caught:
        await CurrentCards(client, now=NOW).freeze(("source", "candidate"))
    assert caught.value.status_code == 503
    assert client.status_index == 4 and client.manifest_index == 2
    assert len(client.queries) == 6
    assert not any("analytics_cards c" in sql for sql, _ in client.queries)


@run_async
async def test_missing_page_payload_returns_preparing_instead_of_partial_success():
    client = Client(
        {"admin:article-page-keys": [{"kind": "article", "id": ID, "stage": "analysis"}]}
    )
    with pytest.raises(AnalyticsUnavailable, match="renewed"):
        await CurrentCards(client, now=NOW).article_board(
            q=None,
            source_id=None,
            needs_attention=False,
            limit=20,
        )


@run_async
async def test_source_private_search_only_passes_matching_ids_to_clickhouse():
    client = Client()
    query = SimpleNamespace(
        q="secret/private?token=credential",
        source_type="publisher",
        terminal_since=NOW - timedelta(days=7),
        needs_attention=True,
        limit=20,
    )
    result = await CurrentCards(client, now=NOW).source_board(
        query,
        matching_ids={"source": [uuid.UUID(ID)], "candidate": []},
    )
    assert len(result.columns) == 9
    assert len(client.queries) == 5
    for sql, parameters in client.queries:
        assert "secret" not in sql + repr(parameters)
    assert client.queries[3][1]["sources"] == [ID]
    assert len(client.queries[3][1]["manifest_shards"]) == 512
    assert "has_active_work=1" in client.queries[3][0]


@run_async
async def test_attention_uses_typed_current_reviews_failures_and_bounded_blocker_targets():
    client = Client(
        {
            "admin:operations-reviews": [
                {
                    "kind": "article",
                    "pending": 123,
                    "oldest": NOW - timedelta(days=3),
                    "failures": 0,
                },
                {"kind": "source", "pending": 4, "oldest": NOW, "failures": 2},
            ],
            "admin:operations-blocker-keys": [
                {
                    "code": "analysis_failed",
                    "total": 30,
                    "targets": [[NOW.isoformat(), ID, "article"]],
                }
            ],
            "admin:operations-targets": [
                {"kind": "article", "id": ID, "title": "Retry", "editorial_revision": 19}
            ],
        }
    )
    result = await CurrentCards(client, now=NOW).operations()
    assert [(item.kind, item.count) for item in result.data.reviews] == [
        ("articles", 123),
        ("sources", 4),
        ("topics", 0),
        ("relationships", 0),
    ]
    assert result.data.source_failures == 2
    blocker = next(item for item in result.data.blockers if item.code == "analysis_failed")
    assert blocker.count == 30 and blocker.targets[0].revision == 19
    assert client.queries[-1][1]["ids"] == [ID]
    assert len(client.queries[3][1]["manifest_shards"]) == 256 * len(CARD_KINDS)
    assert "groupArraySorted(5)" in client.queries[-2][0]
    assert all("payload" not in sql for sql, _ in client.queries)


@run_async
async def test_sixteen_readers_and_twelve_charts_coalesce_by_section_and_window():
    client = Client()
    reads = runtime(client)
    window = recent_window(30, NOW)
    results = await asyncio.gather(
        *[
            reads.section(group, current_window() if group == "operations" else window)
            for _ in range(16)
            for group in CHART_SECTIONS.values()
        ]
    )
    assert len(results) == 192
    assert client.status_reads == 6
    assert sum("admin:card-status" in sql for sql, _ in client.queries) == 2
    assert len(client.queries) <= 21
    before = len(client.queries)
    await reads.section("publishing", window)
    assert len(client.queries) == before
    await reads.close()
    assert client.closed


@run_async
async def test_queue_polling_coalesces_and_refreshes_without_recalculating_historical_charts():
    time = [NOW]
    client = Client({"overview:queues": [{"kind": "analysis", "queued": 9, "running": 2}]})
    reads = runtime(client, clock=lambda: time[0])
    window = recent_window(30, NOW)
    queue_group = CHART_SECTIONS["queue-pressure"]
    results = await asyncio.gather(*[reads.section(queue_group, window) for _ in range(16)])
    assert all(result.data.queues[0].queued == 9 for result in results)
    assert client.status_reads == 1
    assert len(client.queries) == 1 and "overview:queues" in client.queries[0][0]
    historical = await reads.section(CHART_SECTIONS["throughput"], window)
    assert historical.data.section == "processing" and len(historical.data.throughput) == 30
    assert client.status_reads == 2
    assert len(client.queries) == 3
    time[0] += timedelta(seconds=31)
    client.results["overview:queues"][0]["queued"] = 4
    refreshed = await reads.section(queue_group, window)
    assert refreshed.data.queues[0].queued == 4 and refreshed.snapshot.status == "fresh"
    assert client.status_reads == 3
    assert len(client.queries) == 4 and "overview:queues" in client.queries[-1][0]
    assert sum("overview:job-outcomes" in sql for sql, _ in client.queries) == 1
    assert sum("overview:throughput" in sql for sql, _ in client.queries) == 1
    time[0] += timedelta(seconds=31)
    client.failed = True
    with pytest.raises(HTTPException) as caught:
        await reads.section(queue_group, window)
    assert caught.value.status_code == 503
    assert caught.value.detail["code"] == "analytics_preparing"
    assert len(client.queries) == 4


@run_async
@pytest.mark.parametrize("failure", ["offline", "not-ready", "deadline"])
async def test_expired_cache_never_serves_old_data_when_clickhouse_cannot_refresh(failure):
    time = [NOW]
    client = Client()
    reads = runtime(client, clock=lambda: time[0])
    window = recent_window(30, NOW)
    first = await reads.section("publishing", window)
    time[0] += timedelta(seconds=31)
    if failure == "offline":
        client.failed = True
    elif failure == "not-ready":
        client.ready = False
    else:

        async def timeout():
            raise TimeoutError("query deadline reached")

        client.status = timeout
    assert first.snapshot.status == "fresh" and first.snapshot.generation == "1"
    with pytest.raises(HTTPException) as caught:
        await reads.section("publishing", window)
    assert caught.value.status_code == 503
    assert caught.value.detail["code"] == "analytics_preparing"
    assert caught.value.headers["Retry-After"] == "5"


@run_async
async def test_restart_reuses_only_unexpired_redis_results_and_never_extends_cache_lifetime():
    class Redis:
        def __init__(self):
            self.values = {}
            self.expirations = []

        def get(self, key):
            return self.values.get(key)

        def set(self, key, value, **kwargs):
            self.values[key] = value
            self.expirations.append(kwargs["ex"])

    redis = Redis()
    first = runtime(redis=lambda: redis, cache_enabled=True)
    window = recent_window(30, NOW)
    await first.section("publishing", window)
    assert redis.expirations == [30]
    envelope = json.loads(next(iter(redis.values.values())))
    assert set(envelope) == {"expires_at", "report"}
    assert datetime.fromisoformat(envelope["expires_at"]) == NOW + timedelta(seconds=30)
    assert ":v2:" in next(iter(redis.values))
    offline = Client()
    offline.failed = True
    second = runtime(
        offline, clock=lambda: NOW + timedelta(seconds=10), redis=lambda: redis, cache_enabled=True
    )
    assert (await second.section("publishing", window)).snapshot.status == "fresh"
    assert offline.status_reads == 0
    assert len(second.cache) == 1
    assert redis.expirations == [30]
    expired = runtime(
        offline, clock=lambda: NOW + timedelta(seconds=30), redis=lambda: redis, cache_enabled=True
    )
    with pytest.raises(HTTPException) as caught:
        await expired.section("publishing", window)
    assert caught.value.status_code == 503 and offline.status_reads == 1
    # A previous deployment's long-lived document must not become readable
    # through the current fresh-only cache namespace after a process restart.
    previous_key, previous_value = next(iter(redis.values.items()))
    previous = json.loads(previous_value)
    previous["fresh_until"] = previous["expires_at"]
    previous["expires_at"] = (NOW + timedelta(days=1)).isoformat()
    redis.values = {previous_key.replace(":v2:", ":v1:"): json.dumps(previous).encode()}
    upgraded = runtime(
        offline, clock=lambda: NOW + timedelta(seconds=31), redis=lambda: redis, cache_enabled=True
    )
    with pytest.raises(HTTPException) as caught:
        await upgraded.section("publishing", window)
    assert caught.value.status_code == 503


@run_async
async def test_optional_redis_failure_queries_clickhouse_without_using_cached_old_data():
    reads = runtime(redis=lambda: (_ for _ in ()).throw(ConnectionError()), cache_enabled=True)
    report = await reads.section("publishing", recent_window(30, NOW))
    assert report.snapshot.status == "fresh"
    assert reads.client.status_reads == 1


@run_async
async def test_admission_rejects_expired_cache_instead_of_serving_a_fallback(monkeypatch):
    monkeypatch.setattr(analytics_runtime, "MAX_INFLIGHT", 1)
    time = [NOW]
    client = Client()
    reads = runtime(client, clock=lambda: time[0])
    window = recent_window(30, NOW)
    await reads.section("publishing", window)
    time[0] += timedelta(seconds=31)
    started, release = asyncio.Event(), asyncio.Event()
    original = client.status

    async def blocked():
        started.set()
        await release.wait()
        return await original()

    client.status = blocked
    pending = asyncio.create_task(reads.section("audience", window))
    await started.wait()
    try:
        with pytest.raises(HTTPException) as caught:
            await reads.section("publishing", window)
        assert caught.value.status_code == 503
        assert caught.value.detail["code"] == "analytics_preparing"
        assert caught.value.headers["Retry-After"] == "5"
    finally:
        release.set()
        await pending


@run_async
async def test_disconnected_reader_does_not_cancel_shared_calculation():
    client = Client()
    reads = runtime(client)
    window = recent_window(30, NOW)
    disconnected = asyncio.create_task(reads.section("publishing", window))
    remaining = asyncio.create_task(reads.section("publishing", window))
    await asyncio.sleep(0)
    disconnected.cancel()
    with pytest.raises(asyncio.CancelledError):
        await disconnected
    assert (await remaining).data.section == "publishing"
    assert client.status_reads == 1


def test_overview_http_never_opens_postgres_and_keeps_all_twelve_chart_contracts():
    app = FastAPI()
    app.include_router(overview.router)
    reads = runtime()
    app.dependency_overrides[require_admin] = lambda: SimpleNamespace()
    app.dependency_overrides[get_analytics] = lambda: reads
    app.dependency_overrides[get_session] = lambda: pytest.fail("Overview opened PostgreSQL")
    with TestClient(app) as http:
        for chart in CHART_SECTIONS:
            response = http.get(f"/v1/admin/overview/charts/{chart}")
            assert response.status_code == 200, response.text
            assert response.json()["chart"] == chart
        assert http.get("/v1/admin/overview/charts/publication-rate?days=30").status_code == 422


def test_overview_http_expired_chart_returns_preparing_until_clickhouse_recovers(monkeypatch):
    app = FastAPI()
    app.include_router(overview.router)
    time = [NOW]
    monkeypatch.setattr(overview, "utcnow", lambda: time[0])
    client = Client()
    reads = runtime(client, clock=lambda: time[0])
    app.dependency_overrides[require_admin] = lambda: SimpleNamespace()
    app.dependency_overrides[get_analytics] = lambda: reads
    app.dependency_overrides[get_session] = lambda: pytest.fail("Overview opened PostgreSQL")
    path = "/v1/admin/overview/charts/publication-rate"
    with TestClient(app) as http:
        first = http.get(path)
        assert first.status_code == 200 and first.json()["snapshot"]["status"] == "fresh"
        client.failed = True
        time[0] += timedelta(seconds=30)
        failed = http.get(path)
        assert failed.status_code == 503
        assert failed.json()["detail"]["code"] == "analytics_preparing"
        assert failed.headers["retry-after"] == "5"
        assert "data" not in failed.json()
        client.failed = False
        recovered = http.get(path)
        assert recovered.status_code == 200
        assert recovered.json()["snapshot"]["status"] == "fresh"


def test_board_http_reads_only_bounded_current_metadata_after_clickhouse_page_selection():
    app = FastAPI()
    app.include_router(article_pipeline.router)
    app.include_router(source_pipeline.router)
    app.dependency_overrides[require_admin] = lambda: SimpleNamespace()
    app.dependency_overrides[get_analytics] = lambda: runtime()

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

        def execute(self, statement):
            pytest.fail(f"Empty analytical pages opened PostgreSQL: {statement}")

        def close(self):
            pass

    app.dependency_overrides[get_session_factory] = lambda: Session
    with TestClient(app) as http:
        assert http.get("/v1/admin/article-pipeline").status_code == 200
        assert http.get("/v1/admin/source-pipeline").status_code == 200
        assert http.get("/v1/admin/article-pipeline/analysis").status_code == 200
        assert http.get("/v1/admin/source-pipeline/columns/review").status_code == 200


def test_private_source_lookup_loads_only_ids_with_statement_deadline():
    statements = []

    class Session:
        def execute(self, statement):
            statements.append(statement)

        def scalars(self, statement):
            statements.append(statement)
            return [uuid.UUID(ID)]

        def close(self):
            pass

    result = source_pipeline._matching_sources(Session(), SimpleNamespace(q="private/path"))
    assert result == {"source": [uuid.UUID(ID)], "candidate": [uuid.UUID(ID)]}
    assert len(statements) == 3
    assert "set_config" in str(statements[0])
    for statement in statements[1:]:
        assert [column.name for column in statement.selected_columns] == ["id"]
        assert "JOIN" not in str(statement)


def test_article_search_normalization_uses_one_literal_without_reading_any_table():
    statements = []

    class Session:
        def scalar(self, statement):
            statements.append(statement)
            return "istanbul"

        def close(self):
            pass

    assert article_pipeline._normalized_search(Session(), "İstanbul") == "istanbul"
    assert article_pipeline._normalized_search(Session(), "") is None
    assert len(statements) == 1
    assert statements[0].get_final_froms() == []
    assert list(statements[0].compile().params.values())[0] == "İstanbul"


@run_async
async def test_article_uses_database_normalized_needle_without_renormalizing_it():
    client = Client()
    await CurrentCards(client, now=NOW).article_board(
        q="İstanbul",
        normalized_q="istanbul",
        source_id=None,
        needs_attention=False,
        limit=20,
    )
    assert client.queries[3][1]["needle"] == "istanbul"


def test_article_clock_hydration_reads_only_returned_ids_and_keeps_completed_cards_terminal():
    result = PipelineBoard(
        as_of=NOW,
        terminal_since=NOW - timedelta(days=7),
        counts={"analysis": 1},
        columns=[
            PipelineColumn(stage="analysis", total=1, next_cursor=None, items=[article_payload()])
        ],
    )
    statements = []

    class Session:
        def execute(self, statement):
            statements.append(statement)
            return SimpleNamespace(all=lambda: [(uuid.UUID(ID), NOW + timedelta(minutes=3))])

        def close(self):
            pass

    article_pipeline._hydrate_clocks(Session(), result)
    assert result.columns[0].items[0].next_check_at == NOW + timedelta(minutes=3)
    assert [column.name for column in statements[0].selected_columns] == [
        "id",
        "automation_next_check_at",
    ]
    assert list(statements[0].compile().params.values()) == [[uuid.UUID(ID)]]
    item = result.columns[0].items[0]
    item.state, item.next_check_at = "complete", None
    article_pipeline._hydrate_clocks(Session(), result)
    assert item.next_check_at is None


@run_async
async def test_one_failed_section_does_not_poison_independent_successful_sections():
    client = Client()
    reads = runtime(client)
    window = recent_window(30, NOW)
    await reads.section("audience", window)
    client.failed = True
    with pytest.raises(HTTPException):
        await reads.section("publishing", window)
    assert (await reads.section("audience", window)).data.section == "audience"
    client.failed = False
    assert (await reads.section("publishing", window)).data.section == "publishing"


@run_async
async def test_fresh_local_cache_evicts_lru_entries_and_refuses_oversized_documents():
    reads = runtime(cache_max_bytes=1024)
    for index in range(65):
        reads._remember(str(index), b"small")
    assert len(reads.cache) == 64
    assert "0" not in reads.cache
    reads._remember("large", b"x" * 1025)
    assert "large" not in reads.cache and reads.cache_bytes == 64 * 5
    await reads.close()


def test_authentication_failure_prevents_analytical_cache_or_queries():
    app = FastAPI()
    app.include_router(overview.router)
    reads = runtime()

    def denied():
        raise HTTPException(401, "Sign in required")

    app.dependency_overrides[require_admin] = denied
    app.dependency_overrides[get_analytics] = lambda: reads
    with TestClient(app) as http:
        assert http.get("/v1/admin/overview/charts/attention").status_code == 401
    assert not reads.cache and not reads.client.queries


def test_runtime_caps_configured_parallelism_at_two_and_reuses_the_same_client():
    reads = AnalyticsRuntime(
        get_settings().model_copy(update={"clickhouse_max_concurrent_queries": 8})
    )
    assert reads.client.settings.clickhouse_max_concurrent_queries == 2
    assert reads.cards().client is reads.client
    assert reads.cards().client is reads.cards().client


@pytest.mark.parametrize("kinds", [(), ("unknown",), ("article", "unknown")])
@run_async
async def test_unknown_card_populations_fail_before_any_clickhouse_read(kinds):
    client = Client()
    with pytest.raises(ValueError, match="Unknown current analytical kind"):
        await CurrentCards(client, now=NOW).freeze(kinds)
    assert client.queries == []


@pytest.mark.parametrize("elapsed,expected_status", [(90, "stale"), (-30, "catching_up")])
@run_async
async def test_catalog_progress_age_is_bounded_and_reported_without_changing_policy(
    elapsed, expected_status
):
    client = Client()
    client.card_status.update(
        catalog_revision='[["topics","before"]]',
        catalog_target_revision='[["topics","after"]]',
        catalog_progress_at=NOW - timedelta(seconds=elapsed),
    )
    frozen = await CurrentCards(client, now=NOW).freeze(("article",))
    assert frozen.policy == client.policy
    assert frozen.snapshot.status == expected_status
    assert frozen.snapshot.catalog_lag_seconds == max(0, elapsed)
    assert frozen.snapshot.lag_seconds == max(0, elapsed)


@pytest.mark.parametrize("group", ["unknown", ID])
@run_async
async def test_article_origin_and_expanded_group_filters_stay_parameterized(group):
    client = Client()
    await CurrentCards(client, now=NOW).article_board(
        q=None,
        source_id=uuid.UUID(ID),
        source_group=group,
        needs_attention=False,
        limit=20,
        stage="analysis",
        cursor=40,
    )
    for sql, parameters in client.queries[3:]:
        assert "has(c.source_ids,{source_id:UUID})" in sql
        assert parameters["source_id"] == ID and parameters["offset"] == 40
        assert ID not in sql
        if group == "unknown":
            assert "c.source_id IS NULL" in sql and "source_group" not in parameters
        else:
            assert "c.source_id={source_group:UUID}" in sql
            assert parameters["source_group"] == ID


@run_async
async def test_search_cannot_bypass_database_normalization_or_private_identity_lookup():
    client = Client()
    cards = CurrentCards(client, now=NOW)
    with pytest.raises(ValueError, match="database-normalized scalar needle"):
        await cards.article_board(q="İstanbul", source_id=None, needs_attention=False, limit=20)
    query = SimpleNamespace(q="private-feed-token", terminal_since=NOW, limit=20)
    with pytest.raises(ValueError, match="bounded source lookup"):
        await cards.source_board(query)
    assert len(client.queries) == 6
    assert all(
        "page-keys" not in sql and "private-feed-token" not in sql for sql, _ in client.queries
    )


@run_async
async def test_unknown_blocker_codes_cannot_be_silently_excluded_from_current_attention():
    client = Client(
        {"admin:operations-blocker-keys": [{"code": "future-policy", "total": 1, "targets": []}]}
    )
    with pytest.raises(AnalyticsUnavailable, match="Current blocker policy"):
        await CurrentCards(client, now=NOW).operations()
    assert not any("operations-targets" in sql for sql, _ in client.queries)


@run_async
async def test_runtime_shutdown_cancels_shared_work_and_closes_cache_even_if_driver_close_fails(
    monkeypatch,
):
    events, close_threads = [], []
    loop_thread = threading.get_ident()
    cache = SimpleNamespace(
        get=lambda _key: None,
        close=lambda: (events.append("cache"), close_threads.append(threading.get_ident())),
    )
    monkeypatch.setattr(analytics_runtime, "create_redis", lambda *_args, **_kwargs: cache)
    client = Client()

    async def close_client():
        events.append("client")
        raise RuntimeError("client close failed")

    client.close = close_client
    reads = runtime(client, cache_enabled=True)
    started = asyncio.Event()

    async def blocked():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            events.append("cancelled")

    client.status = blocked
    pending = asyncio.create_task(reads.section("publishing", recent_window(30, NOW)))
    await started.wait()
    with pytest.raises(RuntimeError, match="client close failed"):
        await reads.close()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert pending.cancelled() and not reads.inflight
    assert events == ["cancelled", "client", "cache"]
    assert close_threads and close_threads[0] != loop_thread


@pytest.mark.parametrize("method", ["article_board", "source_board"])
@run_async
async def test_board_deadlines_fail_closed_for_both_current_views(monkeypatch, method):
    reads = runtime()
    timeout = asyncio.timeout
    monkeypatch.setattr(analytics_runtime.asyncio, "timeout", lambda _seconds: timeout(0.01))

    async def blocked(*_args, **_kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(
        reads, "cards", lambda: SimpleNamespace(article_board=blocked, source_board=blocked)
    )
    with pytest.raises(AnalyticsUnavailable, match="Pipeline request deadline reached"):
        if method == "article_board":
            await reads.article_board(limit=20)
        else:
            await reads.source_board(SimpleNamespace(q="private token"), cursor=40)


@pytest.mark.parametrize("cached", [b"not-json", b"{}", b'{"expires_at":"invalid"}'])
@run_async
async def test_malformed_fresh_cache_entries_are_replaced_by_current_clickhouse_results(cached):
    writes = []
    cache = SimpleNamespace(
        get=lambda _key: cached,
        set=lambda key, value, **kwargs: writes.append((key, value, kwargs)),
    )
    reads = runtime(redis=lambda: cache, cache_enabled=True)
    result = await reads.section("publishing", recent_window(30, NOW))
    assert result.data.section == "publishing" and result.snapshot.status == "fresh"
    assert reads.client.status_reads == 1 and len(writes) == 1
    envelope = json.loads(writes[0][1])
    assert envelope["report"]["snapshot"]["status"] == "fresh"
    assert writes[0][2] == {"ex": 30}


@run_async
async def test_cache_observation_ages_lag_without_mutating_shared_or_optional_metadata():
    time = [NOW]
    client = Client()
    status = client.status

    async def lagging():
        return (await status()) | {"lag_seconds": 55}

    client.status = lagging
    reads = runtime(client, clock=lambda: time[0])
    window = recent_window(30, NOW)
    first = await reads.section("publishing", window)
    time[0] += timedelta(seconds=10)
    aged = await reads.section("publishing", window)
    assert first.snapshot.status == "fresh" and first.snapshot.lag_seconds == 55
    assert aged.snapshot.status == "stale" and aged.snapshot.lag_seconds == 65
    assert client.status_reads == 1
    without_metadata = first.model_copy(update={"snapshot": None})
    observed = reads._observed(without_metadata)
    assert observed is not without_metadata and observed.snapshot is None
    assert observed.data == without_metadata.data


@run_async
async def test_analytical_validation_errors_are_not_disguised_as_preparing(monkeypatch):
    reads = runtime()
    failure = HTTPException(422, "Unsupported analytical input")

    async def invalid(*_args):
        raise failure

    monkeypatch.setattr(reads, "_calculate", invalid)
    with pytest.raises(HTTPException) as caught:
        await reads.section("operations", current_window())
    assert caught.value is failure
    assert not reads.cache


@run_async
async def test_large_report_is_returned_without_retaining_or_publishing_an_oversized_cache_entry():
    writes = []
    cache = SimpleNamespace(
        get=lambda _key: None,
        set=lambda *args, **kwargs: writes.append((args, kwargs)),
    )
    reads = runtime(redis=lambda: cache, cache_enabled=True, cache_max_bytes=1)
    window = recent_window(30, NOW)
    first = await reads.section("publishing", window)
    second = await reads.section("publishing", window)
    assert first.data == second.data
    assert reads.client.status_reads == 2 and not reads.cache and not writes


def test_http_analytics_dependency_reuses_the_application_runtime_and_fresh_calculation():
    app = FastAPI()
    app.include_router(overview.router)
    reads = runtime()
    app.state.analytics = reads
    app.dependency_overrides[require_admin] = lambda: SimpleNamespace()
    with TestClient(app) as http:
        for _ in range(2):
            response = http.get("/v1/admin/overview/charts/queue-pressure")
            assert response.status_code == 200
            assert response.json()["chart"] == "queue-pressure"
    assert reads.client.status_reads == 1
    assert len(reads.client.queries) == 1 and "overview:queues" in reads.client.queries[0][0]
