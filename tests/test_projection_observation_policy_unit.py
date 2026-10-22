"""Read projections retain complete series and invalidate only affected public data.

Aggregate rows and state writes are database boundaries; SQL aggregation and
commit visibility are covered separately. ORM change histories below are real
SQLAlchemy states exercised without a database connection or flush.
"""

import copy
import uuid
from datetime import UTC, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import cache_events, inference_metrics, read_models
from devfeed_core.article_lifecycle import derive_publication_readiness
from devfeed_core.models import Article, ArticleTag, ReadModelEvent, ReadModelState
from devfeed_core.urls import fingerprint
from sqlalchemy import select, update
from sqlalchemy.orm import make_transient_to_detached
from taxonomy_command_helpers import NOW
from test_analysis_admission_contracts_unit import admission as admission
from test_partner_matching_workflow_unit import product
from test_partner_placement_workflow_unit import account, asset, campaign


@pytest.mark.parametrize("rollup", [False, True])
@pytest.mark.parametrize("naive", [False, True])
def test_inference_chart_preserves_preaggregated_counts_and_fills_inactive_calendar_days(
    rollup, naive
):
    first, last = NOW - timedelta(days=2), NOW
    if naive:
        first, last = first.replace(tzinfo=None), last.replace(tzinfo=None)
    key = (NOW - timedelta(days=1)).date().isoformat()
    point = dict(
        date=key,
        operation="article_analysis",
        model="unit-model",
        effort="medium",
        calls=3,
        returned=1,
        failed=1,
        running=1,
        unreported=1,
        input_tokens=100,
        cached_input_tokens=30,
        output_tokens=40,
        reasoning_tokens=10,
        web_searches=1,
        duration_ms=1200,
    )
    session = Mock()
    session.scalar.side_effect = [rollup, NOW - timedelta(days=10)]
    session.execute.side_effect = [
        SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: [point])),
        SimpleNamespace(mappings=lambda: [{"date": key, "approved": 2, "rejected": 1}]),
        SimpleNamespace(
            mappings=lambda: [{"date": key, "calls": 4, "escalations": 1, "repeated_stages": 1}]
        ),
        SimpleNamespace(mappings=lambda: [{"date": key, "count": 1}]),
    ]
    report = inference_metrics.inference_charts(session, first, last)
    assert report.first_recorded_at == NOW - timedelta(days=10)
    assert [row.model_dump() for row in report.activity] == [point]
    days = report.topic_activity
    assert [day.date for day in days] == [
        (NOW - timedelta(days=n)).date().isoformat() for n in (2, 1, 0)
    ]
    assert days[1].model_dump() == dict(
        date=key, approved=2, rejected=1, deferred=1, calls=4, escalations=1, repeated_stages=1
    )
    assert all(
        value == 0
        for day in (days[0], days[2])
        for field, value in day.model_dump().items()
        if field != "date"
    )
    parameters = session.execute.call_args_list[0].args[1]
    assert parameters["start"] == first and parameters["now"] == last
    if rollup:
        assert parameters["first_full_day"] == (NOW - timedelta(days=1)).replace(hour=0)
        assert parameters["last_day"] == NOW.replace(hour=0)
        assert parameters["first_full_day"].tzinfo == UTC


def test_read_model_event_boundary_keeps_tombstones_and_acknowledges_only_captured_ids():
    session = Mock()
    entity_id, cursor = uuid.uuid4(), uuid.uuid4()
    event = read_models.enqueue_event(session, "source_fanout", entity_id, after_id=cursor)
    assert isinstance(event, ReadModelEvent)
    assert (
        event.kind == "source_fanout" and event.entity_id == entity_id and event.after_id == cursor
    )
    session.add.assert_called_once_with(event)
    assert read_models.acknowledge_events(session, iter(())) == 0
    session.execute.assert_not_called()
    session.execute.return_value.rowcount = 2
    assert read_models.acknowledge_events(session, iter([7, 11])) == 2
    assert session.execute.call_args.args[0].compile().params == {"id_1": [7, 11]}


@pytest.mark.parametrize("limit", [0, 1])
def test_event_batch_refuses_empty_work_budget_and_preserves_repository_order(limit):
    session = Mock()
    rows = [ReadModelEvent(id=7, kind="catalog")]
    session.scalars.return_value = rows
    if limit == 0:
        with pytest.raises(ValueError, match="must be positive"):
            read_models.event_batch(session, limit)
        session.scalars.assert_not_called()
    else:
        assert read_models.event_batch(session, limit) == rows


@pytest.mark.parametrize("loaded", [False, True])
def test_state_upsert_expires_an_already_loaded_generation_without_changing_unrelated_fields(
    monkeypatch,
    loaded,
):
    session, name = Mock(), "article-pipeline"
    state = ReadModelState(name=name, payload={"generation": "old"}, updated_at=NOW)
    session.identity_key.return_value = (ReadModelState, (name,), None)
    session.identity_map = {(ReadModelState, (name,), None): state} if loaded else {}
    session.get.return_value = state if loaded else None
    monkeypatch.setattr(read_models, "utcnow", lambda: NOW)
    assert read_models.get_state(session, name) is (state if loaded else None)
    read_models.set_state(session, name, {"generation": "new", "processed": 4})
    values = session.execute.call_args.args[0].compile().params
    assert values == {
        "name": name,
        "payload": {"generation": "new", "processed": 4},
        "updated_at": NOW,
    }
    if loaded:
        session.expire.assert_called_once_with(state, ["payload", "updated_at"])
    else:
        session.expire.assert_not_called()


@pytest.mark.parametrize("change", ["new", "tier", "link_rel", "name"])
def test_partner_policy_object_history_invalidates_public_sponsor_fields_only(change):
    current = account()
    with cache_events.AppSession() as session:
        if change != "new":
            make_transient_to_detached(current)
        session.add(current)
        if change == "tier":
            current.tier = "gold"
        elif change == "link_rel":
            current.link_rel = ["sponsored"]
        elif change == "name":
            current.name = "Renamed private billing account"
        cache_events.track_objects(session, None, None)
        assert bool(session.info.get(cache_events.DIRTY)) is (change != "name")
        if change != "name":
            assert session.info[cache_events.REASONS] == {"partner_accounts"}


@pytest.mark.parametrize("kind", ["campaign", "asset"])
@pytest.mark.parametrize("action", ["new", "delete", "unchanged"])
def test_public_sponsorship_associations_observe_real_orm_creation_and_deletion(kind, action):
    owner, value, article_id = account(), product(), uuid.uuid4()
    agreement = campaign(owner, ad_format="sponsored_content", article_id=article_id)
    current = (
        agreement
        if kind == "campaign"
        else asset(
            owner,
            value,
            agreement,
            ad_format="sponsored_content",
            article_id=article_id,
        )
    )
    with cache_events.AppSession() as session:
        if action != "new":
            make_transient_to_detached(current)
        session.add(current)
        if action == "delete":
            session.delete(current)
        cache_events.track_objects(session, None, None)
        assert bool(session.info.get(cache_events.DIRTY)) is (action != "unchanged")
        if action != "unchanged":
            assert session.info[cache_events.REASONS] == {
                "partner_campaigns" if kind == "campaign" else "partner_assets"
            }


@pytest.mark.parametrize("private", [False, True])
def test_article_tag_history_keeps_private_processing_out_of_public_invalidation(private):
    current = ArticleTag(article_id=uuid.uuid4(), tag_id=uuid.uuid4(), origin="source")
    with cache_events.AppSession() as session:
        if private:
            session.info[cache_events.PRIVATE_ARTICLES] = {current.article_id}
        session.add(current)
        cache_events.track_objects(session, None, None)
        assert bool(session.info.get(cache_events.DIRTY)) is (not private)


@pytest.mark.parametrize("field", ["summary", "editorial_revision"])
def test_public_article_prose_invalidates_but_private_review_bookkeeping_does_not(field, admission):
    completed = admission.article
    completed.review_status, completed.editorial_revision = "approved", 2
    completed.url_hash = fingerprint(completed.canonical_url)
    assert derive_publication_readiness(completed, **admission.facts).stage == "ready"
    completed.publication_status, completed.published_to_feed_at = (
        "published",
        admission.facts["now"],
    )
    # The observer sees a loaded column projection. Keep associations unloaded,
    # while retaining the validated publication receipt and its identities.
    current = Article(
        **{
            column.name: copy.deepcopy(getattr(completed, column.name))
            for column in Article.__table__.columns
        }
    )
    make_transient_to_detached(current)
    with cache_events.AppSession() as session:
        session.add(current)
        setattr(
            current,
            field,
            "Updated source-supported navigation details." if field == "summary" else 3,
        )
        cache_events.track_objects(session, None, None)
        assert bool(session.info.get(cache_events.DIRTY)) is (field == "summary")


@pytest.mark.parametrize("operation", ["read", "private-write"])
def test_statement_observer_ignores_reads_and_explicit_private_write_operations(operation):
    state = SimpleNamespace(
        is_insert=False,
        is_update=operation == "private-write",
        is_delete=False,
        statement=select(Article) if operation == "read" else update(Article),
        execution_options={"devfeed_private_write": True} if operation == "private-write" else {},
        update_execution_options=Mock(),
    )
    cache_events.track_statements(state)
    state.update_execution_options.assert_not_called()
