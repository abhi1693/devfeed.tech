"""Behavior and concurrency regressions against disposable PostgreSQL."""

from datetime import timedelta

import pytest
from devfeed_core.job_retention import prune_job_payloads
from devfeed_core.models import ArticleAnalysisJob, utcnow
from devfeed_core.topics import lock_topics
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from test_automation_integration import seed

pytestmark = pytest.mark.integration


def test_classification_locks_coexist_and_exclude_catalog_writes(database):
    with database.begin() as first:
        lock_topics(first, read=True)
        with database.begin() as second:
            second.execute(text("SET LOCAL lock_timeout='150ms'"))
            lock_topics(second, read=True)
        with pytest.raises(OperationalError) as failure, database.begin() as writer:
            writer.execute(text("SET LOCAL lock_timeout='150ms'"))
            lock_topics(writer)
        assert failure.value.orig.sqlstate == "55P03"
    with database.begin() as writer:
        lock_topics(writer)
        with pytest.raises(OperationalError) as failure, database.begin() as reader:
            reader.execute(text("SET LOCAL lock_timeout='150ms'"))
            lock_topics(reader, read=True)
        assert failure.value.orig.sqlstate == "55P03"


def test_retention_preserves_latest_failures_provenance_and_pending_articles(database):
    old = utcnow() - timedelta(days=40)
    with database.begin() as session:
        _, article, _ = seed(session)
        article.review_status = "approved"
        jobs = []
        for i, status in enumerate(["succeeded", "failed", "succeeded", "succeeded"]):
            job = ArticleAnalysisJob(
                article_id=article.id,
                status=status,
                created_at=old + timedelta(seconds=i),
                finished_at=old,
                input_snapshot={"text": "source evidence"},
                catalog_snapshot={"topics": ["evidence"]},
                result={"outcome": "ready"},
                input_hash="a" * 64,
            )
            session.add(job)
            jobs.append(job)
        session.flush()
        article.classification_provenance = {"analysis_id": str(jobs[2].id)}
        ids = [j.id for j in jobs]
    assert prune_job_payloads(database) == 1
    assert prune_job_payloads(database) == 0
    with database() as session:
        jobs = [session.get(ArticleAnalysisJob, i) for i in ids]
        assert jobs[0].input_snapshot == jobs[0].catalog_snapshot == {}
        assert jobs[0].inputs_pruned_at is not None
        assert jobs[0].input_hash == "a" * 64
        assert jobs[0].result == {"outcome": "ready"}
        assert all(j.input_snapshot and j.inputs_pruned_at is None for j in jobs[1:])


def test_topic_discovery_uses_early_exit_on_production_sized_links(database):
    import json
    import uuid

    from devfeed_core.models import Article, ArticleOrigin, ArticleTopic, Source, Topic
    from sqlalchemy import insert

    topic_ids = [uuid.uuid4() for _ in range(2000)]
    article_ids = [uuid.uuid4() for _ in range(25000)]
    source_id = uuid.uuid4()
    with database.begin() as session:
        session.execute(
            insert(Source.__table__),
            [
                {
                    "id": source_id,
                    "name": "Profile",
                    "feed_url": "https://example.test/rss",
                    "source_type": "publisher",
                    "approval_status": "approved",
                }
            ],
        )
        session.execute(
            insert(Topic.__table__),
            [
                {
                    "id": id_,
                    "name": f"Topic {i:05}",
                    "slug": f"topic-{i}",
                    "kind": "technology",
                    "status": "active",
                }
                for i, id_ in enumerate(topic_ids)
            ],
        )
        for start in range(0, len(article_ids), 1000):
            chunk = article_ids[start : start + 1000]
            session.execute(
                insert(Article.__table__),
                [
                    {
                        "id": id_,
                        "canonical_url": f"https://example.test/{id_}",
                        "url_hash": id_.hex,
                        "title": "Profile",
                        "publication_status": "published",
                        "review_status": "approved",
                    }
                    for id_ in chunk
                ],
            )
            session.execute(
                insert(ArticleOrigin.__table__),
                [
                    {
                        "article_id": id_,
                        "source_id": source_id,
                        "entry_key": str(id_),
                        "original_url": f"https://example.test/{id_}",
                    }
                    for id_ in chunk
                ],
            )
            session.execute(
                insert(ArticleTopic.__table__),
                [
                    {
                        "article_id": id_,
                        "topic_id": topic_ids[(start + i + offset) % len(topic_ids)],
                        "role": "primary" if offset == 0 else "supporting",
                        "relevance": 1,
                        "evidence": "Profile",
                    }
                    for i, id_ in enumerate(chunk)
                    for offset in range(3)
                ],
            )
        for table in ("topics", "articles", "article_origins", "article_topics", "sources"):
            session.execute(text(f"ANALYZE {table}"))
    inner = """SELECT 1 FROM article_topics at JOIN articles a ON a.id=at.article_id
        WHERE at.topic_id=t.id AND at.role IN ('primary','supporting')
        AND a.publication_status='published' AND a.review_status='approved'
        AND EXISTS(SELECT 1 FROM article_origins o JOIN sources s ON s.id=o.source_id
                   WHERE o.article_id=a.id AND s.approval_status='approved')"""
    prefix = "SELECT t.* FROM topics t WHERE t.status='active' AND "
    suffix = " ORDER BY t.name,t.id LIMIT 100"
    old = prefix + f"EXISTS({inner})" + suffix
    from types import SimpleNamespace

    from devfeed_api.topics import topics
    from sqlalchemy.dialects import postgresql

    captured = []
    topics(
        SimpleNamespace(
            scalars=lambda statement: captured.append(statement) or SimpleNamespace(all=lambda: [])
        ),
        limit=100,
        offset=0,
        has_articles=True,
        sort="name",
    )
    new = str(
        captured[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    reports = {}
    with database() as session:
        assert (
            session.execute(text(old)).mappings().all()
            == session.execute(text(new)).mappings().all()
        )
        for label, query in [("before", old), ("after", new)]:
            if label == "before":
                session.execute(text("DROP INDEX ix_topics_active_name"))
            plan = session.scalar(text("EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) " + query))[0]
            if label == "before":
                session.rollback()  # Restore the index before measuring the new plan.
            reports[label] = {
                "ms": plan["Execution Time"],
                "buffers": plan["Plan"]["Shared Hit Blocks"] + plan["Plan"]["Shared Read Blocks"],
            }
    print("TOPIC_PROFILE=" + json.dumps(reports))

    def nodes(node):
        yield node
        for child in node.get("Plans", []):
            yield from nodes(child)

    assert any(n.get("Index Name") == "ix_topics_active_name" for n in nodes(plan["Plan"]))
    assert not any(
        n.get("Relation Name") == "article_topics" and n["Node Type"] == "Seq Scan"
        for n in nodes(plan["Plan"])
    )
    assert reports["after"]["buffers"] < reports["before"]["buffers"]


def test_retention_protects_pending_articles_and_topic_verification(database):
    import uuid

    from devfeed_core.models import ResearchVerificationJob, TopicAnalysisJob, TopicProposal

    old = utcnow() - timedelta(days=40)
    with database.begin() as session:
        _, article, _ = seed(session)
        for offset in range(2):
            session.add(
                ArticleAnalysisJob(
                    article_id=article.id,
                    status="succeeded",
                    input_snapshot={"evidence": True},
                    created_at=old + timedelta(seconds=offset),
                    finished_at=old,
                )
            )
        protected = []
        removable = None
        for label in ("closed", "pending", "verification"):
            proposal = TopicProposal(
                batch_id=uuid.uuid4(),
                slug=label,
                action="create",
                origin="import",
                source_name="Test",
                proposed={},
                created_by={},
                status="pending" if label == "pending" else "rejected",
                reviewed_at=None if label == "pending" else old,
                reviewed_by=None if label == "pending" else {},
            )
            session.add(proposal)
            session.flush()
            jobs = []
            for offset in range(2):
                job = TopicAnalysisJob(
                    proposal_id=proposal.id,
                    input_hash="a" * 64,
                    input_snapshot={"evidence": True},
                    requested_by={},
                    prompt_version="test",
                    status="succeeded",
                    created_at=old + timedelta(seconds=offset),
                    finished_at=old,
                )
                session.add(job)
                jobs.append(job)
            session.flush()
            if label == "closed":
                removable = jobs[0].id
            else:
                protected.append(jobs[0].id)
            protected.append(jobs[1].id)
            if label == "verification":
                session.add(
                    ResearchVerificationJob(id=jobs[0].id, status="queued", relationships=False)
                )
    assert prune_job_payloads(database) == 1
    with database() as session:
        assert session.get(TopicAnalysisJob, removable).input_snapshot == {}
        assert all(session.get(TopicAnalysisJob, id_).input_snapshot for id_ in protected)
        assert all(session.scalars(select(ArticleAnalysisJob.input_snapshot)).all())


def test_retention_skips_locked_history_and_bounds_batches(database, monkeypatch):
    from devfeed_core.config import get_settings

    monkeypatch.setenv("DEVFEED_JOB_PAYLOAD_PRUNE_BATCH_SIZE", "1")
    get_settings.cache_clear()
    old = utcnow() - timedelta(days=40)
    with database.begin() as session:
        _, article, _ = seed(session)
        article.review_status = "approved"
        jobs = [
            ArticleAnalysisJob(
                article_id=article.id,
                status="succeeded",
                input_snapshot={"evidence": True},
                created_at=old + timedelta(seconds=i),
                finished_at=old + timedelta(seconds=i),
            )
            for i in range(3)
        ]
        session.add_all(jobs)
        session.flush()
        oldest, second = jobs[0].id, jobs[1].id
    with database.begin() as locker:
        locker.scalar(
            select(ArticleAnalysisJob).where(ArticleAnalysisJob.id == oldest).with_for_update()
        )
        assert prune_job_payloads(database) == 1
        with database() as session:
            assert session.get(ArticleAnalysisJob, oldest).input_snapshot
            assert session.get(ArticleAnalysisJob, second).input_snapshot == {}
    assert prune_job_payloads(database) == 1
    assert prune_job_payloads(database) == 0


def test_source_proposals_serialize_without_blocking_classification_readers(database, monkeypatch):
    from devfeed_core.article_automation import lock_article_catalog, propose_source_topics
    from devfeed_core.config import get_settings
    from devfeed_core.models import Article, ArticleTag, Tag, TopicProposal

    monkeypatch.setenv("DEVFEED_FULL_AUTOMATION", "true")
    get_settings.cache_clear()
    with database.begin() as session:
        _, article, _ = seed(session, topic=False)
        tag = Tag(name="Fresh Technology", slug="fresh-technology")
        session.add(tag)
        session.flush()
        session.add(ArticleTag(article_id=article.id, tag_id=tag.id, origin="source"))
        identifier = article.id
    with database.begin() as first:
        lock_article_catalog(first)
        assert propose_source_topics(first, first.get(Article, identifier)) == 1
        with database.begin() as reader:
            reader.execute(text("SET LOCAL lock_timeout='150ms'"))
            lock_topics(reader, read=True)
        with pytest.raises(OperationalError) as failure, database.begin() as second:
            second.execute(text("SET LOCAL lock_timeout='150ms'"))
            lock_article_catalog(second)
        assert failure.value.orig.sqlstate == "55P03"
    with database.begin() as second:
        lock_article_catalog(second)
        assert propose_source_topics(second, second.get(Article, identifier)) == 0
        assert len(second.scalars(select(TopicProposal)).all()) == 1
