"""Late maintenance pages must bound index traversal as well as returned rows."""

import json
import uuid
from datetime import datetime, timedelta

import pytest
from devfeed_core import job_retention
from devfeed_core.models import (
    Article,
    ArticleRefresh,
    JobPayloadRetention,
    Topic,
    TopicAnalysisJob,
    utcnow,
)
from sqlalchemy import event, insert, literal, select, text

from tests.support.source_readiness import configuration

pytestmark = pytest.mark.integration


def test_late_retention_page_bounds_the_leading_finished_at_index_range(database, monkeypatch):
    now = utcnow() + timedelta(seconds=2)
    old = now - timedelta(days=60)
    count, cursor = 20000, 16000
    identifiers = [
        uuid.uuid5(uuid.NAMESPACE_URL, f"retention-page-{index}") for index in range(count)
    ]
    monkeypatch.setattr(job_retention, "utcnow", lambda: now)
    monkeypatch.setattr(job_retention, "get_settings", configuration)
    with database.begin() as session:
        topic = Topic(name="Long retained history", slug="long-retained-history", kind="technology")
        session.add(topic)
        session.flush()
        session.execute(
            insert(TopicAnalysisJob.__table__),
            [
                {
                    "id": identifiers[index],
                    "topic_id": topic.id,
                    "status": "succeeded",
                    "input_hash": "a" * 64,
                    "input_snapshot": {"evidence": index},
                    "requested_by": {},
                    "prompt_version": "history-page-v1",
                    "created_at": old + timedelta(seconds=index),
                    "finished_at": old + timedelta(seconds=index),
                }
                for index in range(count)
            ],
        )
        state = session.get(JobPayloadRetention, "topic")
        state.next_run_at = now
        state.cutoff = now - timedelta(days=30)
        state.cursor_finished_at = old + timedelta(seconds=cursor)
        state.cursor_id = identifiers[cursor]

    engine = database.kw["bind"]
    with engine.begin() as connection:
        connection.exec_driver_sql("ANALYZE topic_analysis_jobs")

    baseline = (
        select(TopicAnalysisJob.id, TopicAnalysisJob.finished_at, TopicAnalysisJob.proposal_id)
        .where(
            TopicAnalysisJob.id.in_(identifiers[cursor + 1 : cursor + 1001]),
            TopicAnalysisJob.finished_at < now - timedelta(days=30),
            *job_retention._eligible_conditions(TopicAnalysisJob),
        )
        .order_by(TopicAnalysisJob.finished_at, TopicAnalysisJob.id)
        .limit(100)
    )
    compiled = baseline.compile(engine, compile_kwargs={"render_postcompile": True})

    measured = []
    explaining = False

    def capture(connection, _cursor, statement, parameters, _context, _many):
        nonlocal explaining
        if (
            explaining
            or measured
            or "FROM topic_analysis_jobs" not in statement
            or "newer" not in statement
            or "LIMIT" not in statement
        ):
            return
        explaining = True
        try:
            # Exercise the ordered retention-index path specifically. Without
            # leading tuple bounds it can traverse the entire earlier index
            # prefix while returning only the IDs from this page; returned-row
            # counts alone cannot detect that work. Other planner choices still
            # receive the same bounded SQL interval.
            connection.exec_driver_sql("SET LOCAL enable_bitmapscan = off")
            connection.exec_driver_sql("SET LOCAL enable_sort = off")
            before = connection.exec_driver_sql(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + str(compiled), compiled.params
            ).scalar()[0]
            measured.append(
                (
                    statement,
                    connection.exec_driver_sql(
                        "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement, parameters
                    ).scalar()[0],
                    before,
                )
            )
        finally:
            connection.exec_driver_sql("SET LOCAL enable_bitmapscan = on")
            connection.exec_driver_sql("SET LOCAL enable_sort = on")
            explaining = False

    event.listen(engine, "before_cursor_execute", capture)
    try:
        assert job_retention.prune_job_payloads(database) == 100
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    statement, after, before = measured[0]
    assert "(topic_analysis_jobs.finished_at, topic_analysis_jobs.id) >=" in statement
    assert "(topic_analysis_jobs.finished_at, topic_analysis_jobs.id) <=" in statement

    def retention_scans(node):
        matching = [node] if node.get("Index Name") == "ix_topic_analysis_jobs_retention" else []
        return matching + [
            scan for child in node.get("Plans", []) for scan in retention_scans(child)
        ]

    scans = retention_scans(after["Plan"])
    assert len(scans) == 1, after
    bounded_scan = scans[0]
    assert "ROW(finished_at, id) >= ROW(" in bounded_scan["Index Cond"]
    assert "ROW(finished_at, id) <= ROW(" in bounded_scan["Index Cond"]
    # Bound the work of this index scan directly. Whole-query buffer hits also
    # include eligibility subplans and vary with heap/index layout; they are not
    # a stable measure of how far the retention scan traverses the history.
    traversed = (
        bounded_scan["Actual Rows"]
        + bounded_scan.get("Rows Removed by Filter", 0)
        + bounded_scan.get("Rows Removed by Index Recheck", 0)
    ) * bounded_scan["Actual Loops"]
    assert traversed <= configuration().job_payload_scan_batch_size, bounded_scan
    assert before["Plan"]["Actual Rows"] == after["Plan"]["Actual Rows"] == 100
    before_buffers = before["Plan"]["Shared Hit Blocks"] + before["Plan"]["Shared Read Blocks"]
    after_buffers = after["Plan"]["Shared Hit Blocks"] + after["Plan"]["Shared Read Blocks"]
    assert after_buffers < before_buffers
    print(
        {
            "unbounded_ms": before["Execution Time"],
            "bounded_ms": after["Execution Time"],
            "unbounded_buffers": before_buffers,
            "bounded_buffers": after_buffers,
            "bounded_index_rows": traversed,
        }
    )
    with database() as session:
        assert session.get(TopicAnalysisJob, identifiers[cursor + 1]).inputs_pruned_at == now
        assert session.get(TopicAnalysisJob, identifiers[cursor]).inputs_pruned_at is None
        assert session.get(TopicAnalysisJob, identifiers[cursor + 1001]).inputs_pruned_at is None


def test_owner_retention_uses_generic_sparse_index_past_large_pruned_prefix(database, monkeypatch):
    now = utcnow() + timedelta(seconds=2)
    old = now - timedelta(days=120)
    prefix_stamp = now - timedelta(days=1)
    monkeypatch.setattr(job_retention, "utcnow", lambda: now)
    monkeypatch.setattr(job_retention, "get_settings", configuration)
    with database.begin() as session:
        articles = [
            Article(
                title=f"Retention owner {i}",
                canonical_url=f"https://retention.invalid/{uuid.uuid4()}",
                url_hash=uuid.uuid4().hex,
                review_status="approved",
            )
            for i in range(2)
        ]
        session.add_all(articles)
        session.flush()
        parent, other = [article.id for article in articles]
        session.execute(
            text(
                "INSERT INTO article_refreshes(id,article_id,target,status,base_revision,base_hash,"
                "payload,requested_by,created_at,completed_at,payload_pruned_at) "
                "SELECT md5('already-pruned:'||g::text)::uuid,:parent,"
                "'analysis','cancelled',0,:hash,"
                "'{}','{}',:old,:old+g*interval '1 second',:stamp FROM generate_series(1,100000) g"
            ),
            {"parent": parent, "hash": "a" * 64, "old": old, "stamp": prefix_stamp},
        )
        session.execute(
            text(
                "INSERT INTO article_refreshes(id,article_id,target,status,base_revision,base_hash,"
                "payload,requested_by,created_at,completed_at) "
                "SELECT md5('pending-retention:'||g::text)::uuid,:parent,"
                "'analysis','cancelled',0,:hash,"
                "jsonb_build_object('private_body',repeat('source evidence ',64)),'{}',:old,"
                ":finished+g*interval '1 second' FROM generate_series(1,120) g"
            ),
            {"parent": parent, "hash": "b" * 64, "old": old, "finished": now - timedelta(days=40)},
        )
        for article_id, status in [(parent, "active"), (other, "review")]:
            session.add(
                ArticleRefresh(
                    article_id=article_id,
                    target="analysis",
                    status=status,
                    base_revision=0,
                    base_hash="c" * 64,
                    payload={"private_body": "active evidence"},
                    requested_by={},
                    created_at=old,
                    completed_at=old,
                )
            )
    engine = database.kw["bind"]
    with engine.begin() as connection:
        connection.exec_driver_sql("ANALYZE article_refreshes")
    measured = []
    explaining = False

    def capture(connection, _cursor, statement, parameters, _context, _many):
        nonlocal explaining
        if (
            explaining
            or measured
            or "FROM article_refreshes" not in statement
            or "FOR UPDATE" not in statement
        ):
            return
        explaining = True
        try:
            keys = list(parameters)
            prepared = statement
            types = []
            for position, key in enumerate(keys, 1):
                prepared = prepared.replace(f"%({key})s", f"${position}")
                types.append("timestamptz" if isinstance(parameters[key], datetime) else "integer")
            connection.exec_driver_sql("SET LOCAL plan_cache_mode=force_generic_plan")
            connection.exec_driver_sql(
                f"PREPARE refresh_owner_probe({','.join(types)}) AS {prepared}"
            )
            # EXECUTE arguments cannot use protocol bind placeholders. Render
            # only these typed local datetime/integer fixture values as literals;
            # the prepared statement itself still gets a true generic plan.
            arguments = ",".join(
                str(
                    literal(parameters[key]).compile(
                        dialect=connection.dialect, compile_kwargs={"literal_binds": True}
                    )
                )
                for key in keys
            )
            plan = connection.exec_driver_sql(
                f"EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) EXECUTE refresh_owner_probe({arguments})"
            ).scalar()[0]
            counts = connection.exec_driver_sql(
                "SELECT generic_plans,custom_plans FROM pg_prepared_statements "
                "WHERE name='refresh_owner_probe'"
            ).one()
            measured.append((plan, tuple(counts)))
            connection.exec_driver_sql("DEALLOCATE refresh_owner_probe")
        finally:
            connection.exec_driver_sql("SET LOCAL plan_cache_mode=auto")
            explaining = False

    event.listen(engine, "before_cursor_execute", capture)
    try:
        assert job_retention.prune_job_payloads(database) == 0
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(measured) == 1 and measured[0][1] == (1, 0)

    def nodes(plan):
        yield plan
        for child in plan.get("Plans", []):
            yield from nodes(child)

    plan = measured[0][0]
    scans = [
        node for node in nodes(plan["Plan"]) if node.get("Relation Name") == "article_refreshes"
    ]
    assert any(node.get("Index Name") == "ix_article_refresh_retention" for node in scans)
    assert not any(node["Node Type"] == "Seq Scan" for node in scans)
    assert all(node.get("Rows Removed by Filter", 0) == 0 for node in scans)
    with database() as session:
        assert (
            session.scalar(
                text("SELECT count(*) FROM article_refreshes WHERE payload_pruned_at=:stamp"),
                {"stamp": prefix_stamp},
            )
            == 100000
        )
        assert (
            session.scalar(
                text("SELECT count(*) FROM article_refreshes WHERE payload_pruned_at=:stamp"),
                {"stamp": now},
            )
            == 100
        )
        assert session.get(JobPayloadRetention, "article").next_run_at == now + timedelta(
            seconds=30
        )
    now += timedelta(seconds=31)
    assert job_retention.prune_job_payloads(database) == 0
    with database() as session:
        assert (
            session.scalar(
                text(
                    "SELECT count(*) FROM article_refreshes WHERE payload_pruned_at IS NULL "
                    "AND status IN ('promoted','superseded','cancelled')"
                )
            )
            == 0
        )
        assert session.scalar(
            select(ArticleRefresh.payload).where(ArticleRefresh.status == "active")
        )["private_body"]
        assert session.scalar(
            select(ArticleRefresh.payload).where(ArticleRefresh.status == "review")
        )["private_body"]
    print(
        json.dumps(
            {
                "pruned_prefix": 100000,
                "owner_pages": [100, 20],
                "generic_plans": 1,
                "custom_plans": 0,
                "owner_scan_nodes": [
                    {
                        "type": node["Node Type"],
                        "index": node.get("Index Name"),
                        "rows": node.get("Actual Rows"),
                        "removed": node.get("Rows Removed by Filter", 0),
                    }
                    for node in scans
                ],
            }
        )
    )
