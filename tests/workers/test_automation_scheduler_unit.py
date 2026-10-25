"""Bounded orchestration over preselected repository rows, not SQL eligibility proofs."""

import uuid
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import article_jobs, automation_demands
from devfeed_core import automation_scheduler as scheduler
from devfeed_core.models import (
    Article,
    ArticleContent,
    ArticleOrigin,
    AutomationInvalidation,
    TopicProposal,
    TopicReanalysis,
)
from sqlalchemy.exc import OperationalError

from tests.support.source_readiness import NOW, configuration, source
from tests.workers.test_automation_events_unit import topic


class ReadFactory:
    def __init__(self, *sessions):
        self.sessions = iter(sessions)
        self.open = False

    @contextmanager
    def begin(self):
        self.open = True
        try:
            yield next(self.sessions)
        finally:
            self.open = False


@pytest.fixture
def runtime(monkeypatch):
    value = SimpleNamespace(
        settings=configuration(
            ai_enabled=True, auto_research_imports=False, auto_reanalyze_topics=False
        ),
    )
    monkeypatch.setattr(scheduler, "get_settings", lambda: value.settings)
    monkeypatch.setattr(scheduler, "utcnow", lambda: NOW)
    for name, result in (
        ("schedule_tag_topic_discovery", {"tags_scanned": 2}),
        ("schedule_source_admission", 3),
        ("schedule_article_automation", {"articles_checked": 4}),
        ("schedule_topic_corrections", 5),
        ("schedule_topic_branding", 6),
        ("schedule_relationship_coverage", {"relationship_jobs_scheduled": 7}),
        ("finalize_relationship_reviews", 8),
    ):
        boundary = Mock(return_value=result)
        monkeypatch.setattr(scheduler, name, boundary)
        setattr(value, name, boundary)
    value.research = Mock()
    value.analyze = Mock(return_value=object())
    value.authorized = Mock(return_value=True)
    monkeypatch.setattr(scheduler, "request_topic_analysis", value.research)
    monkeypatch.setattr(scheduler, "request_analysis", value.analyze)
    monkeypatch.setattr(article_jobs, "approved_sources", value.authorized)
    return value


def test_exact_identity_discovery_remains_available_with_the_ai_kill_switch(runtime):
    runtime.settings = configuration(ai_enabled=False)
    factory = ReadFactory()
    counts = scheduler.schedule_automation(factory)
    assert counts == {
        "topic_research_scheduled": 0,
        "articles_reanalyzed": 0,
        "reanalysis_scanned": 0,
        "relationship_jobs_scheduled": 0,
        "relationship_scans_completed": 0,
        "tags_scanned": 2,
    }
    runtime.schedule_tag_topic_discovery.assert_called_once_with(factory)
    runtime.schedule_source_admission.assert_not_called()
    runtime.schedule_article_automation.assert_not_called()
    runtime.schedule_topic_corrections.assert_not_called()
    runtime.schedule_relationship_coverage.assert_not_called()


def test_scheduler_combines_independent_stage_results_without_losing_default_counters(runtime):
    counts = scheduler.schedule_automation(ReadFactory())
    assert counts == {
        "topic_research_scheduled": 0,
        "articles_reanalyzed": 0,
        "reanalysis_scanned": 0,
        "relationship_jobs_scheduled": 7,
        "relationship_scans_completed": 0,
        "tags_scanned": 2,
        "source_reviews_scheduled": 3,
        "articles_checked": 4,
        "topic_corrections_scheduled": 5,
        "topic_branding_scheduled": 6,
        "relationships_rejected": 8,
    }


def proposal(*, complete=False):
    proposed = {
        "name": "Python",
        "slug": "python",
        "kind": "language",
        "description": "A programming language.",
        "aliases": ["Python language"],
        "keywords": ["scripting"],
        "website_url": "https://python.example",
        "logo_url": "https://python.example/logo.png",
        "facts": [
            {
                "name": "maintainer",
                "value": "Python community",
                "source_url": "https://python.example/about",
                "retrieved_at": NOW.isoformat(),
            }
        ],
    }
    if not complete:
        proposed["description"] = None
    return TopicProposal(
        id=uuid.uuid4(),
        batch_id=uuid.uuid4(),
        slug="python",
        action="create",
        origin="import",
        source_name="Language catalog",
        proposed=proposed,
        baseline=None,
        evidence=[],
        status="pending",
        created_at=NOW,
        created_by=scheduler.ACTOR,
        research_requested=True,
    )


def test_import_research_consumes_each_request_once_and_skips_prior_attempts_and_complete_metadata(
    runtime,
):
    runtime.settings = configuration(ai_enabled=True, auto_research_imports=True)
    requested, attempted, complete = proposal(), proposal(), proposal(complete=True)
    # Independently admitted imports have unique pending slugs.
    for index, item in enumerate((requested, attempted, complete)):
        item.slug = item.proposed["slug"] = f"python-{index}"
    session = Mock()
    session.scalars.return_value.all.return_value = [requested, attempted, complete]
    session.scalar.side_effect = [None, uuid.uuid4(), None]
    counts = scheduler.schedule_automation(ReadFactory(session))
    assert counts["topic_research_scheduled"] == 1
    runtime.research.assert_called_once_with(session, requested.id, scheduler.ACTOR)
    assert not any(item.research_requested for item in (requested, attempted, complete))
    assert all(item.status == "pending" for item in (requested, attempted, complete))


def test_bounded_topic_mode_does_not_also_run_legacy_import_research(runtime):
    runtime.settings = configuration(ai_enabled=True, ai_bounded_topics_enabled=True)
    session = Mock()
    session.scalar.return_value = None  # No outstanding reanalysis task in bounded mode.
    counts = scheduler.schedule_automation(ReadFactory(session))
    assert counts["topic_research_scheduled"] == 0
    session.scalars.assert_not_called()
    runtime.research.assert_not_called()


@pytest.mark.parametrize("state", ["absent-task", "deleted-topic", "retired-topic"])
def test_obsolete_reanalysis_tasks_finish_without_reclassifying_articles(runtime, state):
    runtime.settings = configuration(ai_enabled=True, auto_reanalyze_topics=True)
    current = topic(status="rejected" if state == "retired-topic" else "active")
    task = TopicReanalysis(
        id=uuid.uuid4(),
        topic_id=current.id,
        topic_snapshot={"name": "Python", "slug": "python", "aliases": [], "keywords": []},
        created_at=NOW,
        finished_at=None,
    )
    session = Mock()
    session.scalar.return_value = None if state == "absent-task" else task
    session.get.return_value = None if state == "deleted-topic" else current
    assert scheduler.schedule_automation(ReadFactory(session))["reanalysis_scanned"] == 0
    assert task.finished_at == (None if state == "absent-task" else NOW)
    session.scalars.assert_not_called()
    runtime.analyze.assert_not_called()


@pytest.mark.parametrize("full_page", [False, True])
def test_reanalysis_advances_past_nonmatches_and_unauthorized_rows_and_resumes_full_pages(
    runtime, full_page
):
    runtime.settings = configuration(
        ai_enabled=True, auto_reanalyze_topics=True, automation_batch_size=5
    )
    current = topic()
    task = TopicReanalysis(
        id=uuid.uuid4(),
        topic_id=current.id,
        topic_snapshot={
            "name": "Python runtime",
            "slug": "python-runtime",
            "aliases": ["Python"],
            "keywords": [],
        },
        created_at=NOW,
        finished_at=None,
        after_article_id=uuid.UUID(int=1),
    )
    articles = []
    for index, title in enumerate(
        ["Python tutorial", "Storage performance", "Python APIs", "Python tooling", "Python loops"]
    ):
        identifier = uuid.UUID(int=index + 2)
        publisher = source(approval="approved")
        article = Article(
            id=identifier,
            canonical_url=f"https://publisher.example/{index}",
            title=title,
            summary=title + " explained for engineers.",
            review_status="pending",
            publication_status="unpublished",
            editorial_revision=0,
            classification_provenance={},
            metadata_source_type="rss",
            topic_links=[],
            origins=[
                ArticleOrigin(
                    article_id=identifier,
                    source_id=publisher.id,
                    source=publisher,
                    entry_key=str(identifier),
                    original_url=f"https://publisher.example/{index}",
                )
            ],
        )
        articles.append(article)
    if not full_page:
        articles.pop()
    session = Mock()
    session.scalar.return_value = task
    session.scalars.return_value.all.return_value = articles
    content = {
        item.id: ArticleContent(article_id=item.id, text=item.summary, method="body")
        for item in articles
    }
    session.get.side_effect = lambda model, identifier: (
        current if model is type(current) else content[identifier]
    )
    runtime.authorized.side_effect = [True, False, True, *([True] if full_page else [])]
    # A coalesced/previously exhausted request is not counted as newly scheduled work.
    runtime.analyze.side_effect = [object(), None, *([object()] if full_page else [])]

    counts = scheduler.schedule_automation(ReadFactory(session))

    assert counts["reanalysis_scanned"] == len(articles)
    assert counts["articles_reanalyzed"] == (2 if full_page else 1)
    assert task.after_article_id == articles[-1].id
    assert task.finished_at == (None if full_page else NOW)
    assert [call.args[1] for call in runtime.authorized.call_args_list] == [
        item.id for item in articles if "Python" in item.title
    ]
    assert [call.args[1] for call in runtime.analyze.call_args_list] == [
        articles[index].id for index in ([0, 3, 4] if full_page else [0, 3])
    ]
    assert all(call.kwargs == {"automatic": True} for call in runtime.analyze.call_args_list)


@pytest.mark.parametrize("enabled", [False, True])
def test_dependency_wait_keeps_legacy_polling_or_uses_real_deadlines(monkeypatch, enabled):
    settings = configuration(
        automation_dependency_wakes_enabled=enabled, automation_reconcile_seconds=86400
    )
    monkeypatch.setattr(automation_demands, "get_settings", lambda: settings)
    article = Article(automation_next_check_at=NOW + timedelta(minutes=5))
    automation_demands.wait_for_dependency(article, NOW)
    assert article.automation_next_check_at == NOW + timedelta(
        seconds=settings.automation_reconcile_seconds if enabled else 300
    )
    deadline = NOW + timedelta(hours=3)
    automation_demands.wait_for_dependency(article, NOW, reason="topic_research", deadline=deadline)
    assert article.automation_next_check_at == (deadline if enabled else NOW + timedelta(minutes=5))
    assert article.automation_wait_reason == ("topic_research" if enabled else None)


@pytest.mark.parametrize(
    "status,offset",
    [("running", -1), ("running", 600), ("running", None), ("queued", -1), ("queued", 600)],
)
def test_active_dependency_wait_preserves_recovery_and_retry_times(monkeypatch, status, offset):
    monkeypatch.setattr(automation_demands, "get_settings", lambda: configuration())
    article = Article(automation_next_check_at=NOW)
    deadline = NOW + timedelta(seconds=offset) if offset is not None else None
    job = SimpleNamespace(status=status, lease_until=deadline, available_at=deadline)
    automation_demands.wait_for_job(article, job, NOW)
    expected = (
        NOW + timedelta(seconds=15)
        if status == "running" and offset == -1
        else deadline
        if offset == 600
        else NOW + timedelta(days=1)
    )
    assert article.automation_next_check_at == expected
    assert article.automation_wait_reason == f"{status}_job"


@pytest.mark.parametrize(
    "changed",
    [
        "full_automation",
        "article_topic_proposals_enabled",
        "automation_dependency_wakes_enabled",
        "automation_reconcile_seconds",
    ],
)
def test_runtime_policy_changes_invalidate_stable_dependency_observations(changed):
    settings = configuration()
    original = automation_demands.policy_version(settings)
    assert original == automation_demands.policy_version(settings.model_copy())
    old = getattr(settings, changed)
    setattr(settings, changed, not old if isinstance(old, bool) else old + 300)
    assert automation_demands.policy_version(settings) != original


@pytest.mark.parametrize(
    "owned,stored", [(False, None), (True, None), (True, "same"), (True, "previous")]
)
def test_policy_capture_persists_one_bounded_wake_when_settings_change(monkeypatch, owned, stored):
    session = Mock()
    session.scalar.return_value = owned
    monkeypatch.setattr(automation_demands, "policy_version", lambda _: "same")
    monkeypatch.setattr(
        automation_demands,
        "get_state",
        lambda *_: SimpleNamespace(payload={"policy": stored}) if stored else None,
    )
    save = Mock()
    monkeypatch.setattr(automation_demands, "set_state", save)
    automation_demands._enqueue_policy(ReadFactory(session), configuration())
    if owned and stored != "same":
        session.execute.assert_called_once()
        save.assert_called_once_with(session, automation_demands.POLICY_STATE, {"policy": "same"})
    else:
        session.execute.assert_not_called()
        save.assert_not_called()


@pytest.mark.parametrize(
    "source_page,full_page,successor",
    [
        (False, True, False),
        (False, False, False),
        (False, False, True),
        (True, True, False),
        (True, False, False),
        ("tag", True, False),
    ],
)
def test_demand_pages_progress_and_preserve_changes_for_a_successor_pass(
    monkeypatch, source_page, full_page, successor
):
    monkeypatch.setattr(
        automation_demands,
        "get_settings",
        lambda: configuration(automation_batch_size=1, automation_invalidation_batch_size=2),
    )
    capture = Mock()
    monkeypatch.setattr(automation_demands, "_enqueue_policy", capture)
    demand = AutomationInvalidation(
        key="source:test" if source_page else "all",
        source_id=uuid.uuid4() if source_page is True else None,
        tag_id=uuid.uuid4() if source_page == "tag" else None,
        revision=3,
        scan_revision=2,
        after_article_id=uuid.UUID(int=10),
    )
    page = [uuid.UUID(int=11), uuid.UUID(int=12)] if full_page else [uuid.UUID(int=11)]
    session = Mock()
    session.scalar.side_effect = [True, demand]
    session.scalars.side_effect = [*([[]] if not source_page else []), page, page, [demand.key]]
    session.execute.return_value.rowcount = 0 if successor else 1
    factory = ReadFactory(session)
    assert automation_demands.expand_automation_demands(factory, NOW) == len(page)
    capture.assert_called_once_with(factory, automation_demands.get_settings())
    # Demand persistence never overwrites a writer's newer revision. The page
    # either advances, finishes the matching revision, or schedules its successor.
    saved = [call.args[0].compile().params for call in session.execute.call_args_list]
    if full_page:
        assert saved[-1]["after_article_id"] == page[-1]
        assert saved[-1]["scan_revision"] == 2
        assert "revision" not in saved[-1]
    elif successor:
        assert saved[-1]["after_article_id"] is None and saved[-1]["scan_revision"] is None
    else:
        assert "revision_1" in saved[-1]
    if source_page:
        assert saved[0]["automation_wait_reason"] is None
    else:
        assert "maintenance" in saved[0].values()


@pytest.mark.parametrize(
    "full_page,changed", [(True, None), (False, "all"), (False, "catalog:tags")]
)
def test_equal_global_cursors_share_one_page_and_acknowledge_each_revision_independently(
    monkeypatch, full_page, changed
):
    monkeypatch.setattr(
        automation_demands,
        "get_settings",
        lambda: configuration(automation_invalidation_batch_size=2),
    )
    monkeypatch.setattr(automation_demands, "_enqueue_policy", lambda *_: None)
    primary = AutomationInvalidation(
        key="all", revision=3, scan_revision=2, after_article_id=uuid.UUID(int=10)
    )
    companion = AutomationInvalidation(
        key="catalog:tags", revision=7, scan_revision=None, after_article_id=uuid.UUID(int=10)
    )
    page = [uuid.UUID(int=11), uuid.UUID(int=12)] if full_page else [uuid.UUID(int=11)]
    session = Mock()
    session.scalar.side_effect = [True, primary]
    session.scalars.side_effect = [[companion], page, page, [primary.key, companion.key]]
    results = [Mock(rowcount=1)]  # One shared article-page update.
    for key in ("all", "catalog:tags"):
        results.append(Mock(rowcount=0 if key == changed else 1))
        if key == changed:
            results.append(Mock(rowcount=1))
    session.execute.side_effect = results
    assert automation_demands.expand_automation_demands(ReadFactory(session), NOW) == len(page)
    saved = [call.args[0].compile().params for call in session.execute.call_args_list]
    assert len(saved) == (3 if full_page else 4)
    if full_page:
        assert [item["scan_revision"] for item in saved[1:]] == [2, 7]
        assert all(item["after_article_id"] == page[-1] for item in saved[1:])
    else:
        acknowledged = [item for item in saved[1:] if "revision_1" in item]
        assert [item["revision_1"] for item in acknowledged] == [2, 7]
        restarted = [item for item in saved[1:] if "after_article_id" in item]
        assert len(restarted) == 1
        assert restarted[0]["key_1"] == changed
        assert restarted[0]["after_article_id"] is None
        assert restarted[0]["scan_revision"] is None


@pytest.mark.parametrize("case", ["complete", "changed", "deleted", "contended", "vanished"])
def test_direct_dependency_requests_are_batched_and_replayed_without_scanning_article_history(
    monkeypatch, case
):
    monkeypatch.setattr(
        automation_demands,
        "get_settings",
        lambda: configuration(automation_invalidation_batch_size=2),
    )
    monkeypatch.setattr(automation_demands, "_enqueue_policy", lambda *_: None)
    identifiers = [uuid.UUID(int=1), uuid.UUID(int=2)]
    demands = [
        AutomationInvalidation(
            key=f"article:{identifier}",
            revision=revision,
            scan_revision=None,
            after_article_id=None,
        )
        for identifier, revision in zip(identifiers, (3, 7), strict=True)
    ]
    session = Mock()
    session.scalar.side_effect = [True, demands[0]]
    if case == "vanished":
        session.scalars.side_effect = [[], []]
    elif case == "contended":
        error = OperationalError("busy article", {}, Exception("busy parent"))
        error.orig.sqlstate = "55P03"
        session.scalars.side_effect = [demands, error]
    else:
        locked = [] if case == "deleted" else identifiers
        session.scalars.side_effect = [demands, locked, [demand.key for demand in demands]]
        results = [] if case == "deleted" else [Mock(rowcount=2)]
        results.append(Mock(rowcount=0 if case == "changed" else 1))
        if case == "changed":
            results.append(Mock(rowcount=1))
        results.append(Mock(rowcount=1))
        session.execute.side_effect = results
    processed = automation_demands.expand_automation_demands(ReadFactory(session), NOW)
    assert processed == (0 if case in {"contended", "vanished"} else 2)
    if case in {"contended", "vanished"}:
        session.execute.assert_not_called()
    else:
        writes = [call.args[0] for call in session.execute.call_args_list]
        acknowledged = [statement.compile().params for statement in writes if statement.is_delete]
        assert [item["revision_1"] for item in acknowledged] == [3, 7]
        # Even a full direct batch consumes exact requests, never a global cursor.
        assert [item["key_1"] for item in acknowledged] == [item.key for item in demands]
        if case != "deleted":
            params = writes[0].compile().params
            assert params["id_1"] == identifiers
            assert params["automation_wait_reason"] is None
        restarted = [
            statement.compile().params
            for statement in writes
            if statement.is_update and statement.table.name == AutomationInvalidation.__tablename__
        ]
        assert len(restarted) == int(case == "changed")


@pytest.mark.parametrize(
    "case", ["busy", "empty", "empty-page", "locked", "locked-queue", "database-error"]
)
def test_demand_recovery_defers_contention_without_hiding_database_failures(monkeypatch, case):
    monkeypatch.setattr(automation_demands, "get_settings", lambda: configuration())
    monkeypatch.setattr(automation_demands, "_enqueue_policy", lambda *_: None)
    session = Mock()
    demand = AutomationInvalidation(
        key="all", revision=1, scan_revision=None, after_article_id=None
    )
    session.scalar.side_effect = [case != "busy", None if case == "empty" else demand]
    if case in {"locked", "locked-queue", "database-error"}:
        error = OperationalError("locked", {}, Exception("database unavailable"))
        error.orig.sqlstate = "08006" if case == "database-error" else "55P03"
        page = [uuid.UUID(int=1)]
        session.scalars.side_effect = (
            [[], page, page, error] if case == "locked-queue" else [[], error]
        )
    else:
        session.scalars.return_value = []
    if case == "database-error":
        with pytest.raises(OperationalError):
            automation_demands.expand_automation_demands(ReadFactory(session), NOW)
    else:
        assert automation_demands.expand_automation_demands(ReadFactory(session), NOW) == 0
    if case in {"busy", "empty", "locked", "locked-queue", "database-error"}:
        session.execute.assert_not_called()


@pytest.mark.parametrize("case", ["disabled", "retry-now", "completed", "failure"])
def test_locked_observation_consumes_its_flushed_revision_without_losing_later_wakes(
    monkeypatch, case
):
    settings = configuration(automation_dependency_wakes_enabled=case != "disabled")
    monkeypatch.setattr(automation_demands, "get_settings", lambda: settings)
    session = Mock()
    article = Article(
        automation_next_check_at=NOW + timedelta(hours=3),
        automation_input_revision=3,
        automation_evaluated_revision=1,
        automation_wait_reason="topic_research",
    )

    def own_requests_flush():
        # A newly queued prerequisite updates the database wake clock/revision;
        # this observation knows that prerequisite is now waiting for completion.
        article.automation_next_check_at = NOW
        article.automation_wait_reason = None

    session.flush.side_effect = own_requests_flush
    session.refresh.side_effect = lambda *_args, **_kwargs: setattr(
        article, "automation_input_revision", 4
    )
    if case == "failure":
        with (
            pytest.raises(ValueError, match="changed input"),
            automation_demands.observe_automation_demand(session, article, NOW),
        ):
            raise ValueError("changed input")
    else:
        with automation_demands.observe_automation_demand(session, article, NOW):
            if case == "retry-now":
                article.automation_next_check_at = NOW
    if case == "completed":
        assert article.automation_evaluated_revision == 4
        assert article.automation_next_check_at == NOW + timedelta(hours=3)
        assert article.automation_wait_reason == "topic_research"
        session.refresh.assert_called_once_with(
            article, attribute_names=["automation_input_revision"]
        )
    else:
        assert article.automation_evaluated_revision == 1
        session.flush.assert_not_called()


@pytest.mark.parametrize(
    "case",
    ["disabled", "noop", "throttled", "backlog", "cursor-drained", "empty", "database-error"],
)
def test_automation_metrics_distinguish_total_age_from_progress_without_sampling_every_tick(
    monkeypatch, case, caplog
):
    pending, oldest = Mock(), Mock()
    runtime = SimpleNamespace(
        instruments={"read_model_pending": pending, "read_model_oldest": oldest},
        meter_provider=None if case == "noop" else object(),
    )
    monkeypatch.setattr(
        automation_demands, "current", lambda: None if case == "disabled" else runtime
    )
    monkeypatch.setattr(automation_demands, "utcnow", lambda: NOW)
    monkeypatch.setattr(automation_demands.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(
        automation_demands, "_backlog_sample_at", 99.0 if case == "throttled" else None
    )
    session = Mock()
    session.execute.return_value.one.side_effect = [
        (0, None, None, 0, None)
        if case in {"empty", "cursor-drained"}
        else (
            3,
            NOW - timedelta(hours=1),
            NOW - timedelta(seconds=5),
            2,
            NOW - timedelta(seconds=15),
        ),
        (0, None, 0, None)
        if case == "empty"
        else (2, NOW - timedelta(seconds=10), 5, NOW - timedelta(hours=2)),
    ]
    if case == "database-error":
        session.execute.side_effect = OperationalError(
            "metrics", {}, Exception("database unavailable")
        )
    factory = ReadFactory(session)
    automation_demands.record_automation_backlog(factory)
    if case in {"disabled", "noop", "throttled"}:
        session.execute.assert_not_called()
        pending.set.assert_not_called()
    elif case == "database-error":
        pending.set.assert_not_called()
        assert "automation_backlog_observation_failed" in caplog.text
    else:
        queue_empty = case in {"empty", "cursor-drained"}
        assert [call.args for call in pending.set.call_args_list] == [
            (0 if queue_empty else 3, {"kind": "automation-invalidations"}),
            (0 if queue_empty else 2, {"kind": "automation-direct-invalidations"}),
            (0 if case == "empty" else 2, {"kind": "automation-foreground"}),
            (0 if case == "empty" else 5, {"kind": "automation-maintenance"}),
        ]
        assert [call.args for call in oldest.set.call_args_list] == [
            (0 if queue_empty else 3600.0, {"kind": "automation-invalidations"}),
            (0 if queue_empty else 5.0, {"kind": "automation-invalidation-progress"}),
            (0 if queue_empty else 15.0, {"kind": "automation-direct-invalidations"}),
            (0 if case == "empty" else 10.0, {"kind": "automation-foreground"}),
            (0 if case == "empty" else 7200.0, {"kind": "automation-maintenance"}),
        ]
        assert session.execute.call_count == 2
    if case not in {"disabled", "noop", "throttled"}:
        assert automation_demands._backlog_sample_at == 100.0
        # Both success and failure attempts remain throttled for this process.
        automation_demands.record_automation_backlog(ReadFactory())
