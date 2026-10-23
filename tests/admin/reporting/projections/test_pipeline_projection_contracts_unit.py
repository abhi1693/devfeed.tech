"""Projector documents compose preloaded evidence without retaining a SQL connection."""

import uuid
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest
from devfeed_admin_api import article_pipeline, automation
from devfeed_admin_api import pipeline_projection as projections
from devfeed_core.models import IngestionJob, ReadModelState, SourceDiscoveryJob

from tests.admin.sources.test_source_pipeline_contracts_unit import candidate, facts
from tests.support.article_readiness import processing_case as processing_case
from tests.support.source_readiness import NOW, job, source


@pytest.mark.parametrize("kind", ["active", "ingesting", "candidate"])
def test_source_documents_preserve_identity_activity_and_scheduled_transitions(monkeypatch, kind):
    current = source(approval="approved") if kind != "candidate" else None
    proposed = candidate() if kind == "candidate" else None
    queued = (
        job(
            SourceDiscoveryJob,
            candidate_id=proposed.id,
            stage="discover",
            available_at=NOW + timedelta(minutes=3),
        )
        if proposed is not None
        else job(IngestionJob, source_id=current.id, available_at=NOW + timedelta(minutes=3))
        if kind == "ingesting"
        else None
    )
    value = facts(
        current,
        candidate=proposed,
        jobs={"discovery" if proposed else "ingestion": queued} if queued else {},
    )
    loaded = iter([[current] if current is not None else [], [proposed] if proposed else []])
    session = SimpleNamespace(scalars=lambda _: next(loaded))

    @contextmanager
    def factory():
        yield session

    monkeypatch.setattr(
        projections, "source_readiness_batch", lambda *args, **kwargs: {value.key: value}
    )

    rows = projections.source_rows(
        factory,
        [current.id] if current is not None else [],
        [proposed.id] if proposed else [],
        "policy-generation",
        NOW,
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["policy_version"] == "policy-generation" and row["evaluated_at"] == NOW
    assert row["entity_id"] == (current.id if current is not None else proposed.id)
    assert row["kind"] == ("source" if current is not None else "candidate")
    assert (
        row["stage"]
        == {"active": "active", "ingesting": "ingestion", "candidate": "discovery"}[kind]
    )
    assert row["payload"]["snapshot"] == value.snapshot
    assert row["has_active_work"] is (queued is not None)
    assert row["next_transition_at"] == (queued.available_at if queued else None)
    assert row["terminal_at"] == (current.updated_at if kind == "active" else None)
    assert (current.name if current is not None else proposed.name) in row["search_text"]
    assert (current.feed_url if current is not None else proposed.identity_url) in row[
        "search_text"
    ]


def test_empty_source_batch_does_not_open_a_read_session():
    def forbidden():
        pytest.fail("Empty projection batch touched the database")

    assert projections.source_rows(forbidden, [], [], "policy", NOW) == []


def test_next_transition_ignores_active_running_and_already_due_work():
    identifier = uuid.uuid4()
    future = job(IngestionJob, source_id=identifier, available_at=NOW + timedelta(minutes=4))
    earlier = job(IngestionJob, source_id=uuid.uuid4(), available_at=NOW + timedelta(minutes=2))
    due = job(IngestionJob, source_id=uuid.uuid4(), available_at=NOW)
    running = job(IngestionJob, source_id=uuid.uuid4(), status="running")
    assert (
        projections._next_transition([None, due, running, future, earlier], NOW)
        == earlier.available_at
    )
    assert projections._next_transition([None, due, running], NOW) is None


@pytest.mark.parametrize(
    "state",
    [
        "unpublished",
        "published-recent",
        "published-old",
        "rejected-recent",
        "rejected-old",
        "rejected-no-review",
    ],
)
def test_article_projection_retains_only_recent_terminal_history_and_current_admission(
    monkeypatch, processing_case, state
):
    article, evidence = processing_case
    article.discovered_at = NOW - timedelta(days=10)
    terminal = NOW - timedelta(days=8 if state.endswith("old") else 2)
    if state.startswith("published"):
        article.publication_status = "published"
        article.published_to_feed_at = terminal
    if state.startswith("rejected"):
        article.review_status = "rejected"
    rejected = (
        [(article.id, terminal)]
        if state.startswith("rejected") and state != "rejected-no-review"
        else []
    )
    loaded = iter(
        [
            None,
            SimpleNamespace(all=lambda: [(article, "angular routing publisher")]),
            SimpleNamespace(all=lambda: rejected),
        ]
    )
    connection = {"open": False, "detached": False}
    session = SimpleNamespace(
        execute=lambda _: next(loaded),
        scalars=lambda _: [],
        expunge_all=lambda: connection.update(detached=True),
    )

    @contextmanager
    def factory():
        connection["open"] = True
        try:
            yield session
        finally:
            connection["open"] = False

    selected = []

    def batch(session, articles, **options):
        selected.extend(articles)
        assert options == {"lightweight": True}
        return {
            name: {article.id: evidence[name]} if evidence[name] is not None else {}
            for name in ("content", "enrichment", "analysis", "discovery", "storage")
        }

    monkeypatch.setattr(projections, "catalog", lambda _: evidence["taxonomy"])
    monkeypatch.setattr(article_pipeline, "_batch_facts", batch)
    monkeypatch.setattr(
        automation,
        "automation_blocker_rows",
        lambda *_: {article.id: []},
    )
    derive = projections.derive_publication_readiness

    def admission(*args, **kwargs):
        assert not connection["open"] and connection["detached"]
        return derive(*args, **kwargs)

    monkeypatch.setattr(projections, "derive_publication_readiness", admission)

    rows = projections.article_rows(factory, [article.id], "policy-generation", NOW)

    if state.endswith("old") or state == "rejected-no-review":
        assert rows == [] and selected == []
        return
    assert selected == [article] and len(rows) == 1
    row = rows[0]
    assert row["entity_id"] == article.id and row["kind"] == "article"
    assert (
        row["policy_version"] == "policy-generation"
        and row["search_text"] == "angular routing publisher"
    )
    assert row["terminal_at"] == (None if state == "unpublished" else terminal)
    assert (
        row["stage"]
        == {"unpublished": "ready", "published-recent": "published", "rejected-recent": "rejected"}[
            state
        ]
    )
    assert not row["has_active_work"] and row["next_transition_at"] is None
    assert row["payload"]["_source_ids"] == [str(article.origins[0].source_id)]
    assert row["payload"]["_search"]["canonical_url"] == article.canonical_url
    assert row["payload"]["_automation_blockers"] == []
    assert row["evaluated_at"] == NOW


def test_drain_returns_processed_total_only_after_backfill_completion(monkeypatch):
    batches, requested = iter([3, 2, 0]), []
    monkeypatch.setattr(
        projections,
        "project_batch",
        lambda factory, **options: requested.append(options) or next(batches),
    )

    @contextmanager
    def factory():
        yield SimpleNamespace(
            get=lambda *_: ReadModelState(name="pipeline", payload={"backfill_complete": True}),
            scalar=lambda _: None,
        )

    assert projections.drain_pipeline(factory, batch=5, max_batches=4) == 5
    assert requested == [{"batch": 5}] * 3


@pytest.mark.parametrize(
    "state,pending",
    [(None, None), ({"backfill_complete": False}, None), ({"backfill_complete": True}, 42)],
)
def test_idle_drain_does_not_claim_incomplete_or_pending_work_is_finished(
    monkeypatch, state, pending
):
    monkeypatch.setattr(projections, "project_batch", lambda *args, **kwargs: 0)

    @contextmanager
    def factory():
        yield SimpleNamespace(
            get=lambda *_: (
                ReadModelState(name="pipeline", payload=state) if state is not None else None
            ),
            scalar=lambda _: pending,
        )

    with pytest.raises(RuntimeError, match="retry the drain"):
        projections.drain_pipeline(factory, max_batches=2)


def test_drain_is_bounded_when_work_keeps_arriving(monkeypatch):
    requested = []
    monkeypatch.setattr(
        projections, "project_batch", lambda *args, **kwargs: requested.append(True) or 1
    )
    with pytest.raises(RuntimeError, match="bounded run"):
        projections.drain_pipeline(object(), max_batches=2)
    assert requested == [True, True]
