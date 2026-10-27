"""Application commits send narrow analytics changes, including bulk writes."""

import os
import uuid
from datetime import UTC, datetime

import pytest
from devfeed_core import analytics_producer as producer
from devfeed_core.cache_events import AppSession
from devfeed_core.db import get_engine  # noqa: F401 -- installs application producer integration
from sqlalchemy import (
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    Uuid,
    create_engine,
    delete,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import registry


@pytest.fixture
def source_engine(monkeypatch, integration_environment):
    url = os.environ.get("DEVFEED_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set DEVFEED_TEST_DATABASE_URL for producer integration")
    if not url.rsplit("/", 1)[-1].endswith("_test"):
        pytest.fail("Producer integration requires a disposable _test database")
    engine = create_engine(url)
    schema = "producer_" + uuid.uuid4().hex
    with engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    metadata = MetaData(schema=schema)
    articles = Table(
        "articles",
        metadata,
        Column("id", Uuid, primary_key=True, default=uuid.uuid4),
        Column("discovered_at", DateTime(timezone=True)),
        Column("published_to_feed_at", DateTime(timezone=True)),
        Column("publication_status", String),
        Column("content_type", String),
    )
    origins = Table(
        "article_origins",
        metadata,
        Column("id", Uuid, primary_key=True, default=uuid.uuid4),
        Column("article_id", Uuid),
        Column("source_id", Uuid),
    )
    metadata.create_all(engine)
    from devfeed_core import cache_events

    monkeypatch.setattr(cache_events, "invalidate_public_cache", lambda **_kwargs: None)
    emitted = []
    monkeypatch.setattr(producer, "submit", emitted.append)
    yield engine, articles, origins, emitted
    with engine.begin() as connection:
        connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
    engine.dispose()


@pytest.mark.integration
def test_orm_and_bulk_commits_capture_old_and_new_association_owners(source_engine):
    engine, articles, origins, emitted = source_engine
    mapper = registry()

    class Article:
        pass

    mapper.map_imperatively(Article, articles)
    a, b, origin, source = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with AppSession(engine) as session:
        item = Article()
        item.id, item.discovered_at, item.publication_status, item.content_type = (
            a,
            datetime.now(UTC),
            "published",
            "article",
        )
        session.add(item)
        session.execute(
            insert(articles).values(
                id=b,
                discovered_at=datetime.now(UTC),
                publication_status="published",
                content_type="article",
            )
        )
        session.execute(insert(origins).values(id=origin, article_id=a, source_id=source))
        session.commit()
        assert emitted and any(row["source_table"] == "articles" for row in emitted[-1])
        emitted.clear()
        session.execute(update(origins).where(origins.c.id == origin).values(article_id=b))
        session.commit()
        change = next(row for row in emitted[-1] if row["source_table"] == "article_origins")
        assert change["before_record"]["article_id"] == str(a)
        assert str(change["identity"]["id"]) == str(origin)
        # The producer hydrates the new B row after commit, retaining old A.
        assert session.scalar(select(origins.c.article_id)) == b
    mapper.dispose()


@pytest.mark.integration
def test_rollback_savepoint_noop_and_upsert_preserve_commit_boundary(source_engine):
    engine, articles, origins, emitted = source_engine
    identity = uuid.uuid4()
    values = dict(
        id=identity,
        discovered_at=datetime.now(UTC),
        publication_status="pending",
        content_type="article",
    )
    with AppSession(engine) as session:
        session.execute(insert(articles).values(**values))
        session.rollback()
        assert not emitted
        session.execute(insert(articles).values(**values))
        session.commit()
        emitted.clear()
        with session.begin_nested() as nested:
            session.execute(
                update(articles)
                .where(articles.c.id == identity)
                .values(publication_status="published")
            )
            nested.rollback()
        session.commit()
        assert not emitted
        statement = (
            pg_insert(articles)
            .values(**values)
            .on_conflict_do_nothing(index_elements=[articles.c.id])
        )
        session.execute(statement)
        session.commit()
        assert not emitted
        session.execute(
            update(articles).where(articles.c.id == identity).values(publication_status="published")
        )
        session.execute(delete(origins).where(origins.c.article_id == identity))
        session.commit()
        assert len(emitted) == 1
        assert all(row["source_table"] == "articles" for row in emitted[0])


@pytest.fixture
def writer_runtime(monkeypatch):
    import threading
    from types import SimpleNamespace

    started, release = threading.Event(), threading.Event()
    batches, closed = [], []
    state = SimpleNamespace(block=False, fail=False)

    class Client:
        def __init__(self, _settings):
            pass

        async def close(self):
            closed.append(True)

    class Publisher:
        def __init__(self, *_args):
            pass

        async def publish(self, changes):
            started.set()
            if state.block:
                assert release.wait(2), "Test did not release the blocked delivery"
            batches.append(changes)
            if state.fail:
                raise RuntimeError("delivery unavailable")

    monkeypatch.setattr(producer, "AnalyticsClient", Client)
    monkeypatch.setattr(producer, "DirectAnalyticsPublisher", Publisher)
    monkeypatch.setattr(producer, "MAX_PENDING_TRANSACTIONS", 1)
    monkeypatch.setattr(producer, "_producer", None)
    yield state, started, release, batches, closed
    release.set()
    producer.shutdown(2)


def article_change(index):
    return [{"source_table": "articles", "identity": {"id": str(uuid.UUID(int=index))}}]


def test_shutdown_drains_a_full_buffer_and_preserves_a_bounded_deadline(writer_runtime, caplog):
    state, started, release, batches, closed = writer_runtime
    state.block = True
    writer = producer.AnalyticsProducer(object())
    try:
        assert writer.submit(article_change(1))
        assert started.wait(1)
        assert writer.submit(article_change(2))
        assert not writer.submit(article_change(3))
        assert not writer.shutdown(0)
        assert not writer.submit(article_change(4))
        release.set()
        assert writer.shutdown(2)
        assert writer.pending.unfinished_tasks == 0
        assert [row["identity"]["id"] for batch in batches for row in batch] == [
            str(uuid.UUID(int=1)),
            str(uuid.UUID(int=2)),
        ]
        assert closed == [True]
        assert {record.message for record in caplog.records} >= {
            "analytics_direct_buffer_full",
            "analytics_direct_drain_timed_out",
            "analytics_direct_writer_stopping",
        }
    finally:
        release.set()
        writer.shutdown(2)


@pytest.mark.parametrize("fail", [False, True])
def test_shutdown_delivers_accepted_work_once_and_closes_the_client(writer_runtime, fail):
    state, _, _, batches, closed = writer_runtime
    state.fail = fail
    writer = producer.AnalyticsProducer(object())
    try:
        assert writer.submit(article_change(1))
        assert writer.shutdown(2)
        assert batches == [article_change(1)]
        assert writer.pending.unfinished_tasks == 0 and closed == [True]
        assert writer.shutdown(0)
    finally:
        writer.shutdown(2)


def test_global_shutdown_is_offline_and_restarts_only_after_a_completed_drain(
    writer_runtime, monkeypatch
):
    from unittest.mock import Mock

    assert producer.shutdown()
    settings = Mock(return_value=object())
    monkeypatch.setattr("devfeed_core.config.get_settings", settings)
    assert producer.submit(article_change(1))
    first = producer._producer
    assert producer.shutdown(2)
    assert producer._producer is None
    assert producer.submit(article_change(2))
    assert producer._producer is not first
    assert producer.shutdown(2)
    assert settings.call_count == 2


@pytest.mark.filterwarnings("ignore:This process.*multi-threaded:DeprecationWarning")
def test_fork_discards_inherited_writer_and_locked_synchronization(writer_runtime):
    import select as select_io
    import signal
    import threading

    producer._producer = object()
    producer._lock = threading.Lock()
    read_fd, write_fd = os.pipe()
    child = None
    try:
        with producer._lock:
            child = os.fork()
            if child == 0:
                os.close(read_fd)
                try:
                    assert producer._producer is None
                    assert producer._lock.acquire(blocking=False)
                    producer._lock.release()
                    os.write(write_fd, b"reset")
                finally:
                    os._exit(0)
        os.close(write_fd)
        write_fd = None
        assert select_io.select([read_fd], [], [], 2)[0], "Fork child inherited a locked producer"
        assert os.read(read_fd, 32) == b"reset"
    finally:
        os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)
        if child:
            finished, _ = os.waitpid(child, os.WNOHANG)
            if not finished:
                os.kill(child, signal.SIGKILL)
                os.waitpid(child, 0)
        producer._producer = None


def test_shutdown_wakes_an_idle_writer_and_skips_foreign_processes(writer_runtime, monkeypatch):
    _, _, _, batches, closed = writer_runtime
    writer = producer.AnalyticsProducer(object())
    assert writer.shutdown(2)
    assert batches == [] and closed == [True]
    monkeypatch.setattr(writer, "pid", -1)
    assert writer.shutdown(0)


def test_shutdown_sentinel_after_queued_work_does_not_discard_the_last_batch(
    writer_runtime, monkeypatch
):
    state, started, release, batches, closed = writer_runtime
    monkeypatch.setattr(producer, "MAX_PENDING_TRANSACTIONS", 3)
    state.block = True
    writer = producer.AnalyticsProducer(object())
    try:
        assert writer.submit(article_change(1))
        assert started.wait(1)
        assert writer.submit(article_change(2))
        assert not writer.shutdown(0)
        release.set()
        assert writer.shutdown(2)
        assert batches == [article_change(1), article_change(2)]
        assert writer.pending.unfinished_tasks == 0 and closed == [True]
    finally:
        release.set()
        writer.shutdown(2)


def test_attempt_capture_keeps_completion_before_requeue_and_original_owner():
    from types import SimpleNamespace

    session = SimpleNamespace(info={})
    first = {"id": str(uuid.uuid4()), "status": "running", "attempts": 1}
    completed = first | {"status": "succeeded", "finished_at": "2026-10-27T00:00:00Z"}
    queued = first | {"status": "queued", "attempts": 2}
    producer._remember(session, "article_analysis_jobs", first)
    producer._remember(session, "article_analysis_jobs", completed)
    producer._remember(session, "article_analysis_jobs", queued)
    producer._remember(session, "untracked_table", {"id": "ignored"})
    producer._remember(session, "articles", None)
    changes = list(session.info[producer._PENDING].values())
    assert len(changes) == 1
    assert changes[0]["before_record"] == first
    assert changes[0]["attempts"] == [completed]
    assert changes[0]["identity"] == {"id": first["id"]}


def test_bound_update_predicates_keep_old_primary_key_separate_from_new_values():
    from sqlalchemy.dialects import postgresql

    table = Table("articles", MetaData(), Column("id", Uuid, primary_key=True))
    old, new = uuid.uuid4(), uuid.uuid4()
    statement = table.update().where(table.c.id == old).values(id=new)
    compiled = statement.compile(dialect=postgresql.dialect())
    predicate = producer._bound_predicate(statement, compiled, compiled.params)
    assert predicate is not None
    bound = predicate.compile(dialect=postgresql.dialect())
    assert list(bound.params.values()) == [old]
    assert producer._bound_predicate(table.update().values(id=new), None, {}) is None


def test_hydration_rebuilds_both_association_owners_and_deleted_feed_tombstones(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock

    old, new, deleted_user, current_user = [str(uuid.uuid4()) for _ in range(4)]
    publications = {new: {"article_id": new, "deleted": False}}
    feeds = {current_user: {"user_id": current_user, "deleted": False}}
    publish = AsyncMock(return_value=publications)
    feed = AsyncMock(return_value=feeds)
    monkeypatch.setattr(producer, "_publication_rows", publish)
    monkeypatch.setattr(producer, "_feed_rows", feed)
    changes = [
        {
            "source_table": "article_origins",
            "identity": {"id": str(uuid.uuid4())},
            "record": {"article_id": new},
            "before_record": {"article_id": old},
        },
        {
            "source_table": "user_topics",
            "identity": {"user_id": current_user},
            "before_record": {"user_id": deleted_user},
        },
        {"source_table": "sources", "identity": {"id": str(uuid.uuid4())}},
    ]
    connection = object()
    hydrated = asyncio.run(producer.hydrate_changes(connection, changes))
    publish.assert_awaited_once_with(connection, {old, new})
    feed.assert_awaited_once_with(connection, {deleted_user, current_user})
    assert hydrated[:3] == changes
    states = {row["identity"].get("id"): row["publication"] for row in hydrated[3:5]}
    assert states == {old: {"article_id": old, "deleted": True}, new: publications[new]}
    assert {row["identity"]["user_id"]: row["feed"] for row in hydrated[5:]} == {
        deleted_user: {"user_id": deleted_user, "deleted": True},
        current_user: feeds[current_user],
    }


def test_current_source_snapshot_batches_composite_keys_and_preserves_old_attempts():
    import asyncio

    old, current, user = [str(uuid.uuid4()) for _ in range(3)]
    complete = {"id": current, "status": "succeeded", "attempts": 1}
    calls = []

    class Result:
        def __init__(self, rows):
            self.rows = rows

        async def fetchall(self):
            return self.rows

    class Connection:
        async def execute(self, query, parameters):
            calls.append((query.as_string(), parameters))
            if "user_topics" in query.as_string():
                return Result([{"record": {"user_id": user, "topic_id": current}}])
            return Result([{"record": {"id": current, "status": "queued", "attempts": 2}}])

    changes = [
        {"source_table": "article_analysis_jobs", "identity": {"id": old}},
        {
            "source_table": "article_analysis_jobs",
            "identity": {"id": current},
            "attempts": [complete],
        },
        {"source_table": "user_topics", "identity": {"user_id": user, "topic_id": current}},
    ]
    result = asyncio.run(producer.read_current_changes(Connection(), changes))
    assert len(calls) == 2
    assert calls[0][1] == (old, current) and calls[1][1] == (user, current)
    assert result[0]["record"] is None
    assert result[1]["record"]["status"] == "queued"
    assert result[2] == {
        "source_table": "article_analysis_jobs",
        "record": complete,
        "attempt_only": True,
    }
    assert result[3]["record"] == {"user_id": user, "topic_id": current}


def test_publication_and_feed_rows_shape_authoritative_inputs_and_skip_empty_queries():
    import asyncio

    article, source, topic, user = [uuid.uuid4() for _ in range(4)]
    now = datetime.now(UTC)
    calls = []

    class Result:
        def __init__(self, rows):
            self.rows = rows

        async def fetchall(self):
            return self.rows

    class Connection:
        async def execute(self, query, parameters):
            calls.append((query, parameters))
            if "FROM articles a" in query:
                return Result(
                    [
                        {
                            "id": article,
                            "published_to_feed_at": now,
                            "discovered_at": now,
                            "source_ids": [source],
                            "topic_ids": [topic],
                            "automated": True,
                        }
                    ]
                )
            return Result(
                [
                    {
                        "id": user,
                        "interest_count": 1,
                        "generation": None,
                        "invalidated": None,
                        "has_recommendations": False,
                        "expires_at": None,
                        "next_refresh_at": now,
                    }
                ]
            )

    async def run():
        connection = Connection()
        assert await producer._publication_rows(connection, set()) == {}
        assert await producer._feed_rows(connection, set()) == {}
        publication = await producer._publication_rows(connection, {str(article)})
        feed = await producer._feed_rows(connection, {str(user)})
        assert publication[str(article)] == {
            "article_id": str(article),
            "published_at": now,
            "discovered_at": now,
            "source_ids": [str(source)],
            "topic_ids": [str(topic)],
            "automated": True,
            "deleted": False,
        }
        assert feed[str(user)] == {
            "user_id": str(user),
            "interest_count": 1,
            "has_generation": False,
            "invalidated": False,
            "has_recommendations": False,
            "expires_at": None,
            "next_refresh_at": now,
            "deleted": False,
        }

    asyncio.run(run())
    assert len(calls) == 2


@pytest.mark.integration
@pytest.mark.parametrize("bulk_mode", ["execute_many", "values_list"])
def test_core_bulk_insert_captures_every_inserted_identity_without_consuming_returning(
    source_engine, bulk_mode
):
    engine, articles, _, emitted = source_engine
    identities = [uuid.uuid4(), uuid.uuid4()]
    values = [
        dict(
            id=value,
            discovered_at=datetime.now(UTC),
            publication_status="pending",
            content_type="article",
        )
        for value in identities
    ]
    with AppSession(engine) as session:
        if bulk_mode == "execute_many":
            result = session.execute(insert(articles).returning(articles.c.id), values)
        else:
            result = session.execute(insert(articles).values(values).returning(articles.c.id))
        assert set(result.scalars()) == set(identities)
        assert not emitted
        session.commit()
    assert {str(row["identity"]["id"]) for batch in emitted for row in batch} == set(
        map(str, identities)
    )


@pytest.mark.integration
def test_primary_key_update_emits_old_tombstone_and_new_owner(source_engine):
    engine, articles, _, emitted = source_engine
    old, new = uuid.uuid4(), uuid.uuid4()
    with AppSession(engine) as session:
        session.execute(
            insert(articles).values(
                id=old,
                discovered_at=datetime.now(UTC),
                publication_status="pending",
                content_type="article",
            )
        )
        session.commit()
        emitted.clear()
        session.execute(update(articles).where(articles.c.id == old).values(id=new))
        session.commit()
        assert session.scalar(select(articles.c.id)) == new
    assert {str(row["identity"]["id"]) for row in emitted[0]} == {str(old), str(new)}
    previous = next(row for row in emitted[0] if str(row["identity"]["id"]) == str(old))
    assert previous["before_record"]["id"] == str(old)


@pytest.mark.integration
def test_nested_commit_and_rollback_preserve_only_outer_committed_changes(source_engine):
    engine, articles, _, emitted = source_engine
    first, rolled_back, nested_commit = [uuid.uuid4() for _ in range(3)]

    def add(session, identity):
        session.execute(
            insert(articles).values(
                id=identity,
                discovered_at=datetime.now(UTC),
                publication_status="pending",
                content_type="article",
            )
        )

    with AppSession(engine) as session:
        add(session, first)
        with session.begin_nested() as nested:
            add(session, rolled_back)
            nested.rollback()
        with session.begin_nested():
            add(session, nested_commit)
        assert not emitted
        session.commit()
    assert len(emitted) == 1
    assert {str(row["identity"]["id"]) for row in emitted[0]} == {str(first), str(nested_commit)}


@pytest.mark.integration
def test_conflict_update_captures_previous_fields_and_preserves_returned_rows(source_engine):
    engine, articles, _, emitted = source_engine
    identity = uuid.uuid4()
    values = dict(
        id=identity,
        discovered_at=datetime.now(UTC),
        publication_status="pending",
        content_type="article",
    )
    with AppSession(engine) as session:
        session.execute(insert(articles).values(**values))
        session.commit()
        emitted.clear()
        result = session.execute(
            pg_insert(articles)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[articles.c.id], set_={"publication_status": "published"}
            )
            .returning(articles.c.id)
        )
        assert result.scalar_one() == identity
        session.commit()
    assert len(emitted) == 1 and len(emitted[0]) == 1
    assert emitted[0][0]["before_record"]["publication_status"] == "pending"


@pytest.mark.integration
def test_completed_job_attempt_survives_a_same_transaction_requeue(database, monkeypatch):
    from devfeed_core.models import Article, ArticleAnalysisJob

    emitted = []
    monkeypatch.setattr(producer, "submit", emitted.append)
    with database.begin() as session:
        article = Article(
            canonical_url="https://producer.test/job", url_hash="producer-job", title="Job fixture"
        )
        session.add(article)
        session.flush()
        job = ArticleAnalysisJob(article_id=article.id, status="running", attempts=1)
        session.add(job)
        session.flush()
        job.status, job.finished_at = "succeeded", datetime.now(UTC)
        job.usage = {"totalTokens": 7, "private_prompt": "must stay in PostgreSQL"}
        session.flush()
        job.status, job.finished_at, job.attempts = "queued", None, 2
    change = next(
        row for batch in emitted for row in batch if row["source_table"] == "article_analysis_jobs"
    )
    assert change["attempts"]
    assert {row["status"] for row in change["attempts"]} == {"succeeded"}
    assert {row["attempts"] for row in change["attempts"]} == {1}
    assert all(row["usage"]["totalTokens"] == "7" for row in change["attempts"])
    assert all("private_prompt" not in row["usage"] for row in change["attempts"])


@pytest.fixture
def publication_inputs(database, monkeypatch):
    from datetime import timedelta

    from devfeed_core.models import (
        Article,
        ArticleOrigin,
        ArticleReview,
        ArticleTopic,
        Source,
        Topic,
        UserAccount,
        UserRecommendation,
        UserRecommendationState,
        UserTopic,
    )

    emitted = []
    monkeypatch.setattr(producer, "submit", emitted.append)
    now = datetime.now(UTC)
    ids = {name: uuid.uuid4() for name in ("a", "b", "source", "topic", "user", "origin", "review")}
    with database.begin() as session:
        session.add(
            Source(
                id=ids["source"],
                name="Producer source",
                source_type="publisher",
                feed_url="https://producer.test/rss",
                approval_status="approved",
            )
        )
        session.add(
            Topic(
                id=ids["topic"],
                name="Producer topic",
                slug="producer-topic",
                kind="technology",
                status="active",
            )
        )
        session.add(
            UserAccount(
                id=ids["user"],
                issuer="https://producer.test/id",
                subject="reader",
                organization_id="producer",
            )
        )
        for name in ("a", "b"):
            session.add(
                Article(
                    id=ids[name],
                    canonical_url=f"https://producer.test/{name}",
                    url_hash=f"producer-{name}",
                    title=f"Producer {name}",
                    published_to_feed_at=now,
                    publication_status="published",
                    review_status="approved",
                )
            )
        session.flush()
        session.add(
            ArticleOrigin(
                id=ids["origin"],
                article_id=ids["a"],
                source_id=ids["source"],
                entry_key="producer-entry",
                original_url="https://producer.test/a",
            )
        )
        session.add(
            ArticleTopic(
                article_id=ids["a"],
                topic_id=ids["topic"],
                role="primary",
                relevance=1.0,
                evidence="Test",
            )
        )
        session.add(
            ArticleReview(
                id=ids["review"],
                article_id=ids["a"],
                action="publish",
                revision=1,
                automation={"rule": "test"},
                created_at=now - timedelta(seconds=1),
            )
        )
        session.add(UserTopic(user_id=ids["user"], topic_id=ids["topic"]))
        session.flush()
        state_values = dict(
            user_id=ids["user"],
            generation=uuid.uuid4(),
            interest_count=1,
            invalidated=False,
            expires_at=now + timedelta(hours=1),
            next_refresh_at=now - timedelta(minutes=1),
        )
        session.execute(
            pg_insert(UserRecommendationState)
            .values(**state_values)
            .on_conflict_do_update(
                index_elements=[UserRecommendationState.user_id], set_=state_values
            )
        )
        session.add(
            UserRecommendation(
                user_id=ids["user"],
                article_id=ids["a"],
                position=0,
                score=1,
                reason="followed_topic",
            )
        )
    return ids, emitted


@pytest.mark.integration
def test_composite_association_primary_key_move_captures_both_article_identities(
    publication_inputs, database
):
    from devfeed_core.models import ArticleTopic

    ids, emitted = publication_inputs
    emitted.clear()
    with database.begin() as session:
        session.execute(
            update(ArticleTopic)
            .where(ArticleTopic.article_id == ids["a"], ArticleTopic.topic_id == ids["topic"])
            .values(article_id=ids["b"])
        )
    keys = {
        (str(change["identity"]["article_id"]), str(change["identity"]["topic_id"]))
        for batch in emitted
        for change in batch
        if change["source_table"] == "article_topics"
    }
    assert keys == {(str(ids["a"]), str(ids["topic"])), (str(ids["b"]), str(ids["topic"]))}


@pytest.mark.integration
def test_topic_delete_captures_cascaded_memberships_and_set_null_proposals(
    publication_inputs, database
):
    from devfeed_core.models import Topic, TopicProposal

    ids, emitted = publication_inputs
    with database.begin() as session:
        proposal = TopicProposal(
            batch_id=uuid.uuid4(),
            topic_id=ids["topic"],
            slug="producer-proposal",
            action="update",
            status="pending",
            origin="import",
            source_name="Producer fixture",
            created_by={},
            proposed={},
            evidence=[],
        )
        session.add(proposal)
        session.flush()
        proposal_id = proposal.id
    emitted.clear()
    with database.begin() as session:
        session.execute(delete(Topic).where(Topic.id == ids["topic"]))
    captured = {change["source_table"] for batch in emitted for change in batch}
    assert captured >= {"topics", "article_topics", "articles", "user_topics", "topic_proposals"}
    proposals = [
        change
        for batch in emitted
        for change in batch
        if change["source_table"] == "topic_proposals"
    ]
    assert any(str(change["identity"]["id"]) == str(proposal_id) for change in proposals)
    with database() as session:
        assert session.get(TopicProposal, proposal_id).topic_id is None


@pytest.mark.integration
def test_direct_publisher_updates_clickhouse_publication_owners_and_feed_state(
    publication_inputs, database
):
    import asyncio

    from devfeed_core.analytics import AnalyticsClient
    from devfeed_core.models import (
        ArticleOrigin,
        ArticleReview,
        ArticleTopic,
        UserRecommendation,
        UserRecommendationState,
        UserTopic,
    )

    from tests.support.analytics import settings as analytics_settings

    ids, emitted = publication_inputs
    settings = analytics_settings()
    target = f"producer_{uuid.uuid4().hex}_test"

    async def run():
        async with AnalyticsClient(
            settings.model_copy(update={"clickhouse_database": "default"})
        ) as admin:
            await admin.execute(f"CREATE DATABASE `{target}`")
            try:
                async with AnalyticsClient(
                    settings.model_copy(update={"clickhouse_database": target})
                ) as native:
                    native.insert_settings = {"async_insert_busy_timeout_ms": 1}
                    await native.initialize()
                    publisher = producer.DirectAnalyticsPublisher(native, settings)
                    for batch in emitted:
                        await publisher.publish(batch)
                    initial = await native.query(
                        "SELECT toString(id) AS id,source_ids,topic_ids,automated "
                        "FROM analytics_pipeline_events FINAL WHERE domain='publication'"
                    )
                    first = next(row for row in initial if row["id"] == str(ids["a"]))
                    assert first["source_ids"] == [str(ids["source"])]
                    assert first["topic_ids"] == [str(ids["topic"])] and first["automated"] == 1
                    feed = await native.query(
                        "SELECT interest_count,has_generation,has_recommendations FROM "
                        "analytics_user_events FINAL WHERE domain='feed' AND id={user:UUID}",
                        {"user": str(ids["user"])},
                    )
                    assert feed == [
                        {"interest_count": 1, "has_generation": 1, "has_recommendations": 1}
                    ]
                    emitted.clear()
                    with database.begin() as session:
                        session.execute(
                            update(ArticleOrigin)
                            .where(ArticleOrigin.id == ids["origin"])
                            .values(article_id=ids["b"])
                        )
                        session.execute(
                            update(ArticleTopic)
                            .where(ArticleTopic.article_id == ids["a"])
                            .values(article_id=ids["b"])
                        )
                        session.execute(
                            update(ArticleReview)
                            .where(ArticleReview.id == ids["review"])
                            .values(article_id=ids["b"])
                        )
                        session.execute(delete(UserTopic).where(UserTopic.user_id == ids["user"]))
                        session.execute(
                            delete(UserRecommendation).where(
                                UserRecommendation.user_id == ids["user"]
                            )
                        )
                        session.execute(
                            update(UserRecommendationState)
                            .where(UserRecommendationState.user_id == ids["user"])
                            .values(interest_count=0)
                        )
                    for batch in emitted:
                        await publisher.publish(batch)
                    rows = await native.query(
                        "SELECT toString(id) AS id,source_ids,topic_ids,automated "
                        "FROM analytics_pipeline_events FINAL WHERE domain='publication'"
                    )
                    rows = {row["id"]: row for row in rows}
                    assert rows[str(ids["a"])]["source_ids"] == []
                    assert rows[str(ids["a"])]["topic_ids"] == []
                    assert rows[str(ids["a"])]["automated"] == 0
                    assert rows[str(ids["b"])]["source_ids"] == [str(ids["source"])]
                    assert rows[str(ids["b"])]["topic_ids"] == [str(ids["topic"])]
                    assert rows[str(ids["b"])]["automated"] == 1
                    feed = await native.query(
                        "SELECT interest_count,has_recommendations FROM analytics_user_events "
                        "FINAL WHERE domain='feed' AND id={user:UUID}",
                        {"user": str(ids["user"])},
                    )
                    assert feed == [{"interest_count": 0, "has_recommendations": 0}]
                    cards = await native.query(
                        "SELECT toString(entity_id) AS id FROM "
                        "analytics_card_canonical FINAL WHERE kind='article'"
                    )
                    assert {row["id"] for row in cards} == {str(ids["a"]), str(ids["b"])}
            finally:
                await admin.execute(f"DROP DATABASE `{target}` SYNC")

    asyncio.run(run())


def test_parent_delete_collects_only_supported_fk_children_before_database_cascade(monkeypatch):
    from sqlalchemy import ForeignKey

    metadata = MetaData()
    topics = Table("topics", metadata, Column("id", Uuid, primary_key=True))
    Table(
        "article_topics",
        metadata,
        Column("article_id", Uuid, primary_key=True),
        Column("topic_id", Uuid, ForeignKey(topics.c.id, ondelete="CASCADE"), primary_key=True),
    )
    Table(
        "topic_proposals",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("topic_id", Uuid, ForeignKey(topics.c.id, ondelete="SET NULL")),
    )
    Table(
        "untracked_notes",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("topic_id", Uuid, ForeignKey(topics.c.id, ondelete="CASCADE")),
    )
    Table(
        "articles",
        metadata,
        Column("id", Uuid, primary_key=True),
        Column("topic_id", Uuid, ForeignKey(topics.c.id, ondelete="RESTRICT")),
    )
    identity = uuid.uuid4()
    calls = []

    def records(_connection, table, predicate):
        calls.append((table.name, predicate.compile().params))
        return [{"id": identity}]

    monkeypatch.setattr(producer, "_records", records)
    result = producer._cascaded_records(object(), topics, [{"id": identity}])
    assert {source for source, _ in result} == {"article_topics", "topic_proposals"}
    assert {name for name, _ in calls} == {"article_topics", "topic_proposals"}
    assert all(identity in params.values() for _, params in calls)
    assert producer._cascaded_records(object(), topics, []) == []


def test_source_mutations_refresh_attribution_only_when_source_visibility_changes():
    from types import SimpleNamespace
    from unittest.mock import Mock

    identity, article = uuid.uuid4(), uuid.uuid4()
    connection = Mock()
    connection.execute.return_value.scalars.return_value = [article]
    updating = SimpleNamespace(is_delete=False)
    deleting = SimpleNamespace(is_delete=True)
    assert producer._dependent_articles(
        connection, "sources", [{"id": identity}], updating, [{"name": "Renamed"}]
    ) == [article]
    assert producer._dependent_articles(connection, "topics", [{"id": identity}], deleting, []) == [
        article
    ]
    assert (
        producer._dependent_articles(
            connection,
            "sources",
            [{"id": identity}],
            updating,
            [{"last_success_at": datetime.now(UTC)}],
        )
        == []
    )
    assert (
        producer._dependent_articles(
            connection, "topics", [{"id": identity}], updating, [{"name": "Rename"}]
        )
        == []
    )
    assert (
        producer._dependent_articles(connection, "articles", [{"id": identity}], deleting, []) == []
    )
    assert connection.execute.call_count == 2


@pytest.mark.parametrize("revision", [None, 42])
def test_publisher_allocates_ordered_revision_and_bounds_hydrated_insert_batches(
    monkeypatch, revision
):
    import asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import devfeed_core.analytics_direct_cards as cards

    changes = [{"source_table": "articles", "identity": {"id": str(uuid.uuid4())}}]
    hydrated = changes * 2001
    current = AsyncMock(return_value=changes)
    hydration = AsyncMock(return_value=hydrated)
    overview = SimpleNamespace(publish=AsyncMock())
    publish_cards = AsyncMock()
    monkeypatch.setattr(producer, "read_current_changes", current)
    monkeypatch.setattr(producer, "hydrate_changes", hydration)
    monkeypatch.setattr(producer, "DirectOverviewAnalytics", lambda _: overview)
    monkeypatch.setattr(cards, "publish_cards", publish_cards)

    class Connection:
        @asynccontextmanager
        async def transaction(self):
            yield

        async def execute(self, query, parameters=None):
            async def fetchone():
                return None if revision is None else {"revision": revision}

            return SimpleNamespace(fetchone=fetchone)

    async def run():
        connection, client, settings = Connection(), object(), object()
        publisher = producer.DirectAnalyticsPublisher(client, settings)
        if revision is None:
            with pytest.raises(RuntimeError, match="source ordering"):
                await publisher._publish_current(connection, changes)
            current.assert_not_awaited()
            overview.publish.assert_not_awaited()
        else:
            assert await publisher._publish_current(connection, changes) == changes
            current.assert_awaited_once_with(connection, changes)
            hydration.assert_awaited_once_with(connection, changes)
            assert [len(call.args[0]) for call in overview.publish.await_args_list] == [
                1000,
                1000,
                1,
            ]
            assert all(call.kwargs == {"revision": 42} for call in overview.publish.await_args_list)
            publish_cards.assert_awaited_once_with(client, settings, changes, 42)

    asyncio.run(run())


def test_publisher_streams_dependent_catalog_pages_without_buffering_the_whole_catalog(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import devfeed_core.analytics_direct_cards as cards
    import psycopg

    changes = [{"source_table": "topics", "identity": {"id": str(uuid.uuid4())}}]
    pages = [
        [{"source_table": "articles", "identity": {"id": str(uuid.uuid4())}}] for _ in range(2)
    ]
    phases = []

    class Connection:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            phases.append("closed")

    connection = Connection()
    connect = AsyncMock(return_value=connection)
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", connect)

    async def dependent(seen_connection, current):
        assert seen_connection is connection and current == changes
        for index, page in enumerate(pages):
            phases.append(f"read-{index}")
            yield page
            assert phases[-1] == f"published-{index}"

    monkeypatch.setattr(cards, "dependent_card_batches", dependent)
    settings = SimpleNamespace(
        database_url="postgresql+psycopg://fixture@local/fixture_test",
        database_connect_timeout_seconds=3,
    )
    publisher = producer.DirectAnalyticsPublisher(object(), settings)

    async def publish(current_connection, batch):
        assert current_connection is connection
        phases.append("initial" if batch == changes else f"published-{pages.index(batch)}")
        return changes

    publisher._publish_current = publish
    asyncio.run(publisher.publish(changes))
    assert phases == ["initial", "read-0", "published-0", "read-1", "published-1", "closed"]
    assert connect.call_args.args == ("postgresql://fixture@local/fixture_test",)


def test_writer_preserves_intermediate_association_owners_across_committed_transactions(
    writer_runtime, monkeypatch
):
    state, started, release, batches, _ = writer_runtime
    monkeypatch.setattr(producer, "MAX_PENDING_TRANSACTIONS", 4)
    state.block = True
    identity, first, second = [str(uuid.uuid4()) for _ in range(3)]
    moves = [
        dict(
            source_table="article_origins",
            identity={"id": identity},
            before_record={"id": identity, "article_id": owner},
        )
        for owner in (first, second)
    ]
    writer = producer.AnalyticsProducer(object())
    try:
        assert writer.submit(article_change(1))
        assert started.wait(1)
        for move in moves:
            assert writer.submit([move])
        assert not writer.shutdown(0)
        release.set()
        assert writer.shutdown(2)
        captured = [
            row for batch in batches for row in batch if row["source_table"] == "article_origins"
        ]
        assert captured == moves
    finally:
        release.set()
        writer.shutdown(2)


def test_application_capture_tracks_old_and_new_keys_without_emitting_before_commit(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from sqlalchemy.dialects import postgresql

    old, new, topic = [uuid.uuid4() for _ in range(3)]
    table = Table(
        "article_topics",
        MetaData(),
        Column("article_id", Uuid, primary_key=True),
        Column("topic_id", Uuid, primary_key=True),
    )
    operation = (
        update(table)
        .where(table.c.article_id == old, table.c.topic_id == topic)
        .values(article_id=new)
    )
    compiled = operation.compile(dialect=postgresql.dialect())
    context = SimpleNamespace(
        compiled=compiled, compiled_parameters=[compiled.params], execution_options={}
    )
    session = Mock(info={})
    session.in_nested_transaction.return_value = False
    connection = SimpleNamespace(info={}, dialect=SimpleNamespace(name="postgresql"))
    capture = producer.ApplicationAnalyticsCapture
    capture.attach(session, object(), connection)
    monkeypatch.setattr(
        producer, "_records", lambda *_args: [{"article_id": old, "topic_id": topic}]
    )
    emitted = []
    monkeypatch.setattr(producer, "submit", emitted.append)
    capture.before(connection, object(), "UPDATE", (), context, False)
    capture.after(connection, SimpleNamespace(rowcount=1), "UPDATE", (), context, False)
    assert not emitted
    capture.committed(session)
    assert len(emitted) == 1
    assert {
        (str(row["identity"]["article_id"]), str(row["identity"]["topic_id"])) for row in emitted[0]
    } == {(str(old), str(topic)), (str(new), str(topic))}
    capture.detach(None, SimpleNamespace(info=connection.info))
    assert producer._SESSION not in connection.info


@pytest.mark.parametrize(
    "skipped", ["foreign_driver", "internal_query", "no_session", "untracked", "zero_rows"]
)
def test_application_capture_ignores_nonapplication_and_noop_statements(monkeypatch, skipped):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from sqlalchemy.dialects import postgresql

    table = Table(
        "untracked" if skipped == "untracked" else "articles",
        MetaData(),
        Column("id", Uuid, primary_key=True),
    )
    statement = delete(table).where(table.c.id == uuid.uuid4())
    compiled = statement.compile(dialect=postgresql.dialect())
    context = SimpleNamespace(
        compiled=compiled,
        compiled_parameters=[compiled.params],
        execution_options={producer._SKIP: skipped == "internal_query"},
    )
    session = Mock(info={})
    connection = SimpleNamespace(
        info={},
        dialect=SimpleNamespace(name="sqlite" if skipped == "foreign_driver" else "postgresql"),
    )
    capture = producer.ApplicationAnalyticsCapture
    if skipped != "no_session":
        capture.attach(session, object(), connection)
    read = Mock(return_value=[{"id": uuid.uuid4()}])
    monkeypatch.setattr(producer, "_records", read)
    monkeypatch.setattr(producer, "_dependent_articles", lambda *_: [])
    monkeypatch.setattr(producer, "_cascaded_records", lambda *_: [])
    capture.before(connection, None, "DELETE", (), context, False)
    capture.after(connection, SimpleNamespace(rowcount=0), "DELETE", (), context, False)
    assert session.info == {}
    assert read.call_count == int(skipped == "zero_rows")


def test_capture_savepoints_release_only_committed_outer_pending_work(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    capture = producer.ApplicationAnalyticsCapture
    session = Mock(info={})
    session.in_nested_transaction.return_value = True
    outer = SimpleNamespace(parent=None, nested=False)
    nested = Mock(parent=outer, nested=True)
    producer._remember(session, "articles", {"id": str(uuid.UUID(int=1))})
    capture.savepoint(session, nested)
    producer._remember(session, "articles", {"id": str(uuid.UUID(int=2))})
    emitted = []
    monkeypatch.setattr(producer, "submit", emitted.append)
    capture.committed(session)
    assert not emitted
    capture.rollback(session, nested)
    session.in_nested_transaction.return_value = False
    capture.committed(session)
    assert len(emitted) == 1 and len(emitted[0]) == 1
    assert emitted[0][0]["identity"] == {"id": str(uuid.UUID(int=1))}
    producer._remember(session, "articles", {"id": str(uuid.UUID(int=3))})
    capture.rollback(session, outer)
    capture.committed(session)
    assert len(emitted) == 1 and session.info == {}


@pytest.mark.parametrize(
    "current,previous,duplicate",
    [
        (None, {"attempts": 1}, False),
        ({"status": "queued", "attempts": 1}, {"attempts": 1}, False),
        ({"status": "succeeded", "attempts": 2}, {"attempts": 1}, False),
        ({"status": "failed", "attempts": 2}, {"attempts": 2}, True),
        (
            {"status": "succeeded", "finished_at": "2026-10-27T01:00:00Z"},
            {"finished_at": "2026-10-27T01:00:00Z"},
            True,
        ),
        (
            {"status": "succeeded", "finished_at": "2026-10-27T01:00:00Z"},
            {"finished_at": "2026-10-27T00:00:00Z"},
            False,
        ),
    ],
)
def test_same_completed_attempt_prefers_current_completion_over_a_stale_saved_image(
    current, previous, duplicate
):
    assert producer._same_completed_attempt(current, previous) is duplicate


def test_current_completed_job_suppresses_stale_attempt_snapshot_in_publication():
    import asyncio
    from types import SimpleNamespace

    identity = str(uuid.uuid4())
    current = {"id": identity, "status": "succeeded", "attempts": 1, "duration_ms": 200}
    previous = current | {"duration_ms": 0}

    class Connection:
        async def execute(self, *_args):
            async def fetchall():
                return [{"record": current}]

            return SimpleNamespace(fetchall=fetchall)

    changes = [
        {
            "source_table": "article_analysis_jobs",
            "identity": {"id": identity},
            "attempts": [previous],
        }
    ]
    result = asyncio.run(producer.read_current_changes(Connection(), changes))
    assert len(result) == 1 and result[0]["record"] == current


@pytest.mark.parametrize("style", ["orm", "core"])
@pytest.mark.integration
def test_automation_clock_only_updates_do_not_publish_analytics_but_mixed_edits_do(
    database, monkeypatch, style
):
    from datetime import timedelta

    from devfeed_core.models import Article

    emitted = []
    monkeypatch.setattr(producer, "submit", emitted.append)
    with database.begin() as session:
        row = Article(
            canonical_url="https://producer.test/clock",
            url_hash="producer-clock",
            title="Original title",
        )
        session.add(row)
        session.flush()
        identity = row.id
    emitted.clear()
    when = datetime.now(UTC)
    with database.begin() as session:
        if style == "orm":
            row = session.get(Article, identity)
            row.automation_next_check_at, row.automation_started_at = when, when
        else:
            session.execute(
                update(Article)
                .where(Article.id == identity)
                .values(automation_next_check_at=when, automation_started_at=when)
            )
    assert emitted == []
    with database.begin() as session:
        if style == "orm":
            row = session.get(Article, identity)
            row.automation_next_check_at = when + timedelta(minutes=5)
            row.title = "Updated title"
        else:
            session.execute(
                update(Article)
                .where(Article.id == identity)
                .values(automation_next_check_at=when + timedelta(minutes=5), title="Updated title")
            )
    assert any(
        change["source_table"] == "articles" and str(change["identity"]["id"]) == str(identity)
        for batch in emitted
        for change in batch
    )


@pytest.mark.parametrize(
    "values,expected",
    [
        ({"automation_next_check_at": datetime.now(UTC)}, True),
        ({"automation_started_at": datetime.now(UTC)}, True),
        ({"automation_started_at": None, "title": "Updated title"}, False),
        ({"publication_status": "published"}, False),
    ],
)
def test_clock_update_classifier_preserves_business_mutations(values, expected):
    from devfeed_core.models import Article

    assert producer._clock_only_update(update(Article).values(**values), []) is expected


def test_orm_clock_update_classifier_ignores_predicate_binds_and_requires_assignments():
    from devfeed_core.models import Article, Topic

    operation = update(Article)
    assert producer._clock_only_update(
        operation, [{"articles_id": uuid.uuid4(), "automation_started_at": None}]
    )
    assert not producer._clock_only_update(operation, [{"articles_id": uuid.uuid4()}])
    assert not producer._clock_only_update(insert(Article), [])
    assert not producer._clock_only_update(update(Topic).values(name="Changed"), [])
