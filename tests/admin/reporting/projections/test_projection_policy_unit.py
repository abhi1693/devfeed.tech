"""Saved board documents preserve generation, counts and independent page cursors.

Repository results are already scoped and ordered. SQL eligibility, ranking and
snapshot isolation are covered by integration tests, not emulated here.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_admin_api import article_pipeline, source_pipeline
from devfeed_admin_api import pipeline_reads as reads
from devfeed_core.article_lifecycle import derive_publication_readiness
from devfeed_core.models import ReadModelState
from devfeed_core.read_models import ReadModelSnapshot
from fastapi import HTTPException
from sqlalchemy.orm import Session

from tests.admin.sources.test_source_pipeline_contracts_unit import facts
from tests.support.article_readiness import processing_case as processing_case
from tests.support.source_readiness import NOW, source


@pytest.mark.parametrize("available", [False, True])
def test_board_cold_start_returns_retry_hint_without_starting_projection_work(available):
    session = Mock(spec=Session)
    session.in_transaction.return_value = True
    session.get.return_value = (
        ReadModelState(name="pipeline", payload={"backfill_complete": False}, updated_at=NOW)
        if available
        else None
    )
    with pytest.raises(HTTPException) as error:
        reads.serving_projection(session)
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "projection_initializing"
    assert error.value.headers == {"Retry-After": "2"}
    session.scalar.assert_not_called()
    session.add.assert_not_called()


@pytest.mark.parametrize(
    "change,status",
    [
        (None, "fresh"),
        ("target", "rebuilding"),
        ("policy", "rebuilding"),
        ("backfill", "rebuilding"),
        ("event", "refreshing"),
        ("due", "refreshing"),
        ("both", "refreshing"),
    ],
)
def test_snapshot_status_explains_rebuild_or_the_oldest_pending_transition(
    monkeypatch, change, status
):
    policy = reads.pipeline_policy_version()
    payload = {
        "serving_policy": policy,
        "target_policy": policy,
        "backfill_complete": True,
        "serving_generated_at": (NOW - timedelta(minutes=1)).isoformat(),
    }
    if change == "target":
        payload["target_policy"] = "next-policy"
    elif change == "policy":
        payload["serving_policy"] = payload["target_policy"] = "previous-policy"
    elif change == "backfill":
        payload["backfill_complete"] = False
    event = NOW - timedelta(seconds=30) if change in {"event", "both"} else None
    due = NOW - timedelta(seconds=60) if change in {"due", "both"} else None
    session = Mock(spec=Session)
    session.in_transaction.return_value = True
    session.get.return_value = ReadModelState(name="pipeline", payload=payload, updated_at=NOW)
    session.scalar.side_effect = [event, due]
    monkeypatch.setattr(reads, "utcnow", lambda: NOW)

    generation, snapshot = reads.serving_projection(session)

    assert generation == payload["serving_policy"] and snapshot.generation == generation
    assert snapshot.status == status and snapshot.generated_at == NOW - timedelta(minutes=1)
    assert snapshot.lag_seconds == (60 if due else 30 if event else 0)
    session.add.assert_not_called()


@pytest.mark.parametrize("kind", [None, "article", "source"])
@pytest.mark.parametrize("global_timestamp", [False, True])
def test_legacy_snapshot_uses_global_timestamp_then_persisted_state_time(
    monkeypatch, kind, global_timestamp
):
    policy = reads.pipeline_policy_version()
    session = Mock(spec=Session)
    session.in_transaction.return_value = True
    payload = {"serving_policy": policy, "target_policy": policy, "backfill_complete": True}
    if global_timestamp:
        payload["serving_generated_at"] = (NOW - timedelta(minutes=1)).isoformat()
    session.get.return_value = ReadModelState(
        name="pipeline", payload=payload, updated_at=NOW - timedelta(minutes=2)
    )
    session.scalar.side_effect = [None, None]
    monkeypatch.setattr(reads, "utcnow", lambda: NOW)
    assert reads.serving_projection(session, kind=kind)[1].generated_at == NOW - timedelta(
        minutes=1 if global_timestamp else 2
    )


@pytest.mark.parametrize("kind", [None, "article", "source"])
@pytest.mark.parametrize("full_backfill", [False, True])
def test_catalog_refresh_keeps_source_freshness_independent_of_article_work(
    monkeypatch, kind, full_backfill
):
    policy = reads.pipeline_policy_version()
    payload = {
        "serving_policy": policy,
        "target_policy": policy,
        "backfill_complete": not full_backfill,
        "serving_generated_at": (NOW - timedelta(minutes=20)).isoformat(),
        "article_generated_at": (NOW - timedelta(seconds=5)).isoformat(),
        "source_generated_at": (NOW - timedelta(seconds=2)).isoformat(),
        "catalog_refresh": {
            "token": "refresh-token",
            "revision": [["tags", "current-revision"]],
            "started_at": (NOW - timedelta(seconds=90)).isoformat(),
        },
    }
    session = Mock(spec=Session)
    session.in_transaction.return_value = True
    session.get.return_value = ReadModelState(name="pipeline", payload=payload, updated_at=NOW)
    session.scalar.side_effect = [None, None]
    monkeypatch.setattr(reads, "utcnow", lambda: NOW)

    _, snapshot = reads.serving_projection(session, kind=kind)

    assert snapshot.status == (
        "rebuilding" if full_backfill else "fresh" if kind == "source" else "refreshing"
    )
    assert snapshot.lag_seconds == (0 if kind == "source" else 90)
    assert snapshot.generated_at == NOW - (
        timedelta(seconds=2)
        if kind == "source"
        else timedelta(seconds=5)
        if kind == "article"
        else timedelta(minutes=20)
    )
    session.add.assert_not_called()


@pytest.mark.parametrize("kind", ["article", "source"])
def test_board_pending_events_and_timers_are_scoped_to_the_requested_kind(monkeypatch, kind):
    policy = reads.pipeline_policy_version()
    session = Mock(spec=Session)
    session.in_transaction.return_value = True
    session.get.return_value = ReadModelState(
        name="pipeline",
        payload={"serving_policy": policy, "target_policy": policy, "backfill_complete": True},
        updated_at=NOW,
    )
    session.scalar.side_effect = [NOW - timedelta(seconds=30), NOW - timedelta(seconds=60)]
    monkeypatch.setattr(reads, "utcnow", lambda: NOW)
    _, snapshot = reads.serving_projection(session, kind=kind)
    pending, transitions = [call.args[0].compile().params for call in session.scalar.call_args_list]
    event_kinds = next(set(value) for value in pending.values() if isinstance(value, list))
    timed_kinds = next(set(value) for value in transitions.values() if isinstance(value, list))
    assert "policy" in event_kinds
    if kind == "source":
        assert {"source", "candidate", "backfill_source", "backfill_candidate"} <= event_kinds
        assert not {"article", "backfill_article", "catalog", "source_fanout"} & event_kinds
        assert timed_kinds == {"source", "candidate"}
    else:
        assert {"article", "backfill_article", "catalog", "source_fanout"} <= event_kinds
        assert not {"source", "candidate", "backfill_source", "backfill_candidate"} & event_kinds
        assert timed_kinds == {"article"}
    assert snapshot.status == "refreshing" and snapshot.lag_seconds == 60


@pytest.mark.parametrize("kind", [None, "article", "source"])
@pytest.mark.parametrize("publisher_age", [-10, 60, 61])
def test_idle_board_requires_recent_publisher_verification(monkeypatch, kind, publisher_age):
    policy = reads.pipeline_policy_version()
    session = Mock(spec=Session)
    session.in_transaction.return_value = True
    session.get.return_value = ReadModelState(
        name="pipeline",
        payload={
            "serving_policy": policy,
            "target_policy": policy,
            "backfill_complete": True,
            "serving_generated_at": (NOW - timedelta(days=1)).isoformat(),
        },
        updated_at=NOW - timedelta(seconds=publisher_age),
    )
    session.scalar.side_effect = [None, None]
    monkeypatch.setattr(reads, "utcnow", lambda: NOW)
    _, snapshot = reads.serving_projection(session, kind=kind)
    assert snapshot.status == ("stale" if publisher_age > 60 else "fresh")
    assert snapshot.generated_at == NOW - timedelta(days=1)
    assert snapshot.lag_seconds == max(0, publisher_age)


def current_card(processing_case, *, published=False):
    article, evidence = processing_case
    if published:
        article.publication_status, article.published_to_feed_at = "published", NOW
    current = {
        name: evidence[name]
        for name in ("content", "enrichment", "analysis", "discovery", "storage")
    }
    readiness = derive_publication_readiness(article, **evidence)
    return article_pipeline._card(article, readiness, current, taxonomy=evidence["taxonomy"])


@pytest.mark.parametrize("published,cursor,total", [(False, 0, 2), (False, 1, 2), (True, 0, 1)])
def test_article_column_preserves_saved_cards_and_uses_count_for_continuation(
    monkeypatch, processing_case, published, cursor, total
):
    card = current_card(processing_case, published=published)
    snapshot = ReadModelSnapshot(
        status="refreshing",
        generated_at=NOW - timedelta(seconds=3),
        generation="serving",
        lag_seconds=3,
    )
    monkeypatch.setattr(reads, "utcnow", lambda: NOW)
    monkeypatch.setattr(reads, "serving_projection", lambda _, **_kwargs: ("serving", snapshot))
    session = SimpleNamespace(
        execute=lambda _: SimpleNamespace(all=lambda: [(card.stage, total)]),
        scalars=lambda _: [card.model_dump(mode="json")],
    )

    result = reads.article_board(
        session,
        q=None,
        source_id=None,
        needs_attention=False,
        limit=1,
        stage=card.stage,
        cursor=cursor,
    )

    assert result.as_of == NOW and result.snapshot == snapshot
    assert result.counts == {card.stage: total}
    column = result.columns[0]
    assert column.items == [card] and column.total == total
    assert column.next_cursor == (cursor + 1 if cursor + 1 < total else None)


@pytest.mark.parametrize("cursor", [0, 1])
def test_source_group_cursor_counts_groups_while_stage_total_still_counts_articles(
    monkeypatch, processing_case, cursor
):
    card = current_card(processing_case)
    snapshot = ReadModelSnapshot(
        status="fresh", generated_at=NOW, generation="serving", lag_seconds=0
    )
    monkeypatch.setattr(reads, "serving_projection", lambda _, **_kwargs: ("serving", snapshot))
    group = {
        "source_id": card.source_id if not cursor else None,
        "source_name": card.source_name if not cursor else None,
        "total": 3 if not cursor else 2,
        "state_counts": {card.state: 3 if not cursor else 2},
        "group_count": 2,
    }
    request = Mock(return_value={card.stage: [group]})
    monkeypatch.setattr(reads, "_article_source_groups", request)
    session = SimpleNamespace(execute=lambda _: SimpleNamespace(all=lambda: [(card.stage, 5)]))

    result = reads.article_board(
        session,
        q=None,
        source_id=None,
        needs_attention=False,
        limit=1,
        stage=card.stage,
        cursor=cursor,
        group_by="source",
    )

    column = result.columns[0]
    assert result.counts == {card.stage: 5} and column.total == 5
    assert column.items == [] and column.next_cursor == (1 if cursor == 0 else None)
    assert column.source_groups == [
        article_pipeline.PipelineSourceGroup(
            **{key: group[key] for key in ("source_id", "source_name", "total", "state_counts")}
        )
    ]
    assert request.call_args.args[2:] == ((card.stage,), 1, cursor)


def test_empty_article_board_keeps_all_lanes_and_does_not_invent_continuation(monkeypatch):
    snapshot = ReadModelSnapshot(status="fresh", generation="serving", lag_seconds=0)
    monkeypatch.setattr(reads, "utcnow", lambda: NOW)
    monkeypatch.setattr(reads, "serving_projection", lambda _, **_kwargs: ("serving", snapshot))
    session = SimpleNamespace(
        execute=lambda _: SimpleNamespace(all=lambda: []), scalars=lambda _: []
    )
    result = reads.article_board(session, q=None, source_id=None, needs_attention=False, limit=2)
    assert result.as_of == NOW
    assert set(result.counts) == set(article_pipeline.STAGES)
    assert all(
        column.total == 0 and column.items == [] and column.next_cursor is None
        for column in result.columns
    )


@pytest.mark.parametrize("cursor", [0, 2])
def test_source_columns_keep_snapshot_identity_and_total_beyond_the_last_page(monkeypatch, cursor):
    current = source()
    value = facts(current)
    card = source_pipeline._card(value)
    monkeypatch.setattr(source_pipeline, "utcnow", lambda: NOW)
    query = source_pipeline.SourcePipelineQuery(
        q="", limit=1, terminal_since=NOW - timedelta(days=7)
    )
    serving = Mock(return_value=("serving", ReadModelSnapshot(status="fresh")))
    monkeypatch.setattr(reads, "serving_projection", serving)
    session = SimpleNamespace(
        execute=lambda _: SimpleNamespace(all=lambda: [(card.stage, 1)]),
        scalars=lambda _: [] if cursor else [card.model_dump(mode="json")],
    )

    column = reads.source_columns(session, query, NOW, stage=card.stage, cursor=cursor)[0]

    assert column.total == 1 and column.next_cursor is None
    assert column.items == ([] if cursor else [card])
    if column.items:
        assert (
            column.items[0].snapshot == value.snapshot and column.items[0].source_id == current.id
        )
    serving.assert_called_once_with(session, kind="source")
