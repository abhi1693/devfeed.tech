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
        result = self.selected(str(statement), parameters)
        if result is None and "FROM analytics_card_timer_bootstrap WHERE singleton" in str(
            statement
        ):
            result = dict(
                policy="policy",
                epoch=0,
                kind_index=0,
                shard=None,
                cursor=None,
                complete=True,
                cleanup_pending=False,
            )
        return Cursor(result)


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
            if statement.startswith("WITH initial AS MATERIALIZED")
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
            if str(statement).startswith("DELETE FROM analytics_card_refresh"):
                order.append("ack")
            return await original_execute(statement, parameters)

        connection.execute = execute
        assert await subject._refresh_one(connection, 3, STATE, fence=AsyncMock())
        assert order == ["analytics_card_canonical", "snapshot", "analytics_card_manifest", "ack"]
        assert subject._build.call_args.args[1] == [ENTITY]
        selected = connection.calls[0]
        assert selected[1] == (1600, 1600, 1600, 1600, 8, 200)
        assert "get_byte(uuid_send(entity_id),0)" in selected[0]
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
                if statement.startswith("WITH initial AS MATERIALIZED")
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
        acknowledgements = [
            call
            for call in connection.calls
            if call[0].startswith("DELETE FROM analytics_card_refresh")
        ]
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
        connection = Connection(
            lambda statement, _parameters: (
                [dict(kind="source", entity_id=ENTITY, generation=2)]
                if "FROM analytics_card_timers WHERE policy=" in statement
                else None
            )
        )
        subject._mark = AsyncMock()

        assert await subject._timers_one(connection, 3, STATE, fence=AsyncMock())

        assert subject._mark.call_args.args[1] == {("source", ENTITY)}
        client.query.assert_not_awaited()
        due = next(
            call for call in connection.calls if "FROM analytics_card_timers WHERE" in call[0]
        )
        assert due[1] == ("policy", 0, 200) and "available_at<=statement_timestamp()" in due[0]
        assert any(
            call[0].startswith("DELETE FROM analytics_card_timers") for call in connection.calls
        )

    asyncio.run(verify())


@pytest.mark.parametrize("kind", ["topic-proposal", "relationship-proposal"])
def test_proposal_refreshes_do_not_read_or_write_timer_state(kind):
    async def verify():
        subject, client = publisher()
        connection = Connection(
            lambda statement, _parameters: (
                [dict(kind=kind, entity_id=ENTITY, generation=2)]
                if statement.startswith("WITH initial AS MATERIALIZED")
                else None
            )
        )
        subject._build = Mock(return_value=[observation()])
        client.query.side_effect = [[], [{"shard": 18, "count": 1}]]
        assert await subject._refresh_one(connection, 3, STATE, fence=AsyncMock())
        assert not any("analytics_card_timer" in statement for statement, _ in connection.calls)
        subject._build = Mock(return_value=[observation(next_transition_at=NOW)])
        with pytest.raises(RuntimeError, match="deadlines are not indexed"):
            await subject._refresh_one(connection, 4, STATE, fence=AsyncMock())

    asyncio.run(verify())


def timer_control(**updates):
    return (
        dict(
            policy="policy",
            epoch=2,
            kind_index=0,
            shard=None,
            cursor=None,
            complete=False,
            cleanup_pending=False,
        )
        | updates
    )


@pytest.mark.parametrize("complete", [False, True])
def test_timer_publication_preserves_new_deadlines_and_clears_obsolete_ones(complete):
    async def verify():
        subject, _ = publisher()
        connection = Connection()
        other = uuid.UUID("13000000-0000-4000-8000-000000000002")
        records = [
            dict(entity_id=ENTITY, next_transition_at=NOW + timedelta(hours=1)),
            dict(entity_id=other, next_transition_at=None),
        ]
        await subject._write_timers(
            connection, "policy", "article", records, 7, timer_control(complete=complete)
        )
        inserted = next(call for call in connection.calls if call[0].startswith("INSERT INTO"))
        assert inserted[1][3] == ([ENTITY] if complete else [ENTITY, other])
        assert inserted[1][4] == ([7] if complete else [7, 7])
        assert inserted[1][5] == (
            [NOW + timedelta(hours=1)] if complete else [NOW + timedelta(hours=1), None]
        )
        deleted = [call for call in connection.calls if call[0].startswith("DELETE FROM")]
        assert bool(deleted) is complete
        if complete:
            assert deleted[0][1] == ("policy", "article", [other], 2, 7)

    asyncio.run(verify())


def test_timer_adoption_pages_canonical_keys_and_preserves_observation_versions():
    async def verify():
        subject, client = publisher()
        subject.settings.analytics_backfill_batch_size = 2
        control = timer_control(shard=18)
        connection = Connection(
            lambda statement, _parameters: (
                dict(control)
                if "FROM analytics_card_timer_bootstrap WHERE singleton" in statement
                else None
            )
        )
        other = uuid.UUID("12000000-0000-4000-8000-000000000002")
        client.query.return_value = [
            dict(
                shard=18,
                entity_id=str(ENTITY),
                version=str(5 << 64),
                deleted=0,
                next_transition_at="2026-10-27 13:00:00.000000",
                current=1,
            ),
            dict(
                shard=18,
                entity_id=str(other),
                version=str(6 << 64),
                deleted=1,
                next_transition_at=NOW.isoformat(),
                current=1,
            ),
        ]
        assert await subject._timer_bootstrap_one(connection, 8, STATE, fence=AsyncMock())
        upsert = next(
            call
            for call in connection.calls
            if call[0].startswith("INSERT INTO analytics_card_timers")
        )
        assert upsert[1] == ("policy", "article", 2, [ENTITY], [5], [NOW + timedelta(hours=1)])
        assert "< (excluded.epoch,excluded.generation)" in upsert[0]
        assert connection.calls[-1][1] == (18, other)
        assert client.query.call_args.args[1]["limit"] == 2
        assert client.query.call_args.args[1]["shard"] == 18
        assert "shard={shard:UInt16}" in client.query.call_args.args[0]
        assert "ORDER BY entity_id" in client.query.call_args.args[0]

        control.update(shard=18, cursor=other)
        client.query.return_value = []
        assert await subject._timer_bootstrap_one(connection, 9, STATE, fence=AsyncMock())
        assert client.query.call_args.args[1]["cursor"] == other
        assert "entity_id>{cursor:UUID}" in client.query.call_args.args[0]
        assert connection.calls[-1][1] == (19,)

        control.update(kind_index=2, shard=255, cursor=None)
        assert await subject._timer_bootstrap_one(connection, 10, STATE, fence=AsyncMock())
        assert connection.calls[-1][1] == (3, True, True)

    asyncio.run(verify())


def test_timer_adoption_does_not_commit_a_page_after_epoch_changes():
    async def verify():
        subject, client = publisher()
        control = timer_control()
        connection = Connection(
            lambda statement, _parameters: (
                dict(control)
                if "FROM analytics_card_timer_bootstrap WHERE singleton" in statement
                else None
            )
        )

        async def reset_during_read(*_args):
            control["epoch"] += 1
            return []

        client.query.side_effect = reset_during_read
        assert not await subject._timer_bootstrap_one(connection, 4, STATE, fence=AsyncMock())
        assert not any("SET kind_index=" in statement for statement, _ in connection.calls)

    asyncio.run(verify())


def test_timer_adoption_bridge_and_due_pop_keep_tombstones_until_seed_completes():
    async def verify():
        subject, client = publisher()
        control = timer_control()
        connection = Connection(
            lambda statement, _parameters: (
                dict(control)
                if "FROM analytics_card_timer_bootstrap WHERE singleton" in statement
                else None
            )
        )
        client.query.side_effect = [[], [{"kind": "source", "entity_id": str(ENTITY)}]]
        subject._mark = AsyncMock()
        assert await subject._timers_one(connection, 3, STATE, fence=AsyncMock())
        assert subject._mark.call_args.args[1] == {("source", ENTITY)}
        marker = next(
            call
            for call in connection.calls
            if call[0].startswith("INSERT INTO analytics_card_timers")
        )
        assert marker[1] == ("policy", "source", 2, [ENTITY], [3], [None])

        due = Connection(
            lambda statement, _parameters: (
                dict(control)
                if "FROM analytics_card_timer_bootstrap WHERE singleton" in statement
                else [dict(kind="source", entity_id=ENTITY, generation=3)]
                if "FROM analytics_card_timers WHERE policy=" in statement
                else None
            )
        )
        client.query.reset_mock()
        assert await subject._timers_one(due, 4, STATE, fence=AsyncMock())
        assert any(
            call[0].startswith("UPDATE analytics_card_timers AS t SET available_at=NULL")
            for call in due.calls
        )
        client.query.assert_not_awaited()

    asyncio.run(verify())


def test_timer_adoption_bridge_scans_only_unseeded_kinds_and_shards():
    async def verify():
        subject, client = publisher()
        control = timer_control(kind_index=1, shard=18)
        client.query.return_value = []
        assert not await subject._timer_bootstrap_due(
            Connection(), 9, "policy", control, fence=AsyncMock()
        )
        calls = client.query.await_args_list
        assert [call.args[1]["kind"] for call in calls] == ["source", "candidate"]
        assert calls[0].args[1]["first_shard"] == 18
        assert "shard>={first_shard:UInt16}" in calls[0].args[0]
        assert "first_shard" not in calls[1].args[1]

    asyncio.run(verify())


def test_timer_cleanup_is_batched_and_stops_when_tombstones_are_drained():
    async def verify():
        subject, _ = publisher()
        control = timer_control(complete=True, cleanup_pending=True)
        results = iter([[{"removed": 1}], []])
        connection = Connection(
            lambda statement, _parameters: (
                dict(control)
                if "FROM analytics_card_timer_bootstrap WHERE singleton" in statement
                else next(results)
                if statement.startswith("WITH stale AS")
                else None
            )
        )
        assert await subject._timer_bootstrap_one(connection, 5, STATE, fence=AsyncMock())
        assert not await subject._timer_bootstrap_one(connection, 6, STATE, fence=AsyncMock())
        assert connection.calls[-1][0].endswith("cleanup_pending=false WHERE singleton")
        assert all(
            call[1][-1] == 200 for call in connection.calls if call[0].startswith("WITH stale AS")
        )

    asyncio.run(verify())


def test_timer_control_initializes_policy_and_refuses_mismatched_or_missing_state():
    async def verify():
        subject, _ = publisher()
        empty = timer_control(policy=None)
        connection = Connection(
            lambda statement, _parameters: (
                dict(empty)
                if "FROM analytics_card_timer_bootstrap WHERE singleton" in statement
                else None
            )
        )
        assert (await subject._timer_control(connection, "policy"))["policy"] == "policy"
        assert connection.calls[-1][1] == ("policy",)
        empty["policy"] = "previous"
        with pytest.raises(RuntimeError, match="policy changed"):
            await subject._timer_control(connection, "policy")

        class Missing(Connection):
            async def execute(self, _statement, _parameters=None):
                return Cursor(None)

        with pytest.raises(RuntimeError, match="state is missing"):
            await subject._timer_control(Missing(), "policy")

    asyncio.run(verify())


def test_quiet_shard_renewal_rebuilds_only_one_immutable_generation():
    async def verify():
        subject, client = publisher()
        client.query.return_value = [{"policy": "policy", "kind": "article", "shard": 18}]
        subject._publish_shard = AsyncMock()
        connection, fence = Connection(), AsyncMock()

        state = dict(STATE, card_serving_policy="previous")
        assert await subject._renew_one(connection, 9, state, fence=fence)

        assert "INTERVAL 1 DAY" in client.query.call_args.args[0]
        assert client.query.call_args.args[1] == {"policy": "policy"}
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


@pytest.mark.parametrize(
    "serving,target,complete,ready",
    [
        ("policy", "policy", True, 1),
        ("policy", "policy", False, 0),
        ("previous", "policy", False, 0),
        ("previous", "policy", True, 0),
        (None, "policy", True, 0),
    ],
)
def test_status_never_exposes_previous_policy_as_ready(serving, target, complete, ready):
    async def verify():
        subject, client = publisher()
        state = dict(
            STATE,
            card_serving_policy=serving,
            card_target_policy=target,
            card_bootstrap_complete=complete,
        )
        connection = Connection(
            lambda statement, parameters: (
                state if "FROM analytics_pipeline_state" in statement else {"lag": 0}
            )
        )
        await subject._status(connection, 3, fence=AsyncMock())
        published = client.insert.call_args.args[1][0]
        assert published["ready"] == ready
        assert published["target_policy"] == target

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


@pytest.mark.parametrize(
    "kind,builder,groups",
    [
        ("article", "article_rows", ([ENTITY],)),
        ("source", "source_rows", ([ENTITY], [])),
        ("candidate", "source_rows", ([], [ENTITY])),
        ("topic-proposal", "proposal_rows", ([ENTITY], [])),
        ("relationship-proposal", "proposal_rows", ([], [ENTITY])),
    ],
)
def test_builder_reads_only_requested_kind_and_reuses_then_releases_source_resources(
    monkeypatch, kind, builder, groups
):
    subject, _ = publisher()
    engine, factory = Mock(), Mock()
    create = Mock(return_value=engine)
    sessions = Mock(return_value=factory)
    monkeypatch.setattr(cards, "create_engine", create)
    monkeypatch.setattr(cards, "sessionmaker", sessions)
    builders = {
        name: Mock(return_value=[observation()])
        for name in ("article_rows", "source_rows", "proposal_rows")
    }
    for name, observe in builders.items():
        monkeypatch.setattr(cards, name, observe)

    for _ in range(2):
        result = subject._build(kind, [ENTITY], "policy", NOW)
        assert result[0]["entity_id"] == ENTITY
    create.assert_called_once_with(subject.settings.database_url, poolclass=cards.NullPool)
    sessions.assert_called_once_with(engine, expire_on_commit=False)
    assert builders[builder].call_args.args == (factory, *groups, "policy", NOW)
    assert all(not observe.called for name, observe in builders.items() if name != builder)

    subject._catalog_snapshots["retained catalogue"] = object()
    subject._candidate_rankings.append(object())
    subject.close()
    subject.close()
    assert not subject._catalog_snapshots and not subject._candidate_rankings
    assert subject._factory is None and subject._engine is None
    engine.dispose.assert_called_once()


def test_ingestion_keeps_research_and_promoted_candidate_owners_in_the_initial_barrier(monkeypatch):
    async def verify():
        subject, client = publisher()
        monkeypatch.setattr(cards, "pipeline_policy_version", lambda _settings: "policy")
        candidate, source, research, topic, relationship, revision = [
            uuid.uuid4() for _ in range(6)
        ]
        state = dict(STATE, card_bootstrap_barrier=False)

        def selected(statement, parameters):
            if "FROM analytics_pipeline_state" in statement:
                return state
            if statement.startswith("SELECT proposal_id FROM topic_analysis_jobs"):
                return [{"proposal_id": topic}]
            if statement.startswith("SELECT id FROM topic_relation_proposals"):
                return [{"id": relationship}]
            if statement.startswith("SELECT source_id FROM source_candidates"):
                return [{"source_id": source}]
            if "FROM catalog_revisions" in statement:
                return [{"name": "topics", "revision": revision}]
            return None

        connection = Connection(selected)
        events = [
            dict(source_table="topic_analysis_jobs", operation="I", record={"id": str(research)}),
            dict(source_table="source_candidates", operation="I", record={"id": str(candidate)}),
            dict(source_table="catalog_revisions", operation="I", record={"name": "topics"}),
        ]
        fence = AsyncMock()
        await subject.ingest(connection, events, 42, fence=fence)

        refresh = next(
            call
            for call in connection.calls
            if call[0].startswith("INSERT INTO analytics_card_refresh")
        )
        assert refresh[1][:2] == (42, True)
        assert set(zip(refresh[1][2], refresh[1][3], strict=True)) == {
            ("candidate", candidate),
            ("source", source),
            ("topic-proposal", topic),
            ("relationship-proposal", relationship),
        }
        fanout = next(
            call
            for call in connection.calls
            if call[0].startswith("INSERT INTO analytics_card_fanout")
        )
        assert fanout[1] == (42, True, ["catalog"], [cards.ZERO_UUID])
        assert "requested_at=excluded.requested_at" in fanout[0]
        assert "requested_at=excluded.requested_at" not in refresh[0]
        target = next(
            parameters
            for statement, parameters in connection.calls
            if statement.startswith("UPDATE analytics_pipeline_state")
        )
        assert target[0].obj == [["topics", str(revision)]]
        assert subject._factory is None  # Ingestion queues identities without hydrating cards.
        fence.assert_awaited_once_with(connection)

        schema_calls = client.execute.await_count
        before = len(connection.calls)
        await subject.ingest(connection, [], 43, fence=fence)
        assert client.execute.await_count == schema_calls
        assert not any(
            statement.startswith(("INSERT", "UPDATE", "DELETE"))
            for statement, _ in connection.calls[before:]
        )

    asyncio.run(verify())


@pytest.mark.parametrize("reset", [False, True])
def test_ingestion_restarts_coverage_before_queueing_changed_policy_or_truncated_sources(
    monkeypatch, reset
):
    async def verify():
        subject, _ = publisher()
        monkeypatch.setattr(cards, "pipeline_policy_version", lambda _settings: "policy")
        previous = dict(STATE, card_target_policy="policy" if reset else "previous")
        current = dict(STATE, card_bootstrap_barrier=False)
        subject._state = AsyncMock(side_effect=[previous, current])
        subject._reset = AsyncMock()
        subject._mark = AsyncMock()
        event = dict(
            source_table="articles", operation="T" if reset else "I", record={"id": str(ENTITY)}
        )
        connection, fence = Connection(), AsyncMock()

        await subject.ingest(connection, [event], 8, fence=fence)

        subject._reset.assert_awaited_once_with(connection, "policy", 8, fence=fence)
        assert subject._state.await_count == 2
        assert all(call.args[3] is True for call in subject._mark.await_args_list)
        if not reset:
            assert subject._mark.await_args_list[0].args[1] == {("article", ENTITY)}

    asyncio.run(verify())


def test_idle_cycle_does_not_hydrate_sources_or_republish_but_still_observes_coverage(monkeypatch):
    async def verify():
        subject, client = publisher()
        monkeypatch.setattr(cards, "pipeline_policy_version", lambda _settings: "policy")
        subject._state = AsyncMock(return_value=STATE)
        subject._coverage, subject._status = AsyncMock(), AsyncMock()
        client.query.return_value = []
        connection = Connection(
            lambda statement, parameters: (
                {"saturated": False} if " AS saturated" in statement else None
            )
        )
        fence = AsyncMock()

        assert not await subject.once(connection, 5, fence=fence)
        assert subject._factory is None
        assert all(
            "next_transition_at<=now64(6)" not in call.args[0]
            for call in client.query.await_args_list
        )
        client.insert.assert_not_called()
        assert not any(
            statement.startswith(("UPDATE", "DELETE", "INSERT"))
            for statement, _ in connection.calls
        )
        subject._coverage.assert_awaited_once_with(connection, fence=fence)
        subject._status.assert_awaited_once_with(connection, 5, fence=fence)
        initialized = client.execute.await_count
        assert initialized > 0
        assert not await subject.once(connection, 6, fence=fence)
        assert client.execute.await_count == initialized

    asyncio.run(verify())


def test_work_classes_rotate_so_continuous_dirty_cards_cannot_starve_coverage(monkeypatch):
    async def verify():
        subject, _ = publisher()
        monkeypatch.setattr(cards, "pipeline_policy_version", lambda _settings: "policy")
        subject._initialized = True
        subject._state = AsyncMock(return_value=STATE)
        subject._coverage, subject._status = AsyncMock(), AsyncMock()
        names = (
            "_refresh_one",
            "_backfill_one",
            "_fanout_one",
            "_timers_one",
            "_timer_bootstrap_one",
            "_renew_one",
        )
        workers = {name: AsyncMock(return_value=True) for name in names}
        for name, worker in workers.items():
            setattr(subject, name, worker)
        connection, fence = Connection(), AsyncMock()
        renew = AsyncMock(return_value={"lease_generation": 20})

        for generation in range(len(names)):
            assert await subject.once(connection, generation, fence=fence, renew=renew)
            assert sum(worker.await_count for worker in workers.values()) == generation + 1
        assert all(worker.await_count == 1 for worker in workers.values())
        assert subject._coverage.await_count == subject._status.await_count == len(names)
        assert subject._turn == 0

    asyncio.run(verify())


def test_lease_loss_and_new_policy_block_every_incremental_worker(monkeypatch):
    async def verify():
        subject, client = publisher()
        subject._initialized = True
        subject._state = AsyncMock(return_value=dict(STATE, card_target_policy="previous"))
        subject._reset, subject._status = AsyncMock(), AsyncMock()
        subject._refresh_one = AsyncMock()
        connection, fence = Connection(), AsyncMock()
        with pytest.raises(RuntimeError, match="superseded"):
            await subject.once(connection, 3, fence=fence, renew=AsyncMock(return_value=None))
        subject._state.assert_not_called()
        subject._refresh_one.assert_not_called()
        client.insert.assert_not_called()

        monkeypatch.setattr(cards, "pipeline_policy_version", lambda _settings: "policy")
        assert await subject.once(connection, 4, fence=fence)
        subject._reset.assert_awaited_once_with(connection, "policy", 4, fence=fence)
        subject._status.assert_awaited_once_with(connection, 4, fence=fence, force=True)
        subject._refresh_one.assert_not_called()

    asyncio.run(verify())


def test_completed_catalog_and_population_barriers_advance_together_only_when_drained():
    async def verify():
        subject, _ = publisher()
        pending = dict(covered=False, fanout=False, dirty=False, catalog=True, any_dirty=True)
        connection = Connection(lambda statement, parameters: pending)
        fence = AsyncMock()
        await subject._coverage(connection, fence=fence)
        assert not any(statement.startswith("UPDATE") for statement, _ in connection.calls)

        pending.update(covered=True, catalog=False, any_dirty=False)
        await subject._coverage(connection, fence=fence)
        updates = [statement for statement, _ in connection.calls if statement.startswith("UPDATE")]
        assert any("card_serving_policy=card_target_policy" in statement for statement in updates)
        assert any(
            "card_catalog_revision=card_catalog_target_revision" in statement
            and "card_catalog_progress_at=NULL" in statement
            for statement in updates
        )

    asyncio.run(verify())


def test_status_heartbeat_coalesces_only_successfully_published_observations(monkeypatch):
    async def verify():
        subject, client = publisher()
        clock = [100.0]
        monkeypatch.setattr(cards.time, "monotonic", lambda: clock[0])
        subject._state = AsyncMock(return_value=STATE)
        connection, fence = Connection(lambda statement, parameters: {"lag": 0}), AsyncMock()
        client.insert.side_effect = RuntimeError("status flush failed")
        with pytest.raises(RuntimeError, match="status flush failed"):
            await subject._status(connection, 1, fence=fence)
        assert subject._last_status_key is None

        client.insert.side_effect = None
        await subject._status(connection, 2, fence=fence)
        clock[0] += 29
        await subject._status(connection, 3, fence=fence)
        assert client.insert.await_count == 2
        clock[0] += 1
        await subject._status(connection, 4, fence=fence)
        assert client.insert.await_count == 3
        assert client.insert.await_args.args[1][0]["version"] == 4

    asyncio.run(verify())


@pytest.mark.parametrize("lease_lost_after_scan", [False, True])
def test_finished_fanout_retires_only_its_captured_generation_under_a_live_lease(
    lease_lost_after_scan,
):
    async def verify():
        subject, client = publisher()
        subject._saturated = AsyncMock(return_value=False)
        selected = dict(
            kind="source",
            entity_id=ENTITY,
            cursor=uuid.uuid4(),
            generation=2,
            initial_pending=False,
            requested_at=NOW,
            available_at=NOW,
        )
        connection = Connection(
            lambda statement, parameters: (
                selected
                if statement.startswith("SELECT ") and "FROM analytics_card_fanout" in statement
                else None
            )
        )
        fence = AsyncMock(
            side_effect=[None, RuntimeError("superseded")] if lease_lost_after_scan else None
        )
        if lease_lost_after_scan:
            with pytest.raises(RuntimeError, match="superseded"):
                await subject._fanout_one(connection, 3, STATE, fence=fence)
        else:
            assert await subject._fanout_one(connection, 3, STATE, fence=fence)
        retired = [call for call in connection.calls if call[0].startswith("DELETE")]
        if lease_lost_after_scan:
            assert not retired
        else:
            assert len(retired) == 1
            assert retired[0][1] == ("source", ENTITY, 2)
            assert "AND generation=%s" in retired[0][0]
        assert subject._factory is None
        client.insert.assert_not_called()

    asyncio.run(verify())


def test_empty_publication_batch_performs_no_storage_or_lease_work():
    async def verify():
        subject, client = publisher()
        connection, fence = Connection(), AsyncMock()
        await subject._publish_shards(connection, "policy", "article", [], 3, fence=fence)
        client.execute.assert_not_called()
        client.query.assert_not_called()
        client.insert.assert_not_called()
        fence.assert_not_called()
        assert not connection.calls

    asyncio.run(verify())
