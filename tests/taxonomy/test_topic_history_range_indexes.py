"""Proving an old run is still latest must not rescan its complete subject history."""

from datetime import timedelta

import pytest
from devfeed_core.job_retention import prune_job_payloads
from devfeed_core.models import Topic, TopicAnalysisJob, utcnow
from sqlalchemy import event, insert

pytestmark = pytest.mark.integration


def test_latest_old_payload_stays_protected_with_bounded_newer_probe(database):
    now = utcnow()
    with database.begin() as session:
        topic = Topic(name="History", slug="history", kind="technology", status="active")
        session.add(topic)
        session.flush()
        session.execute(
            insert(TopicAnalysisJob),
            [
                dict(
                    topic_id=topic.id,
                    input_hash="a" * 64,
                    requested_by={},
                    prompt_version="test",
                    status="succeeded",
                    created_at=now - timedelta(days=100) + timedelta(seconds=index),
                    finished_at=now - timedelta(days=99),
                    inputs_pruned_at=now if index < 1999 else None,
                    input_snapshot={"record": index},
                )
                for index in range(2000)
            ],
        )
    statements = []
    with database() as session:
        engine = session.get_bind()

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "FROM topic_analysis_jobs" in statement and "FOR UPDATE SKIP LOCKED" in statement:
            statements.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", capture)
    try:
        assert prune_job_payloads(database) == 0
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    def visited(node):
        own = (
            (node.get("Actual Rows", 0) + node.get("Rows Removed by Filter", 0))
            * node.get("Actual Loops", 1)
            if node.get("Relation Name") == "topic_analysis_jobs"
            else 0
        )
        return own + sum(visited(child) for child in node.get("Plans", []))

    statement, parameters = statements[-1]
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.exec_driver_sql("ANALYZE topic_analysis_jobs")
            after = connection.exec_driver_sql(
                "EXPLAIN(ANALYZE,BUFFERS,FORMAT JSON) " + statement, parameters
            ).scalar()[0]
            for target in ("topic_id", "proposal_id"):
                connection.exec_driver_sql(f"DROP INDEX ix_topic_analysis_{target}_newer")
            before = connection.exec_driver_sql(
                "EXPLAIN(ANALYZE,BUFFERS,FORMAT JSON) " + statement, parameters
            ).scalar()[0]
        finally:
            transaction.rollback()
    assert visited(after["Plan"]) < 20
    assert visited(before["Plan"]) >= 2000
    print(
        {
            "before_ms": before["Execution Time"],
            "after_ms": after["Execution Time"],
            "before_blocks": before["Plan"]["Shared Hit Blocks"],
            "after_blocks": after["Plan"]["Shared Hit Blocks"],
        }
    )
