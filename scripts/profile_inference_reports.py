"""Profile complete inference reports on an explicitly disposable local test database.

Requires DEVFEED_TEST_DATABASE_URL. --seed replaces ledger/proposal/decision fixture
rows, including dependents. --writes measures inserts/updates in rolled-back
transactions. Production databases and non-local hosts are refused.
"""

import argparse
import json
import os
import statistics
import time
import uuid
from datetime import UTC, datetime, timedelta

from devfeed_core.inference_metrics import inference_charts
from devfeed_core.models import TopicDecisionRun, TopicProposal
from sqlalchemy import create_engine, insert, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker


class ProfileSession:
    def __init__(self, session, legacy=False):
        self.session, self.legacy = session, legacy
        self.activity = None

    def scalar(self, statement):
        if self.legacy and "to_regclass" in str(statement):
            return False
        return self.session.scalar(statement)

    def execute(self, statement, params=None):
        if "WITH usage AS" in str(statement) or "WITH range_calls AS" in str(statement):
            self.activity = (statement, params)
        return self.session.execute(statement, params or {})


def seed(engine, now, rows, decisions):
    with engine.begin() as connection:
        connection.execute(
            text("TRUNCATE inference_calls,topic_decision_runs,topic_proposals CASCADE")
        )
        connection.execute(
            text("""INSERT INTO inference_calls
          (id,started_at,finished_at,operation,model,reasoning_effort,request_hash,
           status,tokens,web_searches,duration_ms)
          SELECT md5('inference-profile-'||n)::uuid,
          :now-(n%100)*interval '1 day'-(n%86400)*interval '1 second', :now,
          (ARRAY['article_analysis','topic_discovery','topic_verification','partner_qualification'])[1+n%4],
          (ARRAY['model-a','model-b','model-c',NULL])[1+n%4],
          (ARRAY['low','medium','high',NULL])[1+(n/4)%4], repeat('a',64),
          (ARRAY['returned','failed','running'])[1+n%3],
          jsonb_build_object('inputTokens',
          CASE WHEN n%17=0 THEN 'malformed' ELSE (1000+n%9000)::text END,
          'cachedInputTokens',(n%11000)::text,'outputTokens',(n%2500)::text,
          'reasoningOutputTokens',(n%3000)::text), n%4,1000+n%20000
          FROM generate_series(1,:rows) n"""),
            {"now": now, "rows": rows},
        )
        proposals, runs = [], []
        for index in range(decisions):
            identifier = uuid.uuid5(uuid.NAMESPACE_URL, f"inference-profile-{index}")
            proposals.append(
                dict(
                    id=identifier,
                    batch_id=uuid.UUID(int=1),
                    slug=f"profile-{index}",
                    action="create",
                    origin="import",
                    source_name="Profile",
                    proposed={},
                    created_by={},
                )
            )
            calls = [
                dict(
                    stage=["discovery", "draft", "verification"][n % 3],
                    started_at=(now - timedelta(days=(index + n) % 100)).isoformat(),
                    escalated=n % 4 == 0,
                )
                for n in range(8)
            ]
            runs.append(
                dict(
                    proposal_id=identifier,
                    input_hash="b" * 64,
                    status="deferred" if index % 5 == 0 else "active",
                    state={"calls": calls},
                    updated_at=now - timedelta(days=index % 100),
                )
            )
        if proposals:
            connection.execute(insert(TopicProposal.__table__), proposals)
            connection.execute(insert(TopicDecisionRun.__table__), runs)
        for table in (
            "inference_calls",
            "inference_daily_usage",
            "topic_decision_runs",
            "topic_proposals",
        ):
            connection.execute(text(f"ANALYZE {table}"))


def report_profiles(sessions, now, repeats):
    reports = []
    for days in (30, 90):
        results, samples, explains = {}, {}, {}
        for repeat in range(repeats):
            for legacy in [True, False] if repeat % 2 == 0 else [False, True]:
                with sessions() as session:
                    session.execute(text("SET TRANSACTION READ ONLY"))
                    session.execute(text("SET LOCAL statement_timeout='10s'"))
                    profile = ProfileSession(session, legacy)
                    started = time.perf_counter()
                    result = inference_charts(profile, now - timedelta(days=days), now)
                    samples.setdefault(legacy, []).append((time.perf_counter() - started) * 1000)
                    results[legacy] = result.model_dump(mode="json")
                    if len(results) == 2:
                        assert results[True] == results[False], "Report results differ"
                    if repeat == repeats - 1:
                        statement, params = profile.activity
                        plan = session.execute(
                            text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + str(statement)),
                            params,
                        ).scalar_one()[0]
                        root = plan["Plan"]
                        explains[legacy] = {
                            key: root.get(key, 0)
                            for key in (
                                "Shared Hit Blocks",
                                "Shared Read Blocks",
                                "Temp Read Blocks",
                                "Temp Written Blocks",
                            )
                        }
                        explains[legacy]["execution_ms"] = plan["Execution Time"]
        reports.append(
            {
                "days": days,
                "result_equality": True,
                "strategies": {
                    "ledger" if legacy else "daily": {
                        "samples_ms": [round(v, 2) for v in values],
                        "median_ms": round(statistics.median(values), 2),
                        "activity_plan": explains[legacy],
                    }
                    for legacy, values in samples.items()
                },
            }
        )
    return reports


def write_profiles(engine, now, count):
    output = {}
    for legacy in (True, False):
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                if legacy:
                    for event in ("insert", "update", "delete", "truncate"):
                        connection.execute(
                            text(
                                f"ALTER TABLE inference_calls DISABLE TRIGGER "
                                f"inference_daily_usage_{event}"
                            )
                        )
                started = time.perf_counter()
                for _ in range(count):
                    identifier = uuid.uuid4()
                    connection.execute(
                        text("""INSERT INTO inference_calls
                        (id,started_at,operation,model,request_hash,status,tokens,web_searches,duration_ms)
                        VALUES (:id,:now,'profile-write','model',
                        repeat('c',64),'running','{}',0,0)"""),
                        {"id": identifier, "now": now},
                    )
                    connection.execute(
                        text("""UPDATE inference_calls SET status='returned',
                        tokens=jsonb_build_object('inputTokens',100,'outputTokens',20),duration_ms=100
                        WHERE id=:id"""),
                        {"id": identifier},
                    )
                output["ledger" if legacy else "daily"] = round(
                    (time.perf_counter() - started) * 1000 / count, 3
                )
            finally:
                transaction.rollback()  # Also restores trigger state after the baseline sample.
    return {"insert_and_finish_ms_per_invocation": output, "invocations_per_strategy": count}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--rows", type=int, default=310000)
    parser.add_argument("--decisions", type=int, default=5000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--writes", type=int, default=0)
    args = parser.parse_args()
    url = make_url(os.environ["DEVFEED_TEST_DATABASE_URL"])
    if not (url.database or "").endswith("_test") or url.host not in {"127.0.0.1", "localhost"}:
        parser.error("Use an explicitly disposable local database ending in _test")
    if not (
        1 <= args.rows <= 1000000
        and 0 <= args.decisions <= 10000
        and 1 <= args.repeats <= 10
        and 0 <= args.writes <= 1000
    ):
        parser.error("Fixture or sample size is outside the supported profiling bounds")
    now = datetime(2026, 10, 18, 19, tzinfo=UTC)
    engine = create_engine(url)
    try:
        if args.seed:
            seed(engine, now, args.rows, args.decisions)
        output = {"reports": report_profiles(sessionmaker(engine), now, args.repeats)}
        if args.writes:
            output["writes"] = write_profiles(engine, now, args.writes)
        print(json.dumps(output, indent=2))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
