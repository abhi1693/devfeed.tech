"""Publisher plans preserve bounded work, complete serving policies and lease ownership.

Repository reads are preselected facts. Database filtering, locking and atomic
publication remain integration contracts; these units exercise the Python plan.
"""

import copy
import uuid
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_admin_api import pipeline_projection as projections
from devfeed_core.models import PipelineProjection, ReadModelEvent, ReadModelState

from tests.support.article_readiness import processing_case as processing_case
from tests.support.source_readiness import NOW

POLICY = "current-policy"
TOKEN = "publisher-token"
REVISION = [["tags", "4"], ["topics", "7"]]


def state(**changes):
    payload = {
        "target_policy": POLICY,
        "serving_policy": "previous-policy",
        "serving_generated_at": (NOW - timedelta(minutes=5)).isoformat(),
        "pending_backfills": ["article", "source", "candidate"],
        "backfill_revision": copy.deepcopy(REVISION),
        "backfill_started_at": (NOW - timedelta(minutes=1)).isoformat(),
        "backfill_complete": False,
        "backfill_progress": False,
        "policy_replay_required": False,
        "obsolete_policies": ["previous-policy"],
    }
    return ReadModelState(name="pipeline", payload={**payload, **changes}, updated_at=NOW)


def event(identifier, kind, entity=None, after=None):
    return ReadModelEvent(
        id=identifier, kind=kind, entity_id=entity, after_id=after, created_at=NOW
    )


def read_session(stored, pages=()):
    session = Mock()
    session.get.return_value = stored
    session.scalars.side_effect = list(pages)
    return session


@pytest.mark.parametrize("target", ["article", "source", "candidate"])
@pytest.mark.parametrize("full", [False, True])
def test_backfill_plan_continues_from_last_read_id_until_entity_coverage_finishes(target, full):
    identifiers = [uuid.UUID(int=101), uuid.UUID(int=102)] if full else [uuid.UUID(int=101)]
    incoming = event(8, f"backfill_{target}", after=uuid.UUID(int=100))
    session = read_session(state(), [identifiers])

    work, finished, acknowledged, continuations, _ = projections._expand(
        session, [incoming], 2, NOW
    )

    assert work[target] == set(identifiers)
    assert sum(map(len, work.values())) == len(identifiers)
    assert acknowledged == [8]
    assert finished == (set() if full else {target})
    assert continuations == (
        [{"kind": f"backfill_{target}", "after_id": identifiers[-1]}] if full else []
    )


def test_source_fanout_coalesces_duplicate_article_events_and_preserves_continuation_owner():
    article, source, candidate, fanout = [uuid.UUID(int=index) for index in range(1, 5)]
    incoming = [
        event(1, "article", article),
        event(2, "article", article),
        event(3, "candidate", candidate),
        event(4, "source", source),
        event(5, "source_fanout", source, after=article),
    ]
    result = projections._expand(read_session(state(), [[fanout]]), incoming, 4, NOW)
    work, finished, acknowledged, continuations, _ = result
    assert work == {"article": {article, fanout}, "source": {source}, "candidate": {candidate}}
    assert finished == set() and acknowledged == [1, 2, 3, 4, 5]
    assert continuations == [{"kind": "source_fanout", "entity_id": source, "after_id": fanout}]


def test_expansion_capacity_leaves_unvisited_events_unacknowledged_for_the_next_delivery():
    identifiers = [uuid.uuid4(), uuid.uuid4()]
    work, _, acknowledged, continuations, _ = projections._expand(
        read_session(state()),
        [event(index + 1, "article", identifier) for index, identifier in enumerate(identifiers)],
        1,
        NOW,
    )
    assert work["article"] == {identifiers[0]}
    assert acknowledged == [1] and continuations == []


@pytest.mark.parametrize("kind,progress", [("policy", False), ("policy", True), ("catalog", True)])
def test_invalidation_during_coverage_only_requires_global_policy_replay_after_progress(
    kind, progress
):
    stored = state(backfill_progress=progress)
    before = copy.deepcopy(stored.payload)
    _, _, acknowledged, continuations, planned = projections._expand(
        read_session(stored), [event(9, kind)], 3, NOW
    )
    assert planned["policy_replay_required"] is (kind == "policy" and progress)
    assert planned["backfill_started_at"] == before["backfill_started_at"]
    assert planned["pending_backfills"] == before["pending_backfills"]
    assert acknowledged == [9] and continuations == []
    assert stored.payload == before  # A plan is unpublished until its owner publishes it.


def test_policy_invalidation_starts_all_entity_passes_while_preserving_last_complete_serving():
    stored = state(pending_backfills=[], backfill_complete=True, backfill_progress=True)
    session = read_session(stored)
    session.execute.return_value = [("tags", 4), ("topics", 7)]
    _, _, acknowledged, continuations, planned = projections._expand(
        session, [event(10, "policy")], 10, NOW
    )
    assert planned["pending_backfills"] == ["article", "source", "candidate"]
    assert planned["backfill_revision"] == REVISION
    assert planned["backfill_started_at"] == NOW.isoformat()
    assert not planned["backfill_complete"] and not planned["backfill_progress"]
    assert not planned["policy_replay_required"]
    assert planned["serving_policy"] == "previous-policy"
    assert acknowledged == [10]
    assert continuations == [
        {"kind": f"backfill_{name}"} for name in ("article", "source", "candidate")
    ]


@pytest.mark.parametrize("count", [1, 2])
def test_catalog_refresh_is_bounded_article_work_without_restarting_coverage(count):
    stored = state(pending_backfills=[], backfill_complete=True, serving_policy=POLICY)
    original = copy.deepcopy(stored.payload)
    identifiers = [uuid.UUID(int=index) for index in range(1, count + 1)]
    session = read_session(stored)
    session.execute.side_effect = [
        [("tags", 4), ("topics", 7)],
        [(identifier, True) for identifier in identifiers],
    ]
    work, finished, acknowledged, continuations, planned = projections._expand(
        session, [event(10, "catalog")], 2, NOW
    )
    assert work == {"article": set(identifiers), "source": set(), "candidate": set()}
    assert planned["backfill_complete"] and planned["pending_backfills"] == []
    assert planned["backfill_started_at"] == original["backfill_started_at"]
    refresh = planned["catalog_refresh"]
    assert refresh["revision"] == REVISION and refresh["started_at"] == NOW.isoformat()
    assert acknowledged == [10] and stored.payload == original
    if count == 2:
        assert finished == set()
        assert continuations == [
            {
                "kind": "catalog",
                "entity_id": uuid.UUID(refresh["token"]),
                "after_id": identifiers[-1],
            }
        ]
    else:
        assert finished == {"catalog"} and continuations == []


def test_catalog_edits_during_refresh_coalesce_without_rewinding_its_cursor():
    refresh = {"token": str(uuid.uuid4()), "revision": REVISION, "started_at": NOW.isoformat()}
    stored = state(pending_backfills=[], backfill_complete=True, catalog_refresh=refresh)
    session = read_session(stored)
    work, finished, acknowledged, continuations, planned = projections._expand(
        session, [event(11, "catalog"), event(12, "catalog")], 2, NOW
    )
    assert not any(work.values()) and not finished and not continuations
    assert acknowledged == [11, 12] and planned["catalog_refresh"] == refresh
    session.scalars.assert_not_called()
    session.execute.assert_not_called()


@pytest.mark.parametrize("active", [False, True])
def test_obsolete_catalog_continuations_cannot_start_or_replace_a_refresh(active):
    changes = (
        {"catalog_refresh": {"token": str(uuid.uuid4()), "revision": REVISION}} if active else {}
    )
    stored = state(pending_backfills=[], backfill_complete=True, **changes)
    session = read_session(stored)
    work, finished, acknowledged, continuations, planned = projections._expand(
        session, [event(12, "catalog", uuid.uuid4(), uuid.uuid4())], 2, NOW
    )
    assert not any(work.values()) and not finished and not continuations
    assert acknowledged == [12] and planned == stored.payload
    session.scalars.assert_not_called()


def test_policy_invalidation_cancels_a_catalog_refresh_finished_in_the_same_batch():
    stored = state(pending_backfills=[], backfill_complete=True, serving_policy=POLICY)
    session = read_session(stored)
    session.execute.side_effect = [[("tags", 4), ("topics", 7)], [], [("tags", 4), ("topics", 7)]]
    _, finished, acknowledged, continuations, planned = projections._expand(
        session, [event(10, "catalog"), event(11, "policy")], 2, NOW
    )
    assert finished == set() and "catalog_refresh" not in planned
    assert acknowledged == [10, 11] and not planned["backfill_complete"]
    assert continuations == [
        {"kind": f"backfill_{name}"} for name in ("article", "source", "candidate")
    ]


def test_catalog_continuation_retains_its_owner_and_advances_without_restarting():
    token = uuid.uuid4()
    refresh = {"token": str(token), "revision": REVISION, "started_at": NOW.isoformat()}
    stored = state(pending_backfills=[], backfill_complete=True, catalog_refresh=refresh)
    identifiers = [uuid.UUID(int=index) for index in (11, 12)]
    session = read_session(stored)
    session.execute.return_value = [(identifier, True) for identifier in identifiers]
    work, finished, acknowledged, continuations, planned = projections._expand(
        session, [event(13, "catalog", token, uuid.UUID(int=10))], 2, NOW
    )
    assert work["article"] == set(identifiers) and not finished
    assert acknowledged == [13] and planned["catalog_refresh"] == refresh
    assert continuations == [{"kind": "catalog", "entity_id": token, "after_id": identifiers[-1]}]
    assert session.execute.call_count == 1  # Only the page; no catalog revision reload.


def test_catalog_pages_advance_through_insensitive_rows_and_preserve_unknown_dependencies():
    token = uuid.uuid4()
    refresh = {"token": str(token), "revision": REVISION, "started_at": NOW.isoformat()}
    stored = state(pending_backfills=[], backfill_complete=True, catalog_refresh=refresh)
    identifiers = [uuid.UUID(int=index) for index in range(1, 5)]
    cursor = None
    for page, expected in (
        ([(identifiers[0], False), (identifiers[1], False)], set()),
        ([(identifiers[2], None), (identifiers[3], True)], set(identifiers[2:])),
        ([], set()),
    ):
        session = read_session(stored)
        session.execute.return_value = page
        work, finished, acknowledged, continuations, _ = projections._expand(
            session, [event(12, "catalog", token, cursor)], 2, NOW
        )
        assert work["article"] == expected and acknowledged == [12]
        query = session.execute.call_args.args[0]
        assert query._limit_clause.value == 2
        assert "payload" not in str(query.whereclause)
        if page:
            assert not finished
            cursor = page[-1][0]
            assert continuations == [{"kind": "catalog", "entity_id": token, "after_id": cursor}]
        else:
            assert finished == {"catalog"} and not continuations


def test_insensitive_catalog_page_consumes_scan_budget_without_acknowledging_later_work():
    token = uuid.uuid4()
    stored = state(
        pending_backfills=[],
        backfill_complete=True,
        catalog_refresh={"token": str(token), "revision": REVISION},
    )
    identifier = uuid.uuid4()
    session = read_session(stored)
    session.execute.return_value = [(identifier, False)]
    work, _, acknowledged, continuations, _ = projections._expand(
        session, [event(10, "catalog", token), event(11, "article", uuid.uuid4())], 1, NOW
    )
    assert not any(work.values()) and acknowledged == [10]
    assert continuations == [{"kind": "catalog", "entity_id": token, "after_id": identifier}]
    assert session.execute.call_count == 1


@pytest.mark.parametrize("existing", [None, "older-target", POLICY])
def test_initialization_retains_serving_pointer_and_rebuilds_only_a_changed_policy(
    monkeypatch, existing
):
    stored = None if existing is None else state(target_policy=existing)
    session = read_session(stored)
    session.execute.return_value = [("tags", 4), ("topics", 7)]
    save, enqueue = Mock(), Mock()
    monkeypatch.setattr(projections, "set_state", save)
    monkeypatch.setattr(projections, "enqueue_event", enqueue)
    projections._initialize(session, POLICY, NOW)
    if existing == POLICY:
        save.assert_not_called()
        enqueue.assert_not_called()
        return
    payload = save.call_args.args[2]
    assert payload["target_policy"] == POLICY
    assert payload["pending_backfills"] == ["article", "source", "candidate"]
    assert payload["backfill_revision"] == REVISION and not payload["backfill_complete"]
    assert payload["obsolete_policies"] == (
        [] if stored is None else ["older-target", "previous-policy"]
    )
    if stored is not None:
        assert payload["serving_policy"] == "previous-policy"
        assert payload["serving_generated_at"] == stored.payload["serving_generated_at"]
    assert [call.args[1] for call in enqueue.call_args_list] == [
        "backfill_article",
        "backfill_source",
        "backfill_candidate",
    ]


def factory_for(session):
    @contextmanager
    def begin():
        yield session

    return SimpleNamespace(begin=begin)


@pytest.mark.parametrize(
    "owner,delta,accepted",
    [("other", 1, False), (TOKEN, 1, True), ("other", -1, True), (None, 0, False)],
)
def test_lease_renewal_respects_another_live_publisher_and_can_recover_expired_ownership(
    owner, delta, accepted
):
    current = (
        None
        if owner is None
        else ReadModelState(
            name="pipeline-worker",
            payload={"token": owner, "expires_at": (NOW + timedelta(seconds=delta)).isoformat()},
        )
    )
    session = Mock()
    session.scalar.return_value = current
    assert projections._lease(factory_for(session), TOKEN, NOW) is accepted
    if accepted:
        assert current.payload == {
            "token": TOKEN,
            "expires_at": (NOW + timedelta(seconds=120)).isoformat(),
        }
        assert current.updated_at == NOW
    elif current is not None:
        assert current.payload["token"] == owner


@pytest.mark.parametrize(
    "owner,delta,owned",
    [(TOKEN, 1, True), (TOKEN, 0, False), ("replacement", 60, False), (None, 0, False)],
)
def test_publication_ownership_requires_the_current_unexpired_token(owner, delta, owned):
    session = Mock()
    session.scalar.return_value = (
        None
        if owner is None
        else ReadModelState(
            name="pipeline-worker",
            payload={"token": owner, "expires_at": (NOW + timedelta(seconds=delta)).isoformat()},
        )
    )
    assert projections._owns_lease(session, TOKEN, NOW) is owned


@pytest.fixture
def publisher(monkeypatch):
    stored = state()
    lease = ReadModelState(
        name="pipeline-worker",
        payload={"token": TOKEN, "expires_at": (NOW + timedelta(minutes=2)).isoformat()},
    )
    session = Mock()
    session.get.return_value = stored
    session.scalars.return_value = []
    session.scalar.side_effect = lambda statement: (
        None if "read_model_events" in str(statement) else lease
    )
    selected = iter([])
    writes = []

    # Result rows are supplied in query order, with no emulation of SQL predicates.
    class Rows:
        def __init__(self, rows):
            self.rows = rows

        def __iter__(self):
            return iter(self.rows)

        def scalars(self):
            return self.rows

    def execute(statement, *parameters):
        if statement.is_select:
            return Rows(next(selected))
        writes.append((statement, parameters))
        return None

    session.execute.side_effect = execute
    opened = []

    @contextmanager
    def begin():
        assert not opened
        opened.append(True)
        try:
            yield session
        finally:
            opened.pop()

    factory = SimpleNamespace(begin=begin)
    enqueue, acknowledge, save = Mock(), Mock(), Mock()
    lease_boundary = Mock(return_value=True)
    article_rows, source_rows = Mock(return_value=[]), Mock(return_value=[])
    monkeypatch.setattr(projections, "_lease", lease_boundary)
    monkeypatch.setattr(projections, "pipeline_policy_version", lambda: POLICY)
    monkeypatch.setattr(projections, "utcnow", lambda: NOW)
    monkeypatch.setattr(projections, "event_batch", lambda *_: incoming)
    monkeypatch.setattr(projections, "enqueue_event", enqueue)
    monkeypatch.setattr(projections, "acknowledge_events", acknowledge)
    monkeypatch.setattr(projections, "set_state", save)
    monkeypatch.setattr(projections, "article_rows", article_rows)
    monkeypatch.setattr(projections, "source_rows", source_rows)
    incoming = []

    def reads(*rows):
        nonlocal selected
        selected = iter(rows)

    return SimpleNamespace(
        stored=stored,
        lease=lease,
        session=session,
        factory=factory,
        incoming=incoming,
        enqueue=enqueue,
        acknowledge=acknowledge,
        save=save,
        lease_boundary=lease_boundary,
        article_rows=article_rows,
        source_rows=source_rows,
        writes=writes,
        reads=reads,
        opened=opened,
    )


def publish(value, **kwargs):
    return projections.project_batch(value.factory, token=TOKEN, now=NOW, **kwargs)


@pytest.mark.parametrize("changed", [False, True])
def test_completed_catalog_refresh_only_requeues_dependent_work_after_new_edits(publisher, changed):
    value = publisher
    token = uuid.uuid4()
    value.stored.payload.update(
        serving_policy=POLICY,
        pending_backfills=[],
        backfill_complete=True,
        catalog_refresh={"token": str(token), "revision": REVISION, "started_at": NOW.isoformat()},
    )
    value.incoming.append(event(12, "catalog", token, uuid.uuid4()))
    value.reads([], [("tags", 4), ("topics", 8 if changed else 7)], [], [])
    assert publish(value) == 1
    saved = value.save.call_args.args[2]
    assert saved["backfill_complete"] and saved["pending_backfills"] == []
    assert "catalog_refresh" not in saved
    value.acknowledge.assert_called_once_with(value.session, [12])
    if changed:
        value.enqueue.assert_called_once_with(value.session, "catalog")
    else:
        value.enqueue.assert_not_called()


def test_busy_publisher_does_not_read_events_or_evaluate_cards(publisher):
    publisher.lease_boundary.return_value = False
    assert publish(publisher) == 0
    publisher.article_rows.assert_not_called()
    publisher.source_rows.assert_not_called()
    publisher.acknowledge.assert_not_called()
    publisher.session.get.assert_not_called()


@pytest.mark.parametrize("failure", [False, True])
def test_lost_ownership_or_failed_evaluation_cannot_acknowledge_the_captured_events(
    publisher, failure
):
    value = publisher
    value.incoming.append(event(11, "article", uuid.uuid4()))
    if failure:
        value.article_rows.side_effect = ValueError("Invalid private evidence")
        # Only final cleanup checks ownership after the evaluator raises.
        value.session.scalar.side_effect = [value.lease]
        with pytest.raises(ValueError, match="private evidence"):
            publish(value)
    else:
        replacement = ReadModelState(
            name="pipeline-worker",
            payload={
                "token": "replacement",
                "expires_at": (NOW + timedelta(minutes=2)).isoformat(),
            },
        )
        value.session.scalar.side_effect = [replacement, replacement]
        assert publish(value) == 0
    value.acknowledge.assert_not_called()
    value.save.assert_not_called()
    assert not any(statement.is_insert or statement.is_delete for statement, _ in value.writes)


def test_due_timer_becomes_event_and_is_cleared_before_card_evaluation(publisher):
    value = publisher
    due = PipelineProjection(
        policy_version=POLICY, kind="source", entity_id=uuid.uuid4(), next_transition_at=NOW
    )
    value.session.scalars.return_value = [due]
    value.stored.payload.update(backfill_complete=True, pending_backfills=[], serving_policy=POLICY)
    value.reads([], [])  # No obsolete policy rows or expired terminal article rows.
    assert publish(value) == 0
    value.enqueue.assert_called_once_with(value.session, "source", due.entity_id)
    assert due.next_transition_at is None
    assert value.save.call_args.args[2]["serving_generated_at"] == NOW.isoformat()


@pytest.mark.parametrize("replay", ["unchanged", "catalog", "policy", "orphans", "pending-event"])
def test_policy_cutover_waits_for_cleanup_then_replays_changed_inputs_without_withholding_coverage(
    publisher, replay
):
    value = publisher
    value.stored.payload.update(pending_backfills=[], policy_replay_required=replay == "policy")
    obsolete = [("article", uuid.uuid4())] if replay == "orphans" else []
    if replay == "pending-event":
        value.session.scalar.side_effect = [value.lease, 99, value.lease]
        value.reads()
    elif obsolete:
        value.reads(obsolete)
    else:
        revisions = [("tags", 4), ("topics", 8 if replay == "catalog" else 7)]
        value.reads([], revisions, [], [])
    assert publish(value) == len(obsolete)
    payload = value.save.call_args.args[2]
    if replay in {"orphans", "pending-event"}:
        assert payload["serving_policy"] == "previous-policy" and not payload["backfill_complete"]
        value.enqueue.assert_not_called()
    else:
        assert payload["serving_policy"] == POLICY
        assert payload["serving_generated_at"] == NOW.isoformat()
        assert payload["backfill_complete"] is (replay != "policy")
        assert payload["pending_backfills"] == (
            ["article", "source", "candidate"] if replay == "policy" else []
        )
        assert value.enqueue.call_count == {"unchanged": 0, "catalog": 1, "policy": 3}[replay]
        if replay == "catalog":
            value.enqueue.assert_called_once_with(value.session, "catalog")
        if replay == "policy":
            assert payload["backfill_started_at"] == NOW.isoformat()
            assert not payload["backfill_progress"] and not payload["policy_replay_required"]


def test_large_event_batch_evaluates_articles_in_bounded_chunks_and_keeps_exact_acknowledgements(
    publisher,
):
    value = publisher
    identifiers = [uuid.UUID(int=index) for index in range(1, 202)]
    value.incoming.append(event(42, "backfill_article"))
    value.stored.payload["pending_backfills"] = ["article"]
    value.session.scalars.side_effect = [[], identifiers]
    value.reads([], [("tags", 4), ("topics", 7)], [], [])

    def evaluate(factory, page, policy, now):
        assert not value.opened and factory is value.factory
        assert policy == POLICY and now == NOW
        return []  # The authoritative rows were removed after the captured invalidation.

    value.article_rows.side_effect = evaluate
    assert publish(value, batch=500) == 1
    assert [len(call.args[1]) for call in value.article_rows.call_args_list] == [200, 1]
    assert [
        identifier for call in value.article_rows.call_args_list for identifier in call.args[1]
    ] == identifiers
    value.acknowledge.assert_called_once_with(value.session, [42])
    assert value.save.call_args.args[2]["backfill_complete"]


def test_retention_bookkeeping_counts_only_supplied_obsolete_and_expired_rows(publisher):
    value = publisher
    value.stored.payload.update(backfill_complete=True, pending_backfills=[], serving_policy=POLICY)
    old = [("previous-policy", "article", uuid.uuid4())]
    expired = [uuid.uuid4(), uuid.uuid4()]
    value.reads(old, expired)
    assert publish(value) == 3
    payload = value.save.call_args.args[2]
    assert payload["obsolete_policies"] == ["previous-policy"]
    assert payload["backfill_complete"] and payload["serving_policy"] == POLICY


def test_evaluated_card_document_is_forwarded_before_its_exact_event_is_acknowledged(
    publisher, processing_case
):
    from devfeed_admin_api.article_pipeline import _card
    from devfeed_core.article_lifecycle import derive_publication_readiness

    value = publisher
    article, facts = processing_case
    readiness = derive_publication_readiness(article, **facts)
    card = _card(article, readiness, facts, taxonomy=facts["taxonomy"])
    document = dict(
        policy_version=POLICY,
        kind="article",
        entity_id=article.id,
        payload=card.model_dump(mode="json"),
        stage=card.stage,
        state=card.state,
        source_id=card.source_id,
        source_name=card.source_name,
        created_at=article.discovered_at,
        activity_at=article.discovered_at,
        terminal_at=None,
        has_active_work=False,
        next_transition_at=None,
        evaluated_at=NOW,
    )
    value.incoming.append(event(88, "article", article.id))
    value.article_rows.return_value = [document]
    assert publish(value) == 1
    inserts = [statement.compile().params for statement, _ in value.writes if statement.is_insert]
    assert len(inserts) == 1
    assert all(inserts[0][f"{name}_m0"] == field for name, field in document.items())
    assert document["payload"]["id"] == str(article.id) and document["stage"] == "ready"
    value.acknowledge.assert_called_once_with(value.session, [88])
    saved = value.save.call_args.args[2]
    assert saved["backfill_progress"] and saved["serving_policy"] == "previous-policy"
