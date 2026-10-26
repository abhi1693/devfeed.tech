"""Native current publication preserves replay, fencing and bounded coverage."""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from devfeed_core import analytics_cards as cards

NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)
ENTITY = uuid.UUID("12000000-0000-4000-8000-000000000001")
STATE = {
    "card_target_policy": "policy",
    "card_serving_policy": "policy",
    "card_bootstrap_complete": True,
    "card_bootstrap_barrier": True,
    "card_catalog_revision": [["topics", "revision"]],
    "card_catalog_target_revision": [["topics", "revision"]],
    "card_catalog_progress_at": None,
}


class Cursor:
    def __init__(self, value):
        self.value = value

    async def fetchone(self):
        return self.value[0] if isinstance(self.value, list) and self.value else self.value

    async def fetchall(self):
        return self.value or []


class Connection:
    def __init__(self, selected=None):
        self.selected = selected or (lambda statement, parameters: None)
        self.calls = []

    @asynccontextmanager
    async def transaction(self):
        yield

    async def execute(self, statement, parameters=None):
        self.calls.append((str(statement), parameters))
        return Cursor(self.selected(str(statement), parameters))


def publisher():
    client = SimpleNamespace(execute=AsyncMock(), insert=AsyncMock(), query=AsyncMock())
    return cards.CardPublisher(
        client, SimpleNamespace(database_url="postgresql+psycopg://unit@database.invalid/unit_test")
    ), client


def observation(**changes):
    return {
        "entity_id": ENTITY,
        "stage": "analysis",
        "state": "waiting",
        "created_at": NOW,
        "activity_at": NOW,
        "evaluated_at": NOW,
        "title": "Public title",
        "editorial_revision": 2,
        "payload": {"id": str(ENTITY), "state": "waiting", "next_check_at": NOW.isoformat()},
        **changes,
    }


def dirty_connection():
    return Connection(
        lambda statement, parameters: (
            [dict(kind="article", entity_id=ENTITY, generation=2)]
            if statement.startswith("WITH oldest")
            else None
        )
    )


def test_semantic_hash_ignores_observation_clock_but_tracks_business_state_and_deletion():
    first = cards.normalize_card(observation(), "policy", "article", ENTITY, 1, NOW)
    duplicate = cards.normalize_card(
        observation(
            evaluated_at=NOW + timedelta(hours=1),
            payload={**json.loads(first["payload"]), "next_check_at": None},
        ),
        "policy",
        "article",
        ENTITY,
        2,
        NOW,
    )
    changed = cards.normalize_card(
        observation(editorial_revision=3), "policy", "article", ENTITY, 3, NOW
    )
    deleted = cards.normalize_card(None, "policy", "article", ENTITY, 4, NOW)
    repeated_delete = cards.normalize_card(
        None, "policy", "article", ENTITY, 5, NOW + timedelta(days=1)
    )

    assert first["semantic_hash"] == duplicate["semantic_hash"]
    assert first["semantic_hash"] != changed["semantic_hash"]
    assert deleted["deleted"] == 1 and deleted["semantic_hash"] == repeated_delete["semantic_hash"]
    assert deleted["version"] == 4 << 64 and first["shard"] == 0x12
    assert set(first) == {name for name, _ in cards.COLUMNS}


def test_current_ddl_preserves_durable_canonical_data_and_expires_only_serving_snapshots():
    canonical, serving, manifests, resets, status = cards.card_initialization_sql()
    assert "ReplacingMergeTree(version)" in canonical and "TTL" not in canonical
    assert "TTL built_at+INTERVAL 7 DAY DELETE" in serving
    assert "ORDER BY(policy,kind,shard,generation,entity_id)" in serving
    assert "ReplacingMergeTree(generation)" in manifests
    assert "UInt128" in resets and "catalog_target_revision String" in status


def test_event_routing_is_replay_order_independent_and_keeps_both_old_and_new_owners():
    old, new, old_source, new_source, candidate, research = [uuid.uuid4() for _ in range(6)]
    events = [
        dict(
            id=20,
            source_table="article_analysis_jobs",
            operation="I",
            record={"article_id": str(new)},
            before_record={"article_id": str(old)},
        ),
        dict(
            id=2,
            source_table="source_candidates",
            operation="I",
            record={"id": str(candidate), "source_id": str(new_source)},
            before_record={"id": str(candidate), "source_id": str(old_source)},
        ),
        dict(
            id=30,
            source_table="research_verification_jobs",
            operation="D",
            record={"id": str(research)},
        ),
    ]
    dirty, fanout, jobs, reset = cards.event_invalidations(events)
    assert {
        ("article", old),
        ("article", new),
        ("source", old_source),
        ("source", new_source),
        ("candidate", candidate),
    } <= dirty
    assert fanout == {("source", old_source), ("source", new_source)}
    assert jobs == {research} and not reset
    assert cards.event_invalidations(events + list(reversed(events))) == (
        dirty,
        fanout,
        jobs,
        reset,
    )
    assert cards.event_invalidations([dict(source_table="articles", operation="T")])[3]
    for table in ("user_accounts", "inference_calls", "overview_daily", "user_tags"):
        assert not cards.event_invalidations([dict(source_table=table, operation="T")])[3]


def test_source_polling_changes_refresh_source_card_without_article_population_fanout():
    source = uuid.uuid4()
    before = dict(
        id=str(source),
        name="Publisher",
        approval_status="approved",
        enabled=True,
        consecutive_failures=0,
    )
    after = {**before, "consecutive_failures": 1}
    event = dict(source_table="sources", operation="I", before_record=before, record=after)
    dirty, fanout, _, _ = cards.event_invalidations([event])
    assert dirty == {("source", source)} and fanout == set()
    for field, value in (("name", "Renamed"), ("approval_status", "rejected"), ("enabled", False)):
        event["record"] = {**after, field: value}
        assert cards.event_invalidations([event])[1] == {("source", source)}
    event.update(operation="D", record=before)
    assert cards.event_invalidations([event])[1] == {("source", source)}


def test_changed_card_is_canonical_then_snapshotted_then_manifested_before_ack():
    async def verify():
        subject, client = publisher()
        connection = dirty_connection()
        subject._build = Mock(return_value=[observation()])
        client.query.side_effect = [[], [{"shard": 18, "count": 1}]]
        order = []
        client.insert.side_effect = lambda table, rows: order.append(table)
        client.execute.side_effect = lambda *args: order.append("snapshot")
        original_execute = connection.execute

        async def execute(statement, parameters=None):
            if str(statement).startswith("DELETE"):
                order.append("ack")
            return await original_execute(statement, parameters)

        connection.execute = execute
        assert await subject._refresh_one(connection, 3, STATE, fence=AsyncMock())
        assert order == ["analytics_card_canonical", "snapshot", "analytics_card_manifest", "ack"]
        assert subject._build.call_args.args[1] == [ENTITY]
        selected = connection.calls[0]
        assert selected[1] == (8, 200) and "get_byte(uuid_send(entity_id),0)" in selected[0]
        assert "r.generation<=done.generation" in connection.calls[-1][0]
        assert "max_block_size=1024" in client.execute.call_args.args[0]

    asyncio.run(verify())


def test_unchanged_card_acks_without_churning_payload_or_page_generation():
    async def verify():
        subject, client = publisher()
        connection = dirty_connection()
        row = cards.normalize_card(observation(), "policy", "article", ENTITY, 1, NOW)
        subject._build = Mock(
            return_value=[
                observation(payload={**json.loads(row["payload"]), "next_check_at": None})
            ]
        )
        client.query.side_effect = [[row], [{"shard": 18, "generation": 1}]]

        assert await subject._refresh_one(connection, 3, STATE, fence=AsyncMock())

        client.insert.assert_not_called()
        client.execute.assert_not_called()
        assert connection.calls[-1][0].startswith("DELETE FROM analytics_card_refresh")

    asyncio.run(verify())


def test_mixed_shards_batch_changes_and_replayed_canonical_before_one_fenced_ack():
    async def verify():
        subject, client = publisher()
        other = uuid.UUID("13000000-0000-4000-8000-000000000002")
        connection = Connection(
            lambda statement, parameters: (
                [
                    dict(kind="article", entity_id=ENTITY, generation=2),
                    dict(kind="article", entity_id=other, generation=7),
                ]
                if statement.startswith("WITH oldest")
                else None
            )
        )
        subject._build = Mock(
            return_value=[observation(), observation(entity_id=other, payload={"id": str(other)})]
        )
        previous = cards.normalize_card(observation(), "policy", "article", ENTITY, 2, NOW)
        client.query.side_effect = [
            [previous],
            [{"shard": 18, "generation": 1}],
            [{"shard": 18, "count": 5}, {"shard": 19, "count": 4}],
        ]

        assert await subject._refresh_one(connection, 8, STATE, fence=AsyncMock())

        # One durable canonical insert and one complete publication for both the
        # newly changed shard and the orphan canonical shard from a failed flush.
        canonical, manifest = client.insert.call_args_list
        assert canonical.args[0] == "analytics_card_canonical"
        assert [item["entity_id"] for item in canonical.args[1]] == [str(other)]
        client.execute.assert_awaited_once()
        assert client.execute.call_args.args[1]["shards"] == [18, 19]
        assert manifest.args[0] == "analytics_card_manifest"
        assert [(item["shard"], item["item_count"]) for item in manifest.args[1]] == [
            (18, 5),
            (19, 4),
        ]
        acknowledgements = [call for call in connection.calls if call[0].startswith("DELETE")]
        assert len(acknowledgements) == 1
        assert acknowledgements[0][1] == ([ENTITY, other], [2, 7], "article")

    asyncio.run(verify())


def test_empty_shard_has_a_zero_manifest_and_failed_batch_keeps_every_dirty_identity():
    async def verify():
        subject, client = publisher()
        connection = Connection()
        client.query.return_value = [{"shard": 19, "count": 4}]
        await subject._publish_shards(
            connection, "policy", "article", [18, 19], 8, fence=AsyncMock()
        )
        assert [
            (item["shard"], item["item_count"]) for item in client.insert.call_args.args[1]
        ] == [
            (18, 0),
            (19, 4),
        ]
        with pytest.raises(ValueError, match="Too many"):
            await subject._publish_shards(
                connection, "policy", "article", range(9), 8, fence=AsyncMock()
            )

    asyncio.run(verify())


def test_canonical_success_manifest_failure_replay_publishes_despite_equal_hash():
    async def verify():
        subject, client = publisher()
        connection = dirty_connection()
        row = cards.normalize_card(observation(), "policy", "article", ENTITY, 2, NOW)
        subject._build = Mock(return_value=[observation()])
        client.query.side_effect = [
            [row],
            [{"shard": 18, "generation": 1}],
            [{"shard": 18, "count": 1}],
        ]

        assert await subject._refresh_one(connection, 3, STATE, fence=AsyncMock())

        assert [call.args[0] for call in client.insert.call_args_list] == [
            "analytics_card_manifest"
        ]
        client.execute.assert_awaited_once()
        assert client.insert.call_args.args[1][0]["generation"] == 3
        assert connection.calls[-1][0].startswith("DELETE FROM analytics_card_refresh")

    asyncio.run(verify())


def test_failed_manifest_keeps_dirty_identity_for_replay():
    async def verify():
        subject, client = publisher()
        connection = dirty_connection()
        subject._build = Mock(return_value=[])
        client.query.side_effect = [[], []]

        async def insert(table, rows):
            if table == "analytics_card_manifest":
                raise RuntimeError("manifest failed")

        client.insert.side_effect = insert
        with pytest.raises(RuntimeError, match="manifest failed"):
            await subject._refresh_one(connection, 3, STATE, fence=AsyncMock())

        tombstone = client.insert.call_args_list[0].args[1][0]
        assert tombstone["deleted"] == 1 and tombstone["entity_id"] == str(ENTITY)
        assert not any(
            "DELETE FROM analytics_card_refresh" in query for query, _ in connection.calls
        )

    asyncio.run(verify())


def test_superseded_lease_cannot_publish_manifest_or_ack_current_work():
    async def verify():
        subject, client = publisher()
        connection = dirty_connection()
        subject._build = Mock(return_value=[observation()])
        client.query.return_value = []
        fence = AsyncMock(side_effect=RuntimeError("superseded"))
        with pytest.raises(RuntimeError, match="superseded"):
            await subject._refresh_one(connection, 3, STATE, fence=fence)
        client.insert.assert_not_called()
        assert not any(
            "DELETE FROM analytics_card_refresh" in query for query, _ in connection.calls
        )

        client.query.return_value = [{"shard": 18, "count": 1}]
        with pytest.raises(RuntimeError, match="superseded"):
            await subject._publish_shard(connection, "policy", "article", 18, 3, fence=fence)
        client.execute.assert_awaited_once()  # An orphan immutable shard is never visible.
        client.insert.assert_not_called()

    asyncio.run(verify())


def test_source_fanout_advances_bounded_keyset_without_whole_population_hydration():
    async def verify():
        subject, client = publisher()
        source, cursor = uuid.uuid4(), uuid.uuid4()
        selected = dict(
            kind="source",
            entity_id=source,
            cursor=cursor,
            generation=2,
            initial_pending=True,
            requested_at=NOW,
            available_at=NOW,
        )
        articles = [{"id": uuid.UUID(int=number)} for number in range(1, 1001)]
        connection = Connection(
            lambda statement, parameters: (
                selected
                if "FROM analytics_card_fanout" in statement and statement.startswith("SELECT")
                else articles
                if statement.startswith("SELECT DISTINCT")
                else None
            )
        )
        subject._mark = AsyncMock()

        assert await subject._fanout_one(connection, 3, STATE, fence=AsyncMock())

        select_query = next(
            call for call in connection.calls if call[0].startswith("SELECT DISTINCT")
        )
        assert "article_id>%s::uuid" in select_query[0]
        assert select_query[1] == (source, cursor, cursor, 1000)
        assert len(subject._mark.call_args.args[1]) == 1000
        assert subject._mark.call_args.args[-1] is True
        assert connection.calls[-1][1][0] == articles[-1]["id"]
        assert subject._factory is None
        client.query.assert_not_called()

    asyncio.run(verify())


def test_catalog_cohort_finishes_before_restart_and_later_pass_cannot_block_initial_ready():
    async def verify():
        subject, client = publisher()
        cursor = uuid.uuid4()
        selected = dict(
            kind="catalog",
            entity_id=cards.ZERO_UUID,
            cursor=cursor,
            generation=2,
            initial_pending=True,
            requested_at=NOW + timedelta(seconds=1),
            available_at=NOW,
        )
        connection = Connection(
            lambda statement, parameters: (
                selected if "FROM analytics_card_fanout" in statement else None
            )
        )
        client.query.return_value = [{"id": str(ENTITY)}]
        subject._mark = AsyncMock()

        assert await subject._fanout_one(connection, 3, STATE, fence=AsyncMock())

        assert client.query.call_args.args[1]["cursor"] == cursor
        assert client.query.call_args.args[1]["limit"] == 1000
        assert "catalog_sensitive=1" in client.query.call_args.args[0]
        restart = [query for query, _ in connection.calls if "cursor=NULL" in query]
        assert restart and "initial_pending=false" in restart[0]
        assert subject._mark.call_args.args[-1] is True

    asyncio.run(verify())


def test_backfill_coverage_freezes_initial_traffic_then_waits_only_initial_cohorts():
    async def verify():
        subject, _ = publisher()
        pending = dict(covered=True, fanout=True, dirty=True, catalog=True, any_dirty=True)
        connection = Connection(
            lambda statement, parameters: pending if statement.startswith("SELECT") else None
        )
        await subject._coverage(connection, fence=AsyncMock())
        assert any("card_bootstrap_barrier=true" in query for query, _ in connection.calls)
        assert not any("card_bootstrap_complete=true" in query for query, _ in connection.calls)

        pending.update(fanout=False, dirty=False)
        await subject._coverage(connection, fence=AsyncMock())
        assert any("card_bootstrap_complete=true" in query for query, _ in connection.calls)
        assert pending["any_dirty"]  # Continuous noninitial traffic does not block readiness.

    asyncio.run(verify())


def test_due_job_timers_enqueue_bounded_identity_work_without_clock_hydration():
    async def verify():
        subject, client = publisher()
        connection = Connection()
        client.query.side_effect = [[], [{"kind": "source", "entity_id": str(ENTITY)}]]
        subject._mark = AsyncMock()

        assert await subject._timers_one(connection, 3, STATE, fence=AsyncMock())

        assert subject._mark.call_args.args[1] == {("source", ENTITY)}
        for call in client.query.call_args_list:
            assert call.args[1]["limit"] == 200
            assert "next_transition_at<=now64(6)" in call.args[0]
            assert "next_check_at" not in call.args[0]

    asyncio.run(verify())


def test_quiet_shard_renewal_rebuilds_only_one_immutable_generation():
    async def verify():
        subject, client = publisher()
        client.query.return_value = [{"policy": "policy", "kind": "article", "shard": 18}]
        subject._publish_shard = AsyncMock()
        connection, fence = Connection(), AsyncMock()

        assert await subject._renew_one(connection, 9, STATE, fence=fence)

        assert "INTERVAL 1 DAY" in client.query.call_args.args[0]
        subject._publish_shard.assert_awaited_once_with(
            connection, "policy", "article", 18, 9, fence=fence
        )

    asyncio.run(verify())


def test_same_policy_reset_withdraws_serving_before_markers_and_preserves_historical_metadata():
    async def verify():
        subject, client = publisher()
        connection = Connection(
            lambda statement, parameters: (
                STATE
                if "card_target_policy" in statement and statement.startswith("SELECT")
                else [{"name": "topics", "revision": ENTITY}]
                if "FROM catalog_revisions" in statement
                else None
            )
        )

        await subject._reset(connection, "policy", 5, fence=AsyncMock())

        tables = [call.args[0] for call in client.insert.call_args_list]
        assert tables == [
            "analytics_card_status",
            "analytics_card_resets",
            "analytics_card_manifest",
        ]
        assert client.insert.call_args_list[0].args[1][0]["ready"] == 0
        assert len(client.insert.call_args_list[1].args[1]) == 5
        assert len(client.insert.call_args_list[2].args[1]) == 5 * 256
        assert all(row["version"] == 5 << 64 for row in client.insert.call_args_list[1].args[1])
        mutations = [
            query for query, _ in connection.calls if query.startswith(("DELETE", "UPDATE"))
        ]
        assert not any(
            "analytics_bootstrap_cursors" in query
            or "bootstrap_complete=" in query.replace("card_bootstrap_complete=", "")
            for query in mutations
        )
        assert {query for query in mutations if query.startswith("DELETE")} == {
            "DELETE FROM analytics_card_refresh",
            "DELETE FROM analytics_card_fanout",
            "DELETE FROM analytics_card_backfills",
        }

    asyncio.run(verify())


def test_reset_failure_does_not_advance_pg_coverage_and_can_retry():
    async def verify():
        subject, client = publisher()
        connection = Connection(
            lambda statement, parameters: STATE if statement.startswith("SELECT") else None
        )
        client.insert.side_effect = [None, RuntimeError("reset failed")]
        with pytest.raises(RuntimeError, match="reset failed"):
            await subject._reset(connection, "policy", 5, fence=AsyncMock())
        assert not any(query.startswith("DELETE") for query, _ in connection.calls)
        assert client.insert.call_args_list[0].args[1][0]["ready"] == 0

    asyncio.run(verify())


def test_publisher_is_lazy_and_rejects_large_builder_batches_before_engine_creation():
    subject, _ = publisher()
    assert subject._engine is None
    with pytest.raises(ValueError, match="at most 200"):
        subject._build("article", [uuid.uuid4() for _ in range(201)], "policy", NOW)
    assert subject._engine is None
    subject.close()


def test_scan_backpressure_checks_only_a_fixed_number_of_narrow_pending_ids():
    async def verify():
        subject, client = publisher()
        connection = Connection(lambda statement, parameters: {"saturated": True})
        fence = AsyncMock()
        assert not await subject._backfill_one(connection, 3, STATE, fence=fence)
        assert not await subject._fanout_one(connection, 3, STATE, fence=fence)
        assert len(connection.calls) == 2
        assert all(parameters == (2000,) for _, parameters in connection.calls)
        assert all("OFFSET %s LIMIT 1" in query for query, _ in connection.calls)
        fence.assert_not_called()
        client.query.assert_not_called()

    asyncio.run(verify())


def test_backfill_kind_rotation_does_not_starve_smaller_source_or_proposal_kinds():
    async def verify():
        subject, _ = publisher()
        subject._mark = AsyncMock()

        def selected(statement, parameters):
            if "SELECT kind,cursor FROM analytics_card_backfills" in statement:
                return {"kind": cards.KINDS[subject._backfill_kind], "cursor": None}
            return None

        connection = Connection(selected)
        for index in range(5):
            assert subject._backfill_kind == index
            assert await subject._backfill_one(connection, 3, STATE, fence=AsyncMock())
        assert subject._backfill_kind == 0
        assert [
            parameters[-1]
            for query, parameters in connection.calls
            if query.startswith("UPDATE analytics_card_backfills")
        ] == list(cards.KINDS)

    asyncio.run(verify())


def test_retained_catalog_snapshots_validate_pg_revision_on_every_batch(monkeypatch):
    from devfeed_core import catalog_cache

    session = Mock()
    session.get_bind.return_value = object()
    revision = ["topics", "first"]
    session.execute.side_effect = lambda statement: [tuple(revision)]
    monkeypatch.setattr(catalog_cache, "get_settings", lambda: SimpleNamespace(cache_enabled=False))
    retained = {}
    first, second = {"topics": [{"name": "First"}]}, {"topics": [{"name": "Second"}]}
    load = Mock(side_effect=[first, second])
    with catalog_cache.reuse_snapshots(retained=retained):
        assert catalog_cache.snapshot(session, ("topics",), load) is first
    with catalog_cache.reuse_snapshots(retained=retained):
        assert catalog_cache.snapshot(session, ("topics",), load) is first
    assert load.call_count == 1
    revision[1] = "second"
    with catalog_cache.reuse_snapshots(retained=retained):
        assert catalog_cache.snapshot(session, ("topics",), load) is second
    assert load.call_count == 2 and len(retained) == 1


def test_catalog_invalidation_releases_old_ranking_before_loading_replacement(monkeypatch):
    from devfeed_core import catalog_cache

    session = Mock()
    session.get_bind.return_value = object()
    revision = ["topics", "old"]
    session.execute.side_effect = lambda statement: [tuple(revision)]
    monkeypatch.setattr(catalog_cache, "get_settings", lambda: SimpleNamespace(cache_enabled=False))
    retained, ranking, order = {}, [object()], []

    def invalidate():
        assert retained == {}
        ranking.clear()
        order.append("released")

    with catalog_cache.reuse_snapshots(retained=retained, on_invalidate=invalidate):
        catalog_cache.snapshot(session, ("topics",), lambda: {"topics": []})
    revision[1] = "new"

    def replacement():
        assert ranking == [] and retained == {}
        order.append("loaded")
        return {"topics": []}

    with catalog_cache.reuse_snapshots(retained=retained, on_invalidate=invalidate):
        catalog_cache.snapshot(session, ("topics",), replacement)
    assert order == ["released", "loaded"]


def test_retained_ranking_is_bounded_and_replaced_when_catalog_snapshot_changes():
    from devfeed_core import analysis

    retained = []
    taxonomy = {"topics": [], "tags": []}
    with analysis.reuse_candidates(retained=retained):
        analysis.analysis_candidates(taxonomy, {"title": "First article"})
    first = retained[0]
    with analysis.reuse_candidates(retained=retained):
        analysis.analysis_candidates(taxonomy, {"title": "Another article"})
    assert retained[0] is first
    changed = {"topics": [], "tags": []}
    with analysis.reuse_candidates(retained=retained):
        for index in range(20):
            analysis.analysis_candidates(changed, {"title": f"Article {index}"})
    assert len(retained) == 1 and retained[0] is not first
    assert retained[0].taxonomy is changed and len(retained[0].results) == 8
