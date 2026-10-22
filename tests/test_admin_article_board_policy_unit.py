"""Offline board composition and readiness caching preserve operator admission.

Pages and current job rows are scoped repository facts. These tests exercise
Python ordering, document composition and cache identity, not SQL page selection.
"""

import copy
import hashlib
import uuid
from collections import OrderedDict
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_admin_api import article_pipeline as pipeline
from devfeed_admin_api import pipeline_reads
from devfeed_core import analysis
from devfeed_core.article_lifecycle import derive_publication_readiness
from devfeed_core.models import ArticleContent
from devfeed_core.urls import fingerprint
from sqlalchemy import select
from test_admin_editorial_policy_unit import operator_api
from test_article_readiness_unit import image_job
from test_article_readiness_unit import processing_case as processing_case
from test_source_readiness_unit import NOW

FACT_NAMES = ("content", "enrichment", "analysis", "discovery", "storage")


def history(base, label, *, status="ready", with_content=False, source_name=None):
    article, facts = copy.deepcopy(base)
    article.id = uuid.uuid4()
    article.canonical_url = f"https://publisher.example/{label}"
    article.url_hash = fingerprint(article.canonical_url)
    article.discovered_at = NOW - timedelta(hours=1)
    for origin in article.origins:
        origin.article_id = article.id
    for link in article.topic_links:
        link.article_id = article.id
    article.origins[0].entry_key = label
    article.origins[0].original_url = article.canonical_url
    if source_name is not None:
        article.origins[0].source.id = uuid.uuid4()
        article.origins[0].source_id = article.origins[0].source.id
        article.origins[0].source.name = source_name
    for name in ("analysis", "enrichment"):
        facts[name].id, facts[name].article_id = uuid.uuid4(), article.id
    facts["enrichment"].result = {"input_url": article.canonical_url, "image_url": None}
    if with_content:
        text = (
            "Angular router guards allow developers to control navigation "
            "with dependency injection."
        )
        facts["content"] = ArticleContent(
            article_id=article.id,
            url=article.canonical_url,
            text=text,
            content_hash=hashlib.sha256(text.encode()).hexdigest(),
            method="trafilatura",
            retrieved_at=NOW - timedelta(minutes=4),
        )
    snapshot = analysis.source_snapshot(article, facts["content"])
    candidates = analysis.analysis_candidates(facts["taxonomy"], snapshot)
    current = facts["analysis"]
    current.input_snapshot, current.input_hash = snapshot, analysis.snapshot_hash(snapshot)
    current.catalog_snapshot, current.catalog_hash = candidates, analysis.snapshot_hash(candidates)
    article.classification_provenance = {
        **article.classification_provenance,
        "analysis_id": str(current.id),
        "input_hash": current.input_hash,
    }
    if status != "ready":
        current.id = uuid.uuid4()  # A newer run leaves the previously applied receipt intact.
        current.status = (
            "queued"
            if status in {"waiting", "retry_wait"}
            else "failed"
            if status == "blocked"
            else "running"
        )
        current.outcome, current.finished_at, current.result = None, None, {}
        current.created_at = NOW - timedelta(minutes=1)
        current.available_at = (
            NOW + timedelta(minutes=2) if current.status == "queued" else NOW - timedelta(minutes=1)
        )
        current.attempts = 0 if status == "waiting" else 3 if status == "blocked" else 1
        current.lease_token, current.lease_until = (
            (uuid.uuid4(), NOW + timedelta(minutes=4))
            if current.status == "running"
            else (None, None)
        )
        current.error = "provider_unavailable" if status in {"blocked", "retry_wait"} else None
        if current.status == "failed":
            current.finished_at = NOW
    return article, facts


def fact_rows(histories):
    return {
        name: {article.id: facts[name] for article, facts in histories if facts[name] is not None}
        for name in FACT_NAMES
    }


def card(value):
    article, facts = value
    readiness = derive_publication_readiness(article, **facts)
    return pipeline._card(article, readiness, facts, taxonomy=facts["taxonomy"])


@pytest.fixture
def isolated_cache(monkeypatch):
    cache = OrderedDict()
    monkeypatch.setattr(pipeline, "_readiness_cache", cache)
    return cache


@pytest.mark.parametrize("lightweight", [False, True])
def test_current_fact_batch_keeps_private_evidence_separate_and_attaches_precomputed_retry_metadata(
    processing_case, lightweight
):
    article, facts = history(processing_case, "fact-batch", with_content=True, status="blocked")
    discovery = image_job(article, operation="discover", status="succeeded")
    article.image_url = "https://publisher.example/hero.png"
    storage = image_job(article, operation="store", status="failed")
    storage.attempts = 3
    storage.error = "origin_unavailable"
    rows = iter(
        [
            [(facts["enrichment"], False)],
            [(facts["analysis"], True, "topics")] if lightweight else [(facts["analysis"], True)],
            [(discovery, False)],
            [(storage, True)],
        ]
    )
    session = SimpleNamespace(scalars=lambda _: [facts["content"]], execute=lambda _: next(rows))
    result = pipeline._batch_facts(session, [article], lightweight=lightweight)
    assert result["content"] == {article.id: facts["content"]}
    assert result["analysis"][article.id] is facts["analysis"]
    assert result["analysis"][article.id]._pipeline_retryable
    assert result["discovery"][article.id] is discovery and result["storage"][article.id] is storage
    assert storage._pipeline_retryable and not discovery._pipeline_retryable
    assert getattr(facts["analysis"], "_pipeline_current_step", None) == (
        "topics" if lightweight else None
    )
    assert facts["content"].text.startswith("Angular router")


@pytest.mark.parametrize("with_content", [False, True])
def test_readiness_cache_reuses_unchanged_evidence_then_rechecks_changed_source_authority(
    monkeypatch, processing_case, isolated_cache, with_content
):
    article, facts = history(processing_case, "cache", with_content=with_content)
    supplied = fact_rows([(article, facts)])
    monkeypatch.setattr(pipeline, "_batch_facts", lambda *_args, **_kwargs: supplied)
    session = Mock()
    session.scalars.side_effect = lambda statement: (
        [facts["content"]]
        if statement.column_descriptions[0]["entity"] is ArticleContent
        else [facts["analysis"]]
    )
    options = dict(taxonomy=facts["taxonomy"], settings=facts["settings"], now=NOW)
    _, first = pipeline._batch_readiness(session, [article], **options)
    assert first[article.id].stage == "ready" and first[article.id].blockers == []
    hydration_count = session.scalars.call_count
    _, second = pipeline._batch_readiness(session, [article], **options)
    assert second[article.id] is first[article.id]
    assert session.scalars.call_count == hydration_count
    source = article.origins[0].source
    source.approval_status, source.enabled = "rejected", False
    source.reviewed_at, source.reviewed_by, source.review_note = (
        NOW,
        "editor",
        "Source no longer meets editorial policy.",
    )
    _, changed = pipeline._batch_readiness(session, [article], **options)
    assert changed[article.id] is not first[article.id]
    assert (
        changed[article.id].stage != "ready"
        and "no_approved_source" in changed[article.id].blockers
    )
    assert session.scalars.call_count > hydration_count


def test_readiness_cache_evicts_least_recently_used_documents_without_changing_admission(
    monkeypatch, processing_case, isolated_cache
):
    histories = [history(processing_case, f"bounded-{index}") for index in range(3)]
    monkeypatch.setattr(pipeline, "_CACHE_SIZE", 2)
    monkeypatch.setattr(
        pipeline,
        "_batch_facts",
        lambda _session, articles, **_: fact_rows(
            [value for value in histories if value[0].id in {article.id for article in articles}]
        ),
    )
    session = Mock()
    session.scalars.return_value = [value[1]["analysis"] for value in histories]

    def evaluate(index):
        article, facts = histories[index]
        return pipeline._batch_readiness(
            session, [article], taxonomy=facts["taxonomy"], settings=facts["settings"], now=NOW
        )[1][article.id]

    first, second = evaluate(0), evaluate(1)
    assert evaluate(0) is first  # Touch the first entry before admitting a third.
    assert evaluate(2).stage == "ready" and len(isolated_cache) == 2
    assert {key[0] for key in isolated_cache} == {histories[0][0].id, histories[2][0].id}
    assert evaluate(1).stage == "ready" and evaluate(1) is not second


def test_active_card_stream_releases_each_completed_page_and_uses_real_admission(
    monkeypatch, processing_case, isolated_cache
):
    histories = [history(processing_case, f"stream-{index}") for index in range(2)]
    monkeypatch.setattr(pipeline, "BATCH_SIZE", 1)
    monkeypatch.setattr(
        pipeline,
        "_batch_facts",
        lambda _session, articles, **_: fact_rows(
            [value for value in histories if value[0].id == articles[0].id]
        ),
    )
    session = Mock()
    session.scalars.side_effect = [
        [histories[0][0]],
        [histories[0][1]["analysis"]],
        [histories[1][0]],
        [histories[1][1]["analysis"]],
        [],
    ]
    facts = histories[0][1]
    cards = list(
        pipeline._active_cards(
            session,
            select(pipeline.Article),
            taxonomy=facts["taxonomy"],
            settings=facts["settings"],
            now=NOW,
        )
    )
    assert [item.id for item in cards] == [article.id for article, _ in histories]
    assert all(item.stage == "ready" and item.blockers == [] for item in cards)
    assert session.expunge_all.call_count == 2


@pytest.fixture
def board_stream(monkeypatch, processing_case):
    values = [
        history(processing_case, f"board-{index}", status=status, source_name=name)
        for index, (status, name) in enumerate(
            [
                ("waiting", "Alpha"),
                ("blocked", "Zulu"),
                ("running", "Alpha"),
                ("ready", "Ready source"),
                ("running", "Beta"),
                ("retry_wait", "Zulu"),
            ]
        )
    ]
    # Shared source identities reflect distinct articles from the same publisher.
    for first, second in ((0, 2), (1, 5)):
        values[second][0].origins[0].source = values[first][0].origins[0].source
        values[second][0].origins[0].source_id = values[first][0].origins[0].source.id
    cards = [card(value) for value in values]
    monkeypatch.setattr(pipeline, "_active_cards", lambda *_args, **_kwargs: iter(cards))
    monkeypatch.setattr(pipeline, "catalog", lambda _: processing_case[1]["taxonomy"])
    monkeypatch.setattr(pipeline, "utcnow", lambda: NOW)
    return cards


@pytest.mark.parametrize(
    "cursor,indices,next_cursor",
    [(0, [2, 4], 2), (1, [4, 0], 3), (2, [0, 1], 4), (4, [5], None), (8, [], None)],
)
def test_offline_lane_pages_running_work_before_fifo_waiting_and_blocked_cards(
    board_stream, cursor, indices, next_cursor
):
    result = pipeline._board(
        object(),
        q=None,
        source_id=None,
        needs_attention=False,
        limit=2,
        stage="analysis",
        cursor=cursor,
    )
    [column] = result.columns
    assert column.total == result.counts["analysis"] == 5
    assert [item.id for item in column.items] == [board_stream[index].id for index in indices]
    assert column.next_cursor == next_cursor
    assert result.terminal_since == NOW - timedelta(days=7)


def test_attention_lane_counts_blocked_and_delayed_retries_without_active_or_ready_work(
    board_stream,
):
    result = pipeline._board(
        object(), q=None, source_id=None, needs_attention=True, limit=20, stage="analysis"
    )
    [column] = result.columns
    assert column.total == 2
    assert [item.id for item in column.items] == [board_stream[1].id, board_stream[5].id]
    assert [item.state for item in column.items] == ["blocked", "retry_wait"]


def test_grouped_lane_counts_articles_but_pages_prioritized_publishers(board_stream):
    result = pipeline._board(
        object(),
        q=None,
        source_id=None,
        needs_attention=False,
        limit=2,
        stage="analysis",
        group_by="source",
    )
    [column] = result.columns
    assert column.total == 5 and column.items == [] and column.next_cursor == 2
    assert [(group.source_name, group.total) for group in column.source_groups] == [
        ("Alpha", 2),
        ("Beta", 1),
    ]
    assert column.source_groups[0].state_counts == {"waiting": 1, "running": 1}
    second = pipeline._board(
        object(),
        q=None,
        source_id=None,
        needs_attention=False,
        limit=2,
        stage="analysis",
        cursor=2,
        group_by="source",
    ).columns[0]
    assert [(group.source_name, group.total) for group in second.source_groups] == [("Zulu", 2)]
    assert second.source_groups[0].state_counts == {"blocked": 1, "retry_wait": 1}
    assert second.next_cursor is None


@pytest.mark.parametrize("stage", ["published", "rejected"])
@pytest.mark.parametrize("attention", [False, True])
def test_terminal_lane_keeps_current_terminal_documents_and_hides_them_from_attention(
    monkeypatch, processing_case, stage, attention
):
    article, facts = history(processing_case, f"terminal-{stage}")
    if stage == "published":
        article.publication_status, article.published_to_feed_at = (
            "published",
            NOW - timedelta(minutes=1),
        )
    else:
        article.review_status, article.editorial_revision = (
            "rejected",
            article.editorial_revision + 1,
        )
    batch = Mock(return_value=fact_rows([(article, facts)]))
    monkeypatch.setattr(pipeline, "_batch_facts", batch)
    session = Mock()
    session.scalar.return_value, session.scalars.return_value = 7, [article]
    result = pipeline._terminal_column(
        session,
        select(pipeline.Article),
        stage,
        since=NOW - timedelta(days=7),
        limit=2,
        cursor=4,
        needs_attention=attention,
        taxonomy=facts["taxonomy"],
    )
    if attention:
        assert result.total == 0 and result.items == [] and result.next_cursor is None
        session.scalar.assert_not_called()
        session.scalars.assert_not_called()
    else:
        assert result.total == 7 and result.next_cursor == 6
        assert result.items[0].id == article.id and result.items[0].stage == stage
        assert result.items[0].state == "complete" and not result.items[0].allowed_actions
        assert batch.call_count == (1 if stage == "published" else 0)


def test_terminal_source_group_page_retains_terminal_totals_and_alphabetical_unknown_order():
    alpha, beta = uuid.uuid4(), uuid.uuid4()
    session = SimpleNamespace(
        execute=lambda _: [(None, None, 2), (beta, "Beta", 3), (alpha, "Alpha", 4)]
    )
    result = pipeline._terminal_column(
        session,
        select(pipeline.Article),
        "published",
        since=NOW - timedelta(days=7),
        limit=2,
        cursor=1,
        needs_attention=False,
        taxonomy={},
        group_by="source",
    )
    assert result.total == 9 and result.next_cursor is None and result.items == []
    assert [(group.source_name, group.total) for group in result.source_groups] == [
        ("Beta", 3),
        (None, 2),
    ]
    assert result.source_groups[0].state_counts == {"complete": 3}


@pytest.mark.parametrize("column", [False, True])
def test_http_board_passes_requested_scope_to_saved_reads_and_releases_session(
    monkeypatch, processing_case, column
):
    displayed = card(history(processing_case, "http-board"))
    result = pipeline.PipelineBoard(
        as_of=NOW,
        terminal_since=NOW - timedelta(days=7),
        counts={"ready": 1},
        columns=[
            pipeline.PipelineColumn(stage="ready", total=1, items=[displayed], next_cursor=None)
        ],
    )
    read = Mock(return_value=result)
    monkeypatch.setattr(pipeline_reads, "article_board", read)
    with operator_api(pipeline) as api:
        params = dict(
            q="Angular",
            source_id=str(displayed.source_id),
            needs_attention="false",
            limit=3,
            group_by="source",
        )
        if column:
            params.update(cursor=2, source_group="unknown")
        response = api.client.get(
            "/v1/admin/article-pipeline" + ("/ready" if column else ""), params=params
        )
        assert response.status_code == 200
        output = response.json() if column else response.json()["columns"][0]
        assert output["items"][0]["id"] == str(displayed.id) and output["total"] == 1
        assert read.call_args.args == (api.session,)
        assert read.call_args.kwargs == dict(
            q="Angular",
            source_id=displayed.source_id,
            needs_attention=False,
            limit=3,
            group_by="source",
            **dict(stage="ready", cursor=2, source_group="unknown") if column else {},
        )
        api.session.close.assert_called_once_with()
