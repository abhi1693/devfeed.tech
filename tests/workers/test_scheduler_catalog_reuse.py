"""Scheduler publication keeps authoritative guards without ranking while holding locks."""

import uuid
from datetime import timedelta

import pytest
from devfeed_aggregator.scheduler import recover_jobs
from devfeed_core import analysis, article_automation, publication_policy
from devfeed_core.config import get_settings
from devfeed_core.db import get_engine
from devfeed_core.job_lifecycle import finish_job
from devfeed_core.models import Article, ArticleAnalysisJob, utcnow
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from tests.workers.test_automation_integration import enable, ready, seed
from tests.workers.test_full_app_automation import full

pytestmark = pytest.mark.integration


def test_publication_batch_ranks_once_per_article_before_locks(database, monkeypatch):
    full(monkeypatch, AUTOMATION_BATCH_SIZE=5)
    monkeypatch.setattr(get_settings(), "cache_enabled", False)
    with database.begin() as session:
        articles = [seed(session, name=f"Framework{index}") for index in range(5)]
        for _, article, topic in articles:
            ready(session, article, topic)
    locked = [False]
    connections = [0]
    ranks, evaluations, loads = [], [], []
    rank = analysis._candidate_scores
    evaluate = publication_policy.evaluate_publication
    load = analysis._read_catalog

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "FOR UPDATE" in statement or "LOCK TABLE topics" in statement:
            locked[0] = True

    def released(session, transaction):
        if transaction.parent is None:
            locked[0] = False

    def checkout(*args):
        connections[0] += 1

    def checkin(*args):
        connections[0] -= 1

    def ranking(*args):
        assert not locked[0], "Full-catalog ranking must precede row and taxonomy locks"
        assert connections[0] == 0, "Ranking must release the read transaction"
        ranks.append(True)
        return rank(*args)

    def evaluation(*args, **kwargs):
        evaluations.append(True)
        return evaluate(*args, **kwargs)

    def loading(*args):
        loads.append(True)
        return load(*args)

    monkeypatch.setattr(analysis, "_candidate_scores", ranking)
    monkeypatch.setattr(analysis, "_read_catalog", loading)
    monkeypatch.setattr(publication_policy, "evaluate_publication", evaluation)
    monkeypatch.setattr(article_automation, "evaluate_publication", evaluation)
    event.listen(get_engine(), "before_cursor_execute", capture)
    event.listen(get_engine(), "checkout", checkout)
    event.listen(get_engine(), "checkin", checkin)
    event.listen(Session, "after_transaction_end", released)
    try:
        counts = article_automation.schedule_article_automation(database)
    finally:
        event.remove(get_engine(), "before_cursor_execute", capture)
        event.remove(get_engine(), "checkout", checkout)
        event.remove(get_engine(), "checkin", checkin)
        event.remove(Session, "after_transaction_end", released)
    assert counts["articles_published"] == 5
    assert len(ranks) == len(evaluations) == 5
    assert len(loads) == 1


@pytest.mark.parametrize("changed", ["catalog", "content", "editorial", "source"])
def test_change_between_preparation_and_locking_never_applies_stale_result(
    database, monkeypatch, changed
):
    full(monkeypatch)
    with database.begin() as session:
        source, article, topic = seed(session)
        ready(session, article, topic)
        identifier, source_id, topic_id = article.id, source.id, topic.id
    prepare = article_automation._prepare_article_candidates

    def racing_prepare(*args):
        result = prepare(*args)
        with database.begin() as editor:
            if changed == "catalog":
                editor.execute(
                    text("UPDATE topics SET name='Changed' WHERE id=:id"), {"id": topic_id}
                )
            elif changed == "content":
                editor.execute(
                    text("UPDATE articles SET summary='Changed article text' WHERE id=:id"),
                    {"id": identifier},
                )
            elif changed == "editorial":
                editor.execute(
                    text(
                        "UPDATE articles SET editorial_revision=editorial_revision+1 WHERE id=:id"
                    ),
                    {"id": identifier},
                )
            else:
                editor.execute(
                    text("UPDATE sources SET enabled=false WHERE id=:id"), {"id": source_id}
                )
        return result

    monkeypatch.setattr(article_automation, "_prepare_article_candidates", racing_prepare)
    counts = article_automation.schedule_article_automation(database)
    assert counts["articles_published"] == counts["articles_rejected"] == 0
    with database() as session:
        article = session.get(Article, identifier)
        assert article.review_status == "pending" and article.publication_status == "unpublished"
        if changed in {"catalog", "content"}:
            assert article.automation_next_check_at <= utcnow()
            jobs = session.scalars(
                select(ArticleAnalysisJob).order_by(
                    ArticleAnalysisJob.created_at, ArticleAnalysisJob.id
                )
            ).all()
            assert jobs[0].status == "succeeded" and jobs[0].outcome == "applied"
            if changed == "content":
                assert len(jobs) == 2
                assert jobs[1].status == "queued"
                assert jobs[1].input_hash == analysis.snapshot_hash(
                    analysis.source_snapshot(article, None)
                )
                assert jobs[1].input_hash != jobs[0].input_hash
                assert not jobs[1].result
            else:
                assert len(jobs) == 1


@pytest.mark.parametrize("status", ["queued", "running"])
def test_repeated_active_checks_preserve_work_without_hydrating_analysis_evidence(
    database, monkeypatch, status
):
    full(monkeypatch, AUTOMATION_BATCH_SIZE=2, AUTOMATION_DEPENDENCY_WAKES_ENABLED=False)
    now = utcnow()
    with database.begin() as session:
        _, article, topic = seed(session, mode="auto")
        job = ready(session, article, topic)
        article.automation_started_at = now - timedelta(hours=25)
        article.automation_next_check_at = now - timedelta(seconds=1)
        job.status, job.attempts = status, 2
        job.finished_at = None
        job.available_at = now + timedelta(hours=1)
        job.lease_token = uuid.uuid4() if status == "running" else None
        job.lease_until = now + timedelta(minutes=20) if status == "running" else None
        job.input_snapshot = {"large_input": "publisher evidence " * 10000}
        job.catalog_snapshot = {"large_catalog": "retained catalog evidence " * 10000}
        job.result = {"large_result": "retained analysis evidence " * 10000}
        job.usage = {"large_usage": "retained specialist evidence " * 10000}
        identifier, job_id = article.id, job.id
        before = (
            job.status,
            job.attempts,
            job.available_at,
            job.lease_token,
            job.lease_until,
        )
    selects = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("SELECT") and "FROM article_analysis_jobs" in statement:
            selects.append(statement.split("FROM", 1)[0])

    def unexpected_catalog(*_):
        pytest.fail("An active-job check must not hydrate the taxonomy")

    monkeypatch.setattr(analysis, "_read_catalog", unexpected_catalog)
    event.listen(get_engine(), "before_cursor_execute", capture)
    try:
        for index in range(3):
            tick_at = now + index * article_automation.CHECK_INTERVAL
            monkeypatch.setattr(article_automation, "utcnow", lambda tick_at=tick_at: tick_at)
            counts = article_automation.schedule_article_automation(database)
            assert counts["articles_checked"] == 1
            assert counts["articles_published"] == counts["articles_rejected"] == 0
    finally:
        event.remove(get_engine(), "before_cursor_execute", capture)
    # Preparation status, locked metadata, one readiness fact load per tick.
    assert len(selects) == 9
    for statement in selects:
        for field in ("input_snapshot", "catalog_snapshot", "result", "usage"):
            assert f"article_analysis_jobs.{field}" not in statement
    with database() as session:
        article, job = session.get(Article, identifier), session.get(ArticleAnalysisJob, job_id)
        assert article.automation_next_check_at == tick_at + article_automation.CHECK_INTERVAL
        assert article.automation_started_at == now - timedelta(hours=25)
        assert (
            job.status,
            job.attempts,
            job.available_at,
            job.lease_token,
            job.lease_until,
        ) == before
        assert len(session.scalars(select(ArticleAnalysisJob)).all()) == 1


def test_analysis_completing_after_preparation_remains_due_and_publishes_on_the_next_tick(
    database, monkeypatch
):
    full(monkeypatch)
    with database.begin() as session:
        _, article, topic = seed(session, mode="auto")
        job = ready(session, article, topic)
        job.status, job.finished_at = "running", None
        identifier, job_id = article.id, job.id
    prepare = article_automation._prepare_article_candidates

    def completing_prepare(*args):
        prepared = prepare(*args)
        assert prepared is None
        with database.begin() as worker:
            job = worker.get(ArticleAnalysisJob, job_id)
            finish_job(job, "applied", utcnow())
        return prepared

    monkeypatch.setattr(article_automation, "_prepare_article_candidates", completing_prepare)
    counts = article_automation.schedule_article_automation(database)
    assert counts["articles_checked"] == 1 and counts["articles_published"] == 0
    with database() as session:
        assert session.get(Article, identifier).automation_next_check_at <= utcnow()
        assert len(session.scalars(select(ArticleAnalysisJob)).all()) == 1
    monkeypatch.setattr(article_automation, "_prepare_article_candidates", prepare)
    assert article_automation.schedule_article_automation(database)["articles_published"] == 1


@pytest.mark.parametrize("step_attempts,status", [(1, "queued"), (3, "failed")])
def test_no_outcome_maintenance_keeps_expired_specialists_recoverable_without_duplicate_jobs(
    database, monkeypatch, step_attempts, status
):
    enable(monkeypatch)
    now = utcnow()
    with database.begin() as session:
        _, article, topic = seed(session)
        job = ready(session, article, topic)
        job.status, job.finished_at, job.attempts = "running", None, 99
        job.lease_token, job.lease_until = uuid.uuid4(), now - timedelta(seconds=1)
        job.usage = {
            "active_step": "overview",
            "article_steps": {
                "developer_relevance": {"status": "succeeded", "output": {"relevant": True}},
                "overview": {"status": "running", "attempts": step_attempts},
            },
        }
        identifier, job_id, token = article.id, job.id, job.lease_token

    assert article_automation.schedule_article_automation(database)["articles_checked"] == 1
    with database() as session:
        job = session.get(ArticleAnalysisJob, job_id)
        assert job.status == "running" and job.lease_token == token

    assert recover_jobs(database, 10, now) == 1
    with database.begin() as session:
        article = session.get(Article, identifier)
        article.automation_next_check_at = now - timedelta(seconds=1)
        job = session.get(ArticleAnalysisJob, job_id)
        assert job.status == status
        assert job.lease_token is None and job.lease_until is None
        assert job.steps["overview"]["status"] == ("pending" if status == "queued" else "failed")
        assert job.steps["developer_relevance"]["output"] == {"relevant": True}
        available_at = job.available_at

    assert article_automation.schedule_article_automation(database)["articles_checked"] == 1
    with database() as session:
        jobs = session.scalars(select(ArticleAnalysisJob)).all()
        assert len(jobs) == 1
        assert jobs[0].id == job_id and jobs[0].status == status
        assert jobs[0].available_at == available_at and jobs[0].attempts == 99


def test_stable_article_waits_for_semantic_input_and_keeps_daily_reconciliation(
    database, monkeypatch
):
    enable(monkeypatch)
    with database.begin() as session:
        _, article, topic = seed(session)
        ready(session, article, topic)
        identifier = article.id
    now = utcnow()
    monkeypatch.setattr(article_automation, "utcnow", lambda: now)
    assert article_automation.schedule_article_automation(database)["articles_checked"] == 1
    with database() as session:
        article = session.get(Article, identifier)
        assert article.automation_wait_reason == "dependencies"
        assert article.automation_input_revision == article.automation_evaluated_revision
        assert article.automation_next_check_at == now + timedelta(days=1)
    # The old five-minute loop no longer ranks settled articles or reads their evidence.
    prepare = article_automation._prepare_article_candidates
    monkeypatch.setattr(
        article_automation, "_prepare_article_candidates", lambda *_: pytest.fail("Unexpected rank")
    )
    monkeypatch.setattr(article_automation, "utcnow", lambda: now + timedelta(minutes=5))
    assert article_automation.schedule_article_automation(database)["articles_checked"] == 0
    with database.begin() as session:
        session.execute(
            text("UPDATE articles SET summary=summary || ' New input.' WHERE id=:id"),
            {"id": identifier},
        )
    with database() as session:
        article = session.get(Article, identifier)
        assert article.automation_input_revision > article.automation_evaluated_revision
    monkeypatch.setattr(article_automation, "_prepare_article_candidates", prepare)
    assert article_automation.schedule_article_automation(database)["articles_checked"] == 1


@pytest.mark.parametrize("status", ["queued", "running"])
def test_active_job_waits_without_rechecks_and_completion_wakes_publication(
    database, monkeypatch, status
):
    full(monkeypatch)
    now = utcnow()
    with database.begin() as session:
        _, article, topic = seed(session, mode="auto")
        job = ready(session, article, topic)
        job.status, job.finished_at = status, None
        job.available_at = now + timedelta(hours=1)
        job.lease_until = now + timedelta(hours=1) if status == "running" else None
        identifier, job_id = article.id, job.id
    monkeypatch.setattr(article_automation, "utcnow", lambda: now + timedelta(seconds=1))
    assert article_automation.schedule_article_automation(database)["articles_checked"] == 1
    with database.begin() as session:
        article = session.get(Article, identifier)
        revision, due = article.automation_input_revision, article.automation_next_check_at
        session.execute(
            text(
                "UPDATE article_analysis_jobs SET dispatched_at=now(),lease_until=:lease "
                "WHERE id=:id"
            ),
            {"id": job_id, "lease": now + timedelta(hours=2)},
        )
    monkeypatch.setattr(article_automation, "utcnow", lambda: now + timedelta(minutes=5))
    assert article_automation.schedule_article_automation(database)["articles_checked"] == 0
    with database.begin() as session:
        article = session.get(Article, identifier)
        assert (article.automation_input_revision, article.automation_next_check_at) == (
            revision,
            due,
        )
        finish_job(session.get(ArticleAnalysisJob, job_id), "applied", utcnow())
    assert article_automation.schedule_article_automation(database)["articles_published"] == 1


def test_catalog_wakes_are_bounded_and_changes_during_a_pass_get_complete_successor_coverage(
    database, monkeypatch
):
    from devfeed_core.automation_demands import expand_automation_demands
    from devfeed_core.models import AutomationInvalidation, Tag, Topic, TopicProposal

    enable(monkeypatch, AUTOMATION_BATCH_SIZE=1, AUTOMATION_INVALIDATION_BATCH_SIZE=2)
    with database.begin() as session:
        articles = [seed(session, name=f"Framework{index}")[1] for index in range(5)]
        session.add(Tag(name="Runtime", slug="runtime"))
        session.flush()
        for article in articles:
            article.automation_next_check_at = utcnow() + timedelta(days=1)
        identifiers = sorted(article.id for article in articles)
    now = utcnow()
    assert expand_automation_demands(database, now) == 2
    with database.begin() as session:
        first_revisions = {
            article.id: article.automation_input_revision
            for article in session.scalars(select(Article))
        }
        assert {
            demand.after_article_id for demand in session.scalars(select(AutomationInvalidation))
        } == {identifiers[1]}
        session.add(
            Topic(
                name="New catalog vocabulary",
                slug="new-vocabulary",
                status="active",
                kind="technology",
            )
        )
        session.add(
            TopicProposal(
                batch_id=uuid.uuid4(),
                slug="new-runtime",
                action="create",
                origin="import",
                source_name="Test",
                created_by={},
                proposed={"name": "Runtime", "slug": "new-runtime"},
            )
        )
    assert expand_automation_demands(database, now) == 2
    with database() as session:
        # A newly requested family starts behind this pass and cannot share its
        # partially completed cursor or lose the already covered article prefix.
        assert session.get(AutomationInvalidation, "proposals").after_article_id is None
    assert expand_automation_demands(database, now) == 1
    with database() as session:
        assert session.get(AutomationInvalidation, "all") is None
        assert session.get(AutomationInvalidation, "catalog:tags") is None
        changed = session.get(AutomationInvalidation, "catalog:topics")
        assert changed.after_article_id is None and changed.scan_revision is None
        assert session.get(AutomationInvalidation, "proposals").after_article_id is None
    for _ in range(10):
        assert expand_automation_demands(database, now) <= 2
        with database() as session:
            if session.scalar(select(AutomationInvalidation.key).limit(1)) is None:
                break
    else:
        pytest.fail("Catalog demand did not converge")
    with database() as session:
        for article in session.scalars(select(Article)):
            assert article.automation_input_revision == first_revisions[article.id] + (
                0 if article.id in identifiers[:2] else 1
            )
        assert all(session.get(Article, key).automation_next_check_at <= now for key in identifiers)


@pytest.mark.parametrize("owner", ["global", "source", "tag"])
def test_repeated_fanout_coalesces_due_work_and_promotes_only_targeted_maintenance(
    database, monkeypatch, owner
):
    from devfeed_core.automation_demands import _enqueue_policy, expand_automation_demands
    from devfeed_core.models import ArticleOrigin, ArticleTag, AutomationInvalidation, Source, Tag
    from sqlalchemy import delete, update

    enable(monkeypatch)
    now = utcnow()
    with database.begin() as session:
        rows = [seed(session, name=f"Framework{index}") for index in range(3)]
        source_id = rows[0][0].id
        identifiers = [row[1].id for row in rows]
        session.execute(update(ArticleOrigin).values(source_id=source_id))
        tag = Tag(name="Runtime", slug="runtime", auto_link_topic=False)
        session.add(tag)
        session.flush()
        session.add_all(
            ArticleTag(article_id=identifier, tag_id=tag.id, origin="source")
            for identifier in identifiers
        )
        session.flush()
        for index, (_, article, _) in enumerate(rows):
            article.automation_next_check_at = now + timedelta(days=1) if index == 2 else now
            article.automation_wait_reason = "maintenance" if index == 1 else None
        tag_id = tag.id
    _enqueue_policy(database, get_settings())
    with database.begin() as session:
        session.execute(delete(AutomationInvalidation))
        before = {
            article.id: article.automation_input_revision
            for article in session.scalars(select(Article))
        }
    writes = []

    def record(_connection, cursor, statement, *_):
        if statement.startswith("UPDATE articles SET") and "automation_input_revision" in statement:
            writes.append(cursor.rowcount)

    event.listen(get_engine(), "after_cursor_execute", record)
    try:
        for wave in range(2):
            with database.begin() as session:
                if owner == "global":
                    session.execute(update(Tag).where(Tag.id == tag_id).values(aliases=[str(wave)]))
                elif owner == "source":
                    session.execute(
                        update(Source)
                        .where(Source.id == source_id)
                        .values(publication_policy="preview" if wave == 0 else "auto")
                    )
                else:
                    session.execute(
                        update(Tag).where(Tag.id == tag_id).values(auto_link_topic=wave == 0)
                    )
            assert expand_automation_demands(database, now) == 3
            with database() as session:
                for index, identifier in enumerate(identifiers):
                    article = session.get(Article, identifier)
                    expected = int(index == 2 or (index == 1 and owner != "global"))
                    assert article.automation_input_revision == before[identifier] + expected
                    assert article.automation_next_check_at <= now
                    assert article.automation_wait_reason == (
                        "maintenance" if owner == "global" and index in {1, 2} else None
                    )
                assert session.scalar(select(AutomationInvalidation.key).limit(1)) is None
    finally:
        event.remove(get_engine(), "after_cursor_execute", record)
    assert writes == [1 if owner == "global" else 2, 0]


def test_due_article_evaluation_cannot_hide_a_racing_tag_catalog_wake(database, monkeypatch):
    from devfeed_core.automation_demands import _enqueue_policy, expand_automation_demands
    from devfeed_core.models import AutomationInvalidation, Tag
    from sqlalchemy import delete, update

    enable(monkeypatch)
    now = utcnow()
    with database.begin() as session:
        _, article, _ = seed(session)
        tag = Tag(name="Runtime", slug="runtime")
        session.add(tag)
        session.flush()
        identifier, tag_id = article.id, tag.id
        article.automation_next_check_at = now
    _enqueue_policy(database, get_settings())
    with database.begin() as session:
        session.execute(delete(AutomationInvalidation))
    with database.begin() as evaluator:
        article = evaluator.scalar(
            select(Article).where(Article.id == identifier).with_for_update()
        )
        with database.begin() as writer:
            # Tag writes need not take the Topic table lock used by classification.
            writer.execute(update(Tag).where(Tag.id == tag_id).values(aliases=["new-runtime"]))
        assert expand_automation_demands(database, now) == 0
        with database() as observer:
            demand = observer.get(AutomationInvalidation, "catalog:tags")
            assert demand.after_article_id is None and demand.scan_revision is None
        article.automation_next_check_at = now + timedelta(days=1)
        article.automation_wait_reason = "dependencies"
    assert expand_automation_demands(database, now) == 1
    with database() as observer:
        assert observer.get(Article, identifier).automation_next_check_at <= now
        assert observer.scalar(select(AutomationInvalidation.key).limit(1)) is None


def test_busy_parent_defers_exact_dependency_wakes_and_replays_bounded_foreground_batches(
    database, monkeypatch
):
    from devfeed_core.automation_demands import _enqueue_policy, expand_automation_demands
    from devfeed_core.models import ArticleOrigin, AutomationInvalidation

    enable(monkeypatch, AUTOMATION_INVALIDATION_BATCH_SIZE=2)
    now = utcnow()
    with database.begin() as session:
        rows = [seed(session, name=f"Deferred{index}") for index in range(6)]
        session.flush()
        for _, article, _ in rows:
            article.automation_next_check_at = now + timedelta(days=1)
        targets = [row[1].id for row in rows[:5]]
        untouched = rows[5][1].id
        owners = [(source.id, article.id) for source, article, _ in rows[:5]]
    _enqueue_policy(database, get_settings())
    with database() as session:
        before = {
            article.id: article.automation_input_revision
            for article in session.scalars(select(Article))
        }
    with database.begin() as reviewer:
        list(
            reviewer.scalars(
                select(Article.id).where(Article.id.in_(targets)).with_for_update(key_share=True)
            )
        )
        with database.begin() as ingestion:
            ingestion.execute(text("SET LOCAL lock_timeout='100ms'"))
            ingestion.add_all(
                ArticleOrigin(
                    article_id=article_id,
                    source_id=source_id,
                    entry_key=f"deferred-{wave}",
                    original_url=f"https://example.com/{article_id}",
                )
                for source_id, article_id in owners
                for wave in range(2)
            )
        with database() as session:
            requests = session.scalars(
                select(AutomationInvalidation).where(
                    AutomationInvalidation.key.startswith("article:")
                )
            ).all()
            assert len(requests) == 5 and {item.revision for item in requests} == {2}
        assert expand_automation_demands(database, now) == 0
        with database() as session:
            assert all(
                session.get(Article, identifier).automation_input_revision == before[identifier]
                and session.get(Article, identifier).automation_next_check_at > now
                for identifier in targets
            )
    for expected in (2, 2, 1):
        assert expand_automation_demands(database, now) == expected
    with database() as session:
        assert not list(
            session.scalars(
                select(AutomationInvalidation.key).where(
                    AutomationInvalidation.key.startswith("article:")
                )
            )
        )
        # Older global work remains untouched while direct foreground requests drain.
        assert session.get(AutomationInvalidation, "all").after_article_id is None
        for identifier in targets:
            article = session.get(Article, identifier)
            assert article.automation_input_revision == before[identifier] + 1
            assert (
                article.automation_next_check_at <= now and article.automation_wait_reason is None
            )
        article = session.get(Article, untouched)
        assert article.automation_input_revision == before[untouched]
        assert article.automation_next_check_at > now


@pytest.mark.parametrize("state", ["racing-revision", "deleted", "terminal"])
def test_direct_dependency_acknowledgement_preserves_new_revisions_and_tombstones(
    database, monkeypatch, state
):
    from devfeed_core.automation_demands import expand_automation_demands
    from devfeed_core.models import AutomationInvalidation
    from sqlalchemy import delete

    enable(monkeypatch, AUTOMATION_INVALIDATION_BATCH_SIZE=2)
    now = utcnow()
    with database.begin() as session:
        _, article, _ = seed(session)
        article.automation_next_check_at = now + timedelta(days=1)
        identifier = article.id
        key = f"article:{identifier}"
        session.add(AutomationInvalidation(key=key))
    with database.begin() as session:
        if state == "deleted":
            session.execute(delete(Article).where(Article.id == identifier))
        elif state == "terminal":
            session.get(Article, identifier).review_status = "rejected"
    raced = []

    def change_before_lock(_connection, _cursor, statement, *_):
        if (
            state == "racing-revision"
            and not raced
            and statement.startswith("SELECT articles.id")
            and "NOWAIT" in statement
        ):
            raced.append(True)
            with database.begin() as writer:
                writer.execute(
                    text("SELECT devfeed_automation_enqueue(NULL,NULL,:key)"), {"key": key}
                )

    event.listen(get_engine(), "before_cursor_execute", change_before_lock)
    try:
        assert expand_automation_demands(database, now) == 1
    finally:
        event.remove(get_engine(), "before_cursor_execute", change_before_lock)
    with database() as session:
        request = session.get(AutomationInvalidation, key)
        if state == "racing-revision":
            assert raced and request.revision == 2 and request.after_article_id is None
            assert session.get(Article, identifier).automation_wait_reason is None
        else:
            assert request is None
    if state == "racing-revision":
        assert expand_automation_demands(database, now) == 1
        with database() as session:
            assert session.get(AutomationInvalidation, key) is None


@pytest.mark.parametrize("commit_first", [False, True])
def test_independent_catalog_families_capture_commits_without_waiting_for_other_writers(
    database, commit_first
):
    from devfeed_core.models import AutomationInvalidation, Tag, TopicProposal
    from sqlalchemy import delete

    with database.begin() as session:
        seed(session)
        session.add(Tag(name="Routing", slug="routing"))
        session.add(
            TopicProposal(
                batch_id=uuid.uuid4(),
                slug="python",
                action="create",
                origin="import",
                source_name="Test",
                created_by={},
                proposed={"name": "Python", "slug": "python"},
            )
        )
        session.flush()
        session.execute(delete(AutomationInvalidation))
    with database() as first:
        first.execute(text("UPDATE tags SET name='Uncommitted routing'"))
        with database.begin() as second:
            second.execute(text("SET LOCAL statement_timeout='500ms'"))
            second.execute(text("UPDATE topics SET description='Committed topic evidence'"))
        with database.begin() as third:
            third.execute(text("SET LOCAL statement_timeout='500ms'"))
            third.execute(
                text(
                    "UPDATE topic_proposals SET proposed=proposed || "
                    "jsonb_build_object('keywords',jsonb_build_array('runtime'))"
                )
            )
        with database() as observer:
            assert set(observer.scalars(select(AutomationInvalidation.key))) == {
                "catalog:topics",
                "proposals",
            }
        first.commit() if commit_first else first.rollback()
    with database() as observer:
        assert set(observer.scalars(select(AutomationInvalidation.key))) == {
            "catalog:topics",
            "proposals",
            *({"catalog:tags"} if commit_first else set()),
        }


@pytest.mark.parametrize("held", ["tags", "topics"])
def test_global_cursor_page_defers_locked_family_before_acknowledging_any_work(
    database, monkeypatch, held
):
    from devfeed_core.automation_demands import expand_automation_demands
    from devfeed_core.models import AutomationInvalidation, Tag

    enable(monkeypatch, AUTOMATION_INVALIDATION_BATCH_SIZE=1)
    with database.begin() as session:
        _, article, _ = seed(session)
        session.add(Tag(name="Routing", slug="routing"))
        session.flush()
        article.automation_next_check_at = utcnow() + timedelta(days=1)
        session.flush()
        session.refresh(article, attribute_names=["automation_input_revision"])
        identifier, revision, due = (
            article.id,
            article.automation_input_revision,
            article.automation_next_check_at,
        )
    with database.begin() as writer:
        writer.execute(text("SET LOCAL statement_timeout='500ms'"))
        writer.execute(text(f"UPDATE {held} SET name=name || ' changed'"))
        assert expand_automation_demands(database, utcnow()) == 0
        with database() as observer:
            article = observer.get(Article, identifier)
            assert (article.automation_input_revision, article.automation_next_check_at) == (
                revision,
                due,
            )
            assert all(
                demand.after_article_id is None and demand.scan_revision is None
                for demand in observer.scalars(select(AutomationInvalidation))
            )
        # The deferred publisher retained no earlier family lock that can block
        # the writer's second statement in the opposite family order.
        other = "topics" if held == "tags" else "tags"
        writer.execute(text(f"UPDATE {other} SET name=name || ' changed'"))
    assert expand_automation_demands(database, utcnow()) == 1
    assert expand_automation_demands(database, utcnow()) == 0
    with database() as observer:
        assert observer.get(Article, identifier).automation_next_check_at <= utcnow()
        assert observer.scalar(select(AutomationInvalidation.key).limit(1)) is None


def test_locked_article_defers_demand_page_without_skipping_its_wake(database, monkeypatch):
    from devfeed_core.automation_demands import expand_automation_demands
    from devfeed_core.models import AutomationInvalidation

    enable(monkeypatch, AUTOMATION_BATCH_SIZE=1, AUTOMATION_INVALIDATION_BATCH_SIZE=2)
    with database.begin() as session:
        article = seed(session)[1]
        article.automation_next_check_at = utcnow() + timedelta(days=1)
        identifier = article.id
    with database.begin() as editor:
        editor.execute(select(Article.id).where(Article.id == identifier).with_for_update())
        assert expand_automation_demands(database, utcnow()) == 0
        with database() as observer:
            demand = observer.get(AutomationInvalidation, "all")
            assert demand.after_article_id is None and demand.scan_revision is None
    assert expand_automation_demands(database, utcnow()) == 1


def test_ineligible_due_prefix_is_parked_in_bounded_pages_and_source_approval_wakes_it(
    database, monkeypatch
):
    from devfeed_core.automation_demands import _enqueue_policy
    from devfeed_core.models import AutomationInvalidation, Source
    from sqlalchemy import delete

    today = utcnow().date()
    enable(monkeypatch, AUTOMATION_BATCH_SIZE=1, AI_CONTENT_NOT_BEFORE=today.isoformat())
    with database.begin() as session:
        source, waiting, _ = seed(session, name="Waiting")
        source.approval_status = "pending"
        _, historical, _ = seed(session, name="Historical")
        historical.discovered_at = utcnow() - timedelta(days=2)
        historical.published_at = historical.discovered_at
        _, admitted, _ = seed(session, name="Admitted")
        waiting.published_at = admitted.published_at = utcnow()
        session.flush()
        for index, article in enumerate((waiting, historical, admitted)):
            article.automation_next_check_at = utcnow() - timedelta(minutes=10 - index)
        waiting_id, historical_id, admitted_id, source_id = (
            waiting.id,
            historical.id,
            admitted.id,
            source.id,
        )
    # Establish policy, then measure due selection independently of its initial
    # catalog pass, which has separate bounded continuation coverage above.
    _enqueue_policy(database, get_settings())
    with database.begin() as session:
        session.execute(delete(AutomationInvalidation))
    prepared = []
    prepare = article_automation._prepare_article_candidates

    def capture(factory, identifier):
        prepared.append(identifier)
        return prepare(factory, identifier)

    monkeypatch.setattr(article_automation, "_prepare_article_candidates", capture)
    counts = [
        article_automation.schedule_article_automation(database)["articles_checked"]
        for _ in range(3)
    ]
    assert counts == [0, 0, 1]
    assert prepared == [admitted_id]
    with database.begin() as session:
        for identifier, reason in (
            (waiting_id, "source_admission"),
            (historical_id, "content_date_deferred"),
        ):
            article = session.get(Article, identifier)
            assert article.automation_wait_reason == reason
            assert article.automation_started_at is None
            assert article.automation_evaluated_revision == 0
        session.get(Source, source_id).approval_status = "approved"
    assert article_automation.schedule_article_automation(database)["articles_checked"] == 1
    assert prepared[-1] == waiting_id


def test_raw_dependency_owner_move_delete_and_terminal_result_edit_wake_current_owners(
    database, monkeypatch
):
    from devfeed_core.models import ArticleContent

    enable(monkeypatch)
    with database.begin() as session:
        _, first, topic = seed(session)
        job = ready(session, first, topic)
        _, second, _ = seed(session, name="Python")
        session.add(
            ArticleContent(
                article_id=first.id,
                url=first.canonical_url,
                text="Private source evidence",
                content_hash="evidence",
                method="body",
            )
        )
        session.flush()
        first.automation_next_check_at = second.automation_next_check_at = utcnow() + timedelta(
            days=1
        )
        first_id, second_id, job_id = first.id, second.id, job.id
    with database.begin() as session:
        session.execute(
            text("UPDATE article_contents SET article_id=:new WHERE article_id=:old"),
            {"new": second_id, "old": first_id},
        )
    with database.begin() as session:
        assert all(
            session.get(Article, key).automation_next_check_at <= utcnow()
            for key in (first_id, second_id)
        )
        for key in (first_id, second_id):
            session.get(Article, key).automation_next_check_at = utcnow() + timedelta(days=1)
    with database.begin() as session:
        session.execute(
            text("DELETE FROM article_contents WHERE article_id=:id"), {"id": second_id}
        )
        session.execute(
            text(
                "UPDATE article_analysis_jobs SET usage=usage || "
                "jsonb_build_object('billing_note',true) WHERE id=:id"
            ),
            {"id": job_id},
        )
        assert session.get(Article, first_id).automation_next_check_at > utcnow()
        session.execute(
            text(
                "UPDATE article_analysis_jobs "
                "SET result=result || jsonb_build_object('corrected',true) "
                "WHERE id=:id"
            ),
            {"id": job_id},
        )
    with database() as session:
        assert all(
            session.get(Article, key).automation_next_check_at <= utcnow()
            for key in (first_id, second_id)
        )


def test_proposal_research_metadata_does_not_invalidate_matching_but_identity_and_status_do(
    database, monkeypatch
):
    from devfeed_core.models import AutomationInvalidation, TopicProposal
    from sqlalchemy import delete

    enable(monkeypatch)
    with database.begin() as session:
        proposal = TopicProposal(
            batch_id=uuid.uuid4(),
            slug="angular",
            action="create",
            origin="import",
            source_name="Test",
            created_by={},
            proposed={"name": "Angular", "slug": "angular"},
        )
        session.add(proposal)
        session.flush()
        identifier = proposal.id
        session.execute(delete(AutomationInvalidation))
    with database.begin() as session:
        session.execute(
            text(
                "UPDATE topic_proposals SET proposed=proposed || "
                "jsonb_build_object('description','Researched context') WHERE id=:id"
            ),
            {"id": identifier},
        )
        assert session.scalar(select(AutomationInvalidation.key).limit(1)) is None
    with database.begin() as session:
        session.execute(
            text(
                "UPDATE topic_proposals SET proposed=proposed || "
                "jsonb_build_object('keywords',jsonb_build_array('routing')) WHERE id=:id"
            ),
            {"id": identifier},
        )
        assert session.get(AutomationInvalidation, "proposals") is not None
        session.execute(delete(AutomationInvalidation))
    with database.begin() as session:
        session.execute(
            text(
                "UPDATE topic_proposals SET status='rejected',"
                "reviewed_at=now(),reviewed_by='{}'::jsonb "
                "WHERE id=:id"
            ),
            {"id": identifier},
        )
        assert session.get(AutomationInvalidation, "proposals") is not None


def test_cascaded_article_tag_delete_wakes_its_article(database, monkeypatch):
    from devfeed_core.models import ArticleTag, Tag

    enable(monkeypatch)
    with database.begin() as session:
        _, article, _ = seed(session)
        tag = Tag(name="Routing", slug="routing")
        session.add(tag)
        session.flush()
        session.add(ArticleTag(article_id=article.id, tag_id=tag.id, origin="source"))
        session.flush()
        article.automation_next_check_at = utcnow() + timedelta(days=1)
        identifier, tag_id = article.id, tag.id
    with database.begin() as session:
        session.execute(text("DELETE FROM tags WHERE id=:id"), {"id": tag_id})
        assert session.get(Article, identifier).automation_next_check_at <= utcnow()


def test_tag_assignment_policy_wakes_only_linked_articles_in_bounded_pages(database, monkeypatch):
    from devfeed_core.automation_demands import _enqueue_policy, expand_automation_demands
    from devfeed_core.models import ArticleTag, AutomationInvalidation, Tag
    from sqlalchemy import delete

    enable(monkeypatch, AUTOMATION_BATCH_SIZE=1, AUTOMATION_INVALIDATION_BATCH_SIZE=1)
    with database.begin() as session:
        articles = [seed(session, name=f"Framework{index}")[1] for index in range(3)]
        tag = Tag(name="Routing", slug="routing", auto_link_topic=False)
        session.add(tag)
        session.flush()
        session.add_all(
            ArticleTag(article_id=article.id, tag_id=tag.id, origin="source")
            for article in articles[:2]
        )
        session.flush()
        for article in articles:
            article.automation_next_check_at = utcnow() + timedelta(days=1)
        linked = {article.id for article in articles[:2]}
        unrelated, tag_id = articles[2].id, tag.id
    _enqueue_policy(database, get_settings())
    with database.begin() as session:
        session.execute(delete(AutomationInvalidation))
    with database.begin() as session:
        session.execute(text("UPDATE tags SET auto_link_topic=true WHERE id=:id"), {"id": tag_id})
        demand = session.scalar(select(AutomationInvalidation))
        assert demand.key == f"tag:{tag_id}" and demand.tag_id == tag_id
    for _ in range(4):
        assert expand_automation_demands(database, utcnow()) <= 1
    with database() as session:
        assert all(session.get(Article, key).automation_next_check_at <= utcnow() for key in linked)
        assert session.get(Article, unrelated).automation_next_check_at > utcnow()
        assert session.scalar(select(AutomationInvalidation.key).limit(1)) is None


def test_bulk_proposal_update_that_removes_vocabulary_still_invalidates_dependencies(
    database, monkeypatch
):
    from devfeed_core.models import AutomationInvalidation, TopicProposal
    from sqlalchemy import delete

    enable(monkeypatch)
    with database.begin() as session:
        for slug in ("angular", "python"):
            session.add(
                TopicProposal(
                    batch_id=uuid.uuid4(),
                    slug=slug,
                    action="create",
                    origin="import",
                    source_name="Test",
                    created_by={},
                    proposed={"name": slug, "slug": slug},
                )
            )
        session.flush()
        session.execute(delete(AutomationInvalidation))
    with database.begin() as session:
        session.execute(
            text(
                "UPDATE topic_proposals "
                "SET proposed=jsonb_build_object('name','angular','slug','angular')"
            )
        )
        assert session.get(AutomationInvalidation, "proposals") is not None


def test_paywall_recovery_plan_limits_latest_history_probes_before_filtering(database, monkeypatch):
    from devfeed_core.models import ArticleEnrichmentJob

    enable(monkeypatch, AUTOMATION_BATCH_SIZE=2)
    with database.begin() as session:
        for index in range(20):
            _, article, _ = seed(session, name=f"Framework{index}")
            for age in range(5):
                session.add(
                    ArticleEnrichmentJob(
                        article_id=article.id,
                        status="succeeded",
                        result={"paywalled": False},
                        created_at=utcnow() - timedelta(days=age),
                    )
                )
    captured = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "paywall_candidates" in statement and "LATERAL" in statement:
            captured.append((statement, parameters))

    event.listen(get_engine(), "before_cursor_execute", capture)
    try:
        assert article_automation.reject_known_paywalled_articles(database) == 0
    finally:
        event.remove(get_engine(), "before_cursor_execute", capture)
    assert len(captured) == 1
    with get_engine().connect() as connection:
        statement, parameters = captured[0]
        plan = connection.exec_driver_sql(
            "EXPLAIN (ANALYZE, FORMAT JSON) " + statement, parameters
        ).scalar()[0]["Plan"]

    def walk(node):
        yield node
        for child in node.get("Plans", []):
            yield from walk(child)

    history_probes = [
        node for node in walk(plan) if node.get("Relation Name") == "article_enrichment_jobs"
    ]
    assert history_probes
    assert all(node["Actual Loops"] <= 2 for node in history_probes)
    assert any(
        node.get("CTE Name") == "paywall_candidates" and node["Actual Rows"] == 2
        for node in walk(plan)
    )


def test_job_completion_runs_within_reserved_capacity_while_catalog_debt_progresses(
    database, monkeypatch
):
    from devfeed_core.automation_demands import maintenance_lane
    from sqlalchemy import func

    full(monkeypatch, AUTOMATION_BATCH_SIZE=2, AUTOMATION_INVALIDATION_BATCH_SIZE=20)
    with database.begin() as session:
        for index in range(8):
            _, article, topic = seed(session, name=f"Framework{index}")
            job = ready(session, article, topic)
            job.status, job.error = "failed", "provider_unavailable"
        _, fresh, topic = seed(session, name="Fresh", mode="auto")
        fresh_job = ready(session, fresh, topic)
        fresh_job.status, fresh_job.finished_at = "running", None
        session.flush()
        for article in session.scalars(select(Article)):
            article.automation_next_check_at = utcnow() + timedelta(days=1)
            article.automation_wait_reason = "dependencies"
        fresh_id, fresh_job_id = fresh.id, fresh_job.id
    with database.begin() as session:
        finish_job(session.get(ArticleAnalysisJob, fresh_job_id), "applied", utcnow())
    counts = article_automation.schedule_article_automation(database)
    assert counts["articles_checked"] == 2 and counts["articles_published"] == 1
    with database() as session:
        assert session.get(Article, fresh_id).publication_status == "published"
        assert (
            session.scalar(
                select(func.count())
                .select_from(Article)
                .where(maintenance_lane() == 1, Article.automation_next_check_at <= utcnow())
            )
            == 7
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(Article)
                .where(
                    Article.automation_wait_reason == "dependencies",
                    Article.publication_status == "unpublished",
                    Article.automation_evaluated_revision == Article.automation_input_revision,
                )
            )
            == 1
        )


def test_source_policy_keeps_foreground_demand_alongside_active_catalog_work(database, monkeypatch):
    from devfeed_core.automation_demands import expand_automation_demands
    from devfeed_core.models import AutomationInvalidation

    enable(monkeypatch, AUTOMATION_INVALIDATION_BATCH_SIZE=1)
    with database.begin() as session:
        source, article, _ = seed(session)
        identifier, source_id = article.id, source.id
        article.automation_next_check_at = utcnow() + timedelta(days=1)
        article.automation_wait_reason = "dependencies"
        source.publication_policy = "preview"
    with database() as session:
        assert set(session.scalars(select(AutomationInvalidation.key))) == {
            "all",
            "catalog:tags",
            "catalog:topics",
            f"source:{source_id}",
        }
    for _ in range(3):
        expand_automation_demands(database, utcnow())
    with database() as session:
        article = session.get(Article, identifier)
        assert article.automation_wait_reason is None
        assert article.automation_next_check_at <= utcnow()


@pytest.mark.parametrize("override,expected", [(None, 1), (3, 3)])
def test_article_capacity_override_inherits_or_increases_only_article_observations(
    database, monkeypatch, override, expected
):
    monkeypatch.delenv("DEVFEED_ARTICLE_AUTOMATION_BATCH_SIZE", raising=False)
    full(monkeypatch, AUTOMATION_BATCH_SIZE=1)
    if override is not None:
        monkeypatch.setenv("DEVFEED_ARTICLE_AUTOMATION_BATCH_SIZE", str(override))
        get_settings.cache_clear()
    monkeypatch.setattr(article_automation, "expand_automation_demands", lambda *_: 0)
    with database.begin() as session:
        for index in range(4):
            _, article, topic = seed(session, name=f"Capacity{index}")
            job = ready(session, article, topic)
            job.status, job.error = "failed", "provider_unavailable"
    counts = article_automation.schedule_article_automation(database)
    assert counts["articles_checked"] == expected
    assert get_settings().automation_batch_size == 1
    with database() as session:
        due = session.scalars(
            select(Article).where(Article.automation_next_check_at <= utcnow())
        ).all()
        assert len(due) == 4 - expected
        assert all(row.automation_evaluated_revision < row.automation_input_revision for row in due)


@pytest.mark.parametrize("observations_per_cycle", [1, 2])
def test_article_deadline_keeps_unprocessed_demand_and_progresses_both_lanes(
    database, monkeypatch, observations_per_cycle
):
    full(
        monkeypatch,
        AUTOMATION_BATCH_SIZE=1,
        ARTICLE_AUTOMATION_BATCH_SIZE=4,
        ARTICLE_AUTOMATION_CYCLE_SECONDS=observations_per_cycle,
    )
    monkeypatch.setattr(article_automation, "expand_automation_demands", lambda *_: 0)
    monkeypatch.setattr(article_automation, "_single_candidate_foreground", True)
    lanes = {}
    with database.begin() as session:
        due_at = utcnow() - timedelta(minutes=1)
        for lane in ("foreground", "maintenance"):
            for index in range(4):
                _, article, topic = seed(session, name=f"{lane}{index}")
                job = ready(session, article, topic)
                job.status, job.error = "failed", "provider_unavailable"
                session.flush()
                article.automation_next_check_at = due_at + timedelta(seconds=index)
                article.automation_wait_reason = "maintenance" if lane == "maintenance" else None
                lanes[article.id] = lane
    with database() as session:
        before = {
            row.id: (row.automation_input_revision, row.automation_evaluated_revision)
            for row in session.scalars(select(Article))
        }
    clock, observed = [0.0], []
    prepare = article_automation._prepare_article_candidates

    def timed_prepare(factory, identifier):
        result = prepare(factory, identifier)
        observed.append(identifier)
        clock[0] += 1
        return result

    monkeypatch.setattr(article_automation, "monotonic", lambda: clock[0])
    monkeypatch.setattr(article_automation, "_prepare_article_candidates", timed_prepare)
    for cycle in range(8 // observations_per_cycle):
        clock[0] = 0
        start = len(observed)
        assert (
            article_automation.schedule_article_automation(database)["articles_checked"]
            == observations_per_cycle
        )
        this_cycle = observed[start:]
        assert len(set(observed)) == len(observed)
        if observations_per_cycle == 2:
            assert {lanes[identifier] for identifier in this_cycle} == {"foreground", "maintenance"}
        else:
            assert lanes[this_cycle[0]] == ("foreground" if cycle % 2 == 0 else "maintenance")
        with database() as session:
            for row in session.scalars(select(Article)):
                if row.id not in observed:
                    assert row.automation_next_check_at <= utcnow()
                    assert (
                        row.automation_input_revision,
                        row.automation_evaluated_revision,
                    ) == before[row.id]
    assert len(observed) == 8
    with database.begin() as session:
        # A new semantic wake after an observation must still be processed again.
        session.get(Article, observed[0]).summary = "New authoritative evidence after evaluation"
    clock[0] = 0
    assert article_automation.schedule_article_automation(database)["articles_checked"] == 1
    assert observed[-1] == observed[0]
    with database() as session:
        article = session.get(Article, observed[0])
        assert article.automation_evaluated_revision == article.automation_input_revision
