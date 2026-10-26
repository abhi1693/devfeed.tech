"""Current boards and independent chart reads use bounded native analytical serving."""

import asyncio
import json
import uuid
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from functools import wraps
from types import SimpleNamespace

import pytest
from devfeed_admin_api import article_pipeline, overview, source_pipeline
from devfeed_admin_api.analytics_current import CARD_KINDS, CurrentCards
from devfeed_admin_api.analytics_runtime import AnalyticsRuntime, get_analytics
from devfeed_admin_api.auth import require_admin
from devfeed_admin_api.dependencies import get_session
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
        return {"ready": self.ready, "capture_started_at": NOW, "lag_seconds": 0}

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
    assert caught.value.detail["code"] == "projection_initializing"
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
    assert caught.value.detail["code"] == "projection_initializing"
    assert client.status_index == 2
    assert not any("analytics_cards c" in sql for sql, _ in client.queries)


@pytest.mark.parametrize("transition", ["same-policy-reset", "new-policy", "heartbeat"])
@run_async
async def test_manifest_freeze_retries_changed_status_once_and_pins_all_reads_to_the_retry_vector(
    transition,
):
    current = Client().card_status
    previous = {**current, "generated_at": NOW - timedelta(seconds=30)}
    if transition == "new-policy":
        previous["policy"] = "previous-policy"
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
    assert client.status_reads == 5
    assert sum("admin:card-status" in sql for sql, _ in client.queries) == 2
    assert len(client.queries) <= 21
    before = len(client.queries)
    await reads.section("publishing", window)
    assert len(client.queries) == before
    await reads.close()
    assert client.closed


@run_async
async def test_last_good_cache_marks_outage_stale_and_expiry_returns_retryable_preparing():
    time = [NOW]
    client = Client()
    reads = runtime(client, clock=lambda: time[0])
    window = recent_window(30, NOW)
    first = await reads.section("publishing", window)
    time[0] += timedelta(seconds=31)
    client.failed = True
    stale = await reads.section("publishing", window)
    assert first.snapshot.status == "fresh"
    assert stale.snapshot.status == "stale" and stale.snapshot.lag_seconds == 31
    time[0] += timedelta(days=1)
    with pytest.raises(HTTPException) as caught:
        await reads.section("publishing", window)
    assert caught.value.status_code == 503
    assert caught.value.detail["code"] == "report_refreshing"
    assert caught.value.headers["Retry-After"] == "5"


@run_async
async def test_redis_last_good_survives_process_restart_and_redis_outage_is_optional():
    class Redis:
        def __init__(self):
            self.values = {}

        def get(self, key):
            return self.values.get(key)

        def set(self, key, value, **kwargs):
            self.values[key] = value

    redis = Redis()
    first = runtime(redis=lambda: redis, cache_enabled=True)
    window = recent_window(30, NOW)
    await first.section("publishing", window)
    offline = Client()
    offline.failed = True
    second = runtime(
        offline, clock=lambda: NOW + timedelta(seconds=31), redis=lambda: redis, cache_enabled=True
    )
    assert (await second.section("publishing", window)).snapshot.status == "stale"
    assert len(second.cache) == 1
    third = runtime(redis=lambda: (_ for _ in ()).throw(ConnectionError()), cache_enabled=True)
    assert (await third.section("publishing", window)).snapshot.status == "fresh"


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


def test_board_http_reads_only_bounded_current_metadata_after_clickhouse_page_selection():
    app = FastAPI()
    app.include_router(article_pipeline.router)
    app.include_router(source_pipeline.router)
    app.dependency_overrides[require_admin] = lambda: SimpleNamespace()
    app.dependency_overrides[get_analytics] = lambda: runtime()

    class Session:
        def execute(self, statement):
            pytest.fail(f"Empty analytical pages opened PostgreSQL: {statement}")

        def close(self):
            pass

    app.dependency_overrides[get_session] = Session
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
async def test_local_last_good_cache_evicts_lru_entries_and_refuses_oversized_documents():
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
