"""Source-board streams retain active work and compose saved operator pages.

Each batch contains already-scoped repository rows and their real lifecycle facts.
No fake SQL predicates, paging, locks or transaction isolation are implemented here.
"""

import itertools
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_admin_api import pipeline_reads
from devfeed_admin_api import source_pipeline as pipeline
from devfeed_core.read_models import ReadModelSnapshot
from test_admin_editorial_policy_unit import operator_api
from test_admin_source_pipeline_contracts_unit import candidate, facts
from test_source_readiness_unit import NOW, profile
from test_source_readiness_unit import source as source_row

SOURCE_IDS = itertools.count(1)


def publisher(**changes):
    identifier = uuid.UUID(int=next(SOURCE_IDS))
    slug = f"publisher-{identifier.hex}"
    return source_row(
        **{
            "id": identifier,
            "slug": slug,
            "feed_url": f"https://{slug}.example/feed",
            "website_url": f"https://{slug}.example/",
            **changes,
        }
    )


def query(monkeypatch, **changes):
    monkeypatch.setattr(pipeline, "utcnow", lambda: NOW)
    return pipeline.SourcePipelineQuery(
        **{
            "q": "",
            "source_type": None,
            "needs_attention": False,
            "limit": 2,
            **changes,
        }
    )


def population():
    old = publisher(
        approval="approved",
        created_at=NOW - timedelta(days=30),
        updated_at=NOW - timedelta(days=20),
        last_success_at=NOW - timedelta(days=20),
    )
    recent = publisher(
        approval="approved",
        created_at=NOW - timedelta(days=30),
        updated_at=NOW - timedelta(hours=1),
    )
    busy = publisher(
        approval="approved",
        created_at=NOW - timedelta(days=30),
        updated_at=NOW - timedelta(days=20),
        last_success_at=NOW - timedelta(days=20),
    )
    # Durable work may wait in a suspended/backlogged queue beyond terminal
    # retention; its source still belongs on the board until the work settles.
    waiting = profile(
        busy,
        status="queued",
        created_at=NOW - timedelta(days=8),
        available_at=NOW - timedelta(days=8),
    )
    blocked, ready, plain = [publisher() for _ in range(3)]
    failed = profile(blocked, status="failed")
    done = profile(ready)
    rejected = candidate(
        approval_status="rejected",
        status="rejected",
        identity_url="https://rejected-publisher.example/",
        review={
            "decision": "rejected",
            "actor": "reviewer",
            "reason": "Not an engineering publisher",
            "at": (NOW - timedelta(days=2)).isoformat(),
        },
    )
    proposed = candidate()
    values = [
        facts(old),
        facts(recent),
        facts(busy, jobs={"profile": waiting}),
        facts(blocked, jobs={"profile": failed}),
        facts(ready, jobs={"profile": done}),
        facts(plain),
        facts(candidate=rejected),
        facts(candidate=proposed),
    ]
    assert [(value.readiness.stage, value.readiness.state) for value in values] == [
        ("active", "complete"),
        ("active", "complete"),
        ("active", "complete"),
        ("profile", "blocked"),
        ("review", "ready"),
        ("profile", "waiting"),
        ("rejected", "complete"),
        ("discovery", "waiting"),
    ]
    return values


@pytest.mark.parametrize("attention", [False, True])
@pytest.mark.parametrize("aggregator", [False, True])
def test_reference_stream_retains_current_work_and_review_but_omits_aged_idle_terminal_history(
    monkeypatch, attention, aggregator
):
    values = population()
    sources, candidates = values[:6], values[6:]
    if aggregator:
        for value in sources:
            value.source.source_type = "aggregator"
        # Rebuild actual facts after the source identity changes; completed
        # profile receipts are scoped to the new source type as well.
        for value in sources:
            for run in value.jobs.values():
                run.result["input"]["source_type"] = "aggregator"
        sources = [facts(value.source, jobs=value.jobs) for value in sources]
    batches = [
        [value.source for value in sources],
        [],
        [value.candidate for value in candidates],
        [],
    ]
    if aggregator:
        batches = batches[:2]
    session = Mock()
    session.scalars.side_effect = batches
    selected = []

    def load(_session, source_rows, candidate_rows=(), *, now):
        selected.append((list(source_rows), list(candidate_rows)))
        assert now == NOW
        current = sources if source_rows else candidates
        return {value.key: value for value in current}

    monkeypatch.setattr(pipeline, "source_readiness_batch", load)
    requested = query(
        monkeypatch,
        q="publisher",
        needs_attention=attention,
        source_type="aggregator" if aggregator else None,
    )
    output = list(pipeline._facts(session, requested, NOW))
    expected = sources[3:5] if attention else [*sources[1:], *([] if aggregator else candidates)]
    assert [value.key for value in output] == [value.key for value in expected]
    assert session.expunge_all.call_count == (1 if aggregator else 2)
    assert len(selected) == (1 if aggregator else 2)
    if not attention:
        assert sources[2] in output and sources[0] not in output
    session.add.assert_not_called()
    session.commit.assert_not_called()


def test_reference_stream_detaches_completed_batch_before_loading_the_next_page(monkeypatch):
    first, second = facts(publisher()), facts(publisher())
    phases = []
    rows = iter([[first.source], [second.source], [], []])

    def read(_statement):
        phases.append("read")
        return next(rows)

    session = SimpleNamespace(scalars=read, expunge_all=lambda: phases.append("detach"))

    def load(_session, current, candidates=(), **_):
        value = first if current[0].id == first.source.id else second
        return {value.key: value}

    monkeypatch.setattr(pipeline, "source_readiness_batch", load)
    output = list(pipeline._facts(session, query(monkeypatch), NOW))
    assert output == [first, second]
    assert phases == ["read", "detach", "read", "detach", "read", "read"]


@pytest.mark.parametrize("column", [False, True])
def test_source_http_board_uses_saved_projection_metadata_and_closes_session_before_return(
    monkeypatch, column
):
    value = facts(publisher(approval="approved"))
    saved = pipeline.SourcePipelineColumn(
        stage="active", total=1, items=[pipeline._card(value)], next_cursor=None
    )
    snapshot = ReadModelSnapshot(
        status="refreshing",
        generation="serving-generation",
        generated_at=NOW - timedelta(minutes=1),
        lag_seconds=15,
    )
    serving = Mock(return_value=("serving-generation", snapshot))
    rows = Mock(return_value=[saved])
    monkeypatch.setattr(pipeline_reads, "serving_projection", serving)
    monkeypatch.setattr(pipeline_reads, "source_columns", rows)
    monkeypatch.setattr(pipeline, "utcnow", lambda: NOW)
    with operator_api(pipeline) as api:
        response = api.client.get(
            "/v1/admin/source-pipeline/columns/active" if column else "/v1/admin/source-pipeline",
            params={"cursor": 0} if column else {},
        )
        assert response.status_code == 200, response.text
        output = response.json()
        if column:
            assert output["stage"] == "active" and output["items"][0]["id"] == str(value.source.id)
            assert rows.call_args.kwargs == {"stage": "active", "cursor": 0}
        else:
            assert output["counts"] == {"active": 1} and output["columns"][0]["total"] == 1
            assert output["snapshot"]["generation"] == "serving-generation"
            assert output["snapshot"]["lag_seconds"] == 15
            assert rows.call_args.kwargs == {"policy": "serving-generation"}
        api.session.close.assert_called_once_with()
        api.session.commit.assert_not_called()


@pytest.mark.parametrize("cursor,count,total", [(0, 2, 3), (2, 1, 3), (3, 0, 3)])
def test_saved_source_column_counts_and_continuation_follow_actual_card_count(
    monkeypatch, cursor, count, total
):
    cards = [
        pipeline._card(facts(publisher(approval="approved"))).model_dump(mode="json")
        for _ in range(count)
    ]
    session = Mock()
    session.execute.return_value.all.return_value = [("active", total)]
    session.scalars.return_value = cards
    requested = query(monkeypatch, q="publisher", source_type="publisher", needs_attention=False)
    (output,) = pipeline_reads.source_columns(
        session, requested, NOW, stage="active", cursor=cursor, policy="saved-policy"
    )
    assert output.stage == "active" and output.total == total
    assert [item.id for item in output.items] == [
        pipeline.SourcePipelineCard.model_validate(value).id for value in cards
    ]
    assert output.next_cursor == (cursor + count if cursor + count < total else None)
    session.add.assert_not_called()
