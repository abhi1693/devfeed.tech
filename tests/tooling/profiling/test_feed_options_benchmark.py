"""Protect benchmark isolation, paired comparisons, and measurement cleanup."""

import argparse
import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql

from scripts import feed_options_benchmark as benchmark

BASELINE = "a" * 40
CANDIDATE = "b" * 40
TARGET = "postgresql+psycopg://benchmark@127.0.0.1/feed_options_benchmark_test"


@pytest.mark.parametrize("value", ["--output=/tmp/payload", "HEAD", "abc1234", "a" * 39, "A" * 40])
def test_baseline_requires_immutable_sha_before_invoking_git(value):
    with pytest.raises(argparse.ArgumentTypeError, match="full lowercase"):
        benchmark.commit_sha(value)
    assert benchmark.commit_sha(BASELINE) == BASELINE


@pytest.fixture
def command(monkeypatch, tmp_path):
    root = tmp_path / "checkout"
    feed = root / "apps/api/src/feed.py"
    feed.parent.mkdir(parents=True)
    feed.write_text("def feed_options(): return 'candidate'\n")
    output = root / "reports/options"
    argv = ["benchmark", "--baseline-ref", BASELINE, "--output", str(output)]
    monkeypatch.setattr(benchmark, "ROOT", root)
    monkeypatch.setattr("sys.argv", argv)
    monkeypatch.setenv("DEVFEED_TEST_DATABASE_URL", TARGET)
    engine = Mock()
    monkeypatch.setattr(benchmark, "create_engine", engine)
    return SimpleNamespace(root=root, output=output, argv=argv, engine=engine)


@pytest.mark.parametrize(
    "arguments,target,machine,message",
    [
        (["--rows", "999"], TARGET, "aarch64", "1000..2000000"),
        (["--repeats", "0"], TARGET, "aarch64", "1..20 repeats"),
        ([], TARGET.replace("127.0.0.1", "database.example"), "aarch64", "loopback"),
        ([], TARGET.replace("benchmark_test", "production"), "aarch64", "loopback"),
        (["--require-arm64"], TARGET, "x86_64", "native ARM64 host"),
        (["--output", "/tmp/escape"], TARGET, "aarch64", "reports directory"),
        (["--baseline-ref", "--help=payload"], TARGET, "aarch64", "expected one argument"),
        (["--baseline-ref", "branch"], TARGET, "aarch64", "full lowercase"),
    ],
)
def test_invalid_inputs_cannot_create_a_database_engine(
    command, monkeypatch, capsys, arguments, target, machine, message
):
    command.argv.extend(arguments)
    monkeypatch.setenv("DEVFEED_TEST_DATABASE_URL", target)
    monkeypatch.setattr(benchmark.platform, "machine", lambda: machine)
    with pytest.raises(SystemExit) as failure:
        benchmark.main()
    assert failure.value.code == 2
    assert message in capsys.readouterr().err
    command.engine.assert_not_called()


def plan():
    return {
        "Execution Time": 4.5,
        "Plan": {
            "Node Type": "Sort",
            "Plan Width": 64,
            "Actual Rows": 500,
            "Actual Loops": 1,
            "Shared Hit Blocks": 20,
            "Plans": [
                {"Node Type": "Index Scan", "Relation Name": "articles", "Actual Loops": 600},
                {
                    "Node Type": "Index Scan",
                    "Relation Name": "article_origins",
                    "Actual Loops": 500,
                },
            ],
        },
    }


@pytest.fixture
def execution(command, monkeypatch):
    statements, captures, migrations, measures, snapshots = [], [], [], [], []
    state = SimpleNamespace(existing_tables=0, version="17.11", build="PostgreSQL aarch64")
    state.eligible_sources = 1000
    state.mismatch = False
    state.ingestion_error = False
    state.writer_stuck = False

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def begin(self):
            snapshots.append([])
            return nullcontext()

        def execution_options(self, **options):
            assert options == {"isolation_level": "REPEATABLE READ"}
            return self

        def scalar(self, statement):
            sql = str(statement)
            if "information_schema.tables" in sql:
                return state.existing_tables
            if sql == "SHOW server_version":
                return state.version
            if sql == "SELECT version()":
                return state.build
            if "count(DISTINCT s.id)" in sql:
                return state.eligible_sources
            return 2000

        def scalars(self, statement):
            return SimpleNamespace(all=lambda: ["sources", "articles"])

        def execute(self, statement, parameters=None):
            if parameters is not None:
                captures.append((statement, parameters))
            return SimpleNamespace(
                all=lambda: [("work_mem", "4096")],
                mappings=lambda: [{"publication_status": "published", "rows": 1580}],
            )

        def exec_driver_sql(self, statement, parameters=None):
            statements.append((statement, parameters))
            return SimpleNamespace(scalar_one=lambda: [plan()])

    connection = Connection()
    engine = SimpleNamespace(
        connect=lambda: connection,
        begin=lambda: nullcontext(connection),
        dispose=Mock(),
    )
    command.engine.return_value = engine

    def git(arguments, **kwargs):
        assert kwargs["cwd"] == command.root
        if arguments == ["git", "--no-replace-objects", "cat-file", "--batch"]:
            revision = kwargs["input"]
            if revision == f"{BASELINE}^{{commit}}\n".encode():
                return f"{BASELINE} commit 6\ncommit\n".encode()
            assert revision == f"{BASELINE}:apps/api/src/feed.py\n".encode()
            return f"{'d' * 40} blob 13\nbaseline code\n".encode()
        if arguments[:2] == ["git", "status"]:
            return b""
        assert arguments == ["git", "rev-parse", "HEAD"]
        return CANDIDATE

    def measure(connection, function, filters):
        measures.append((function, filters))
        snapshots[-1].append(function)
        return (
            "different" if state.mismatch and function == "candidate" else "same public JSON",
            dict(wall_ms=20 if function == "baseline" else 10, application_cpu_ms=2),
            [("SELECT id FROM sources WHERE language = %s", ("en",))],
        )

    class Stop:
        def __init__(self):
            self.stopped = False

        def is_set(self):
            return self.stopped

        def set(self):
            self.stopped = True

        def wait(self, seconds):
            self.set()

    class Writer:
        def __init__(self, *, target, args, daemon):
            assert daemon
            self.target, self.args = target, args

        def start(self):
            if state.ingestion_error:
                self.args[-1].append(dict(error="write failed"))
            else:
                self.target(*self.args)

        def join(self, timeout):
            assert timeout == 30

        def is_alive(self):
            return state.writer_stuck

    monkeypatch.setattr(benchmark.subprocess, "check_output", git)
    monkeypatch.setattr(benchmark.subprocess, "run", lambda *args, **kw: migrations.append(kw))
    monkeypatch.setattr(benchmark, "load_feed", lambda path, name: name.split("_")[1])
    monkeypatch.setattr(benchmark, "measure", measure)
    monkeypatch.setattr(benchmark, "threading", SimpleNamespace(Event=Stop, Thread=Writer))
    monkeypatch.setattr(benchmark.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(benchmark.os, "environ", dict(DEVFEED_TEST_DATABASE_URL=TARGET))
    monkeypatch.setattr(benchmark.os, "chdir", Mock())
    return SimpleNamespace(
        state=state,
        engine=engine,
        statements=statements,
        captures=captures,
        migrations=migrations,
        measures=measures,
        snapshots=snapshots,
    )


def test_full_matrix_pairs_snapshots_alternates_order_and_records_plan_binds(command, execution):
    command.argv.extend(["--rows", "2000", "100000", "--repeats", "1", "--require-arm64"])
    benchmark.main()
    result = json.loads((command.output / "results.json").read_text())
    assert result["expected_cases"] == len(result["cases"]) == 32
    assert result["completed_at"] and not result["candidate_dirty"]
    assert {case["plan_cache_mode"] for case in result["cases"]} == {"auto", "force_generic_plan"}
    assert all(case["equivalent"] and len(case["samples"]) == 1 for case in result["cases"])
    assert execution.snapshots == [["baseline", "candidate"], ["candidate", "baseline"]] * 32
    sql = [statement for statement, _ in execution.statements]
    assert sql.count("SET TRANSACTION READ ONLY") == 64
    assert sql.count("SET LOCAL statement_timeout = '60s'") == 64
    replays = [
        (statement, binds) for statement, binds in execution.statements if "EXPLAIN" in statement
    ]
    assert len(replays) == 64 and all(binds == ("en",) for _, binds in replays)
    assert all(
        "ANALYZE, BUFFERS, WAL, SETTINGS, FORMAT JSON" in statement for statement, _ in replays
    )
    assert len(result["datasets"]) == 2
    assert [batch["batches"] for batch in result["ingestion"] if batch["concurrent"]] == [
        [
            dict(
                articles=20,
                wall_ms=pytest.approx(0, abs=100),
                application_cpu_ms=pytest.approx(0, abs=100),
            )
        ]
    ] * 2
    assert {
        key: value
        for key, value in execution.migrations[0]["env"].items()
        if key.startswith("DEVFEED_")
    } == {
        "DEVFEED_DATABASE_URL": TARGET,
        "DEVFEED_REDIS_URL": "redis://127.0.0.1:1/15",
    }
    assert execution.migrations[0]["check"] is True
    assert sql.index('ALTER TABLE "articles" ENABLE TRIGGER USER') < sql.index("ANALYZE")
    assert "COMPLETE" in (command.output / "summary.md").read_text()
    execution.engine.dispose.assert_called_once()


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("existing_tables", 1, "empty disposable database"),
        ("version", "18.1", "PostgreSQL 17"),
        ("build", "PostgreSQL x86_64", "native ARM64 PostgreSQL"),
    ],
)
def test_database_preflight_failure_always_disposes_engine(
    command, execution, capsys, field, value, message
):
    command.argv.append("--require-arm64")
    setattr(execution.state, field, value)
    with pytest.raises(SystemExit):
        benchmark.main()
    assert message in capsys.readouterr().err
    assert not execution.captures
    execution.engine.dispose.assert_called_once()


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("eligible_sources", 500, "500-source limit"),
        ("mismatch", True, "Result mismatch"),
        ("ingestion_error", True, "Ingestion failed"),
        ("writer_stuck", True, "Ingestion did not stop"),
    ],
)
def test_failed_fixture_comparison_or_ingestion_never_claims_completion(
    command, execution, field, value, message
):
    command.argv.extend(["--rows", "100000", "--repeats", "1"])
    setattr(execution.state, field, value)
    with pytest.raises((AssertionError, RuntimeError), match=message):
        benchmark.main()
    if (command.output / "results.json").exists():
        result = json.loads((command.output / "results.json").read_text())
        assert "completed_at" not in result
        assert "PARTIAL" in (command.output / "summary.md").read_text()
    execution.engine.dispose.assert_called_once()


def test_fixture_sql_uses_bigint_sequences_and_preserves_table_cardinalities(command, execution):
    command.argv.extend(["--rows", "2000", "--repeats", "1"])
    benchmark.main()
    seeds = [
        (str(statement.compile(dialect=postgresql.dialect())), parameters["count"])
        for statement, parameters in execution.captures
    ]
    assert all("generate_series(1::bigint," in sql for sql, _ in seeds)
    counts = {sql.split("INSERT INTO ")[1].split(" ")[0]: count for sql, count in seeds[:11]}
    assert counts["article_tags"] == 18000
    assert counts["article_topics"] == 8000
    assert counts["ingestion_jobs"] == 2740
    assert any("(n + 2000)" in sql and count == 20 for sql, count in seeds)


@pytest.mark.parametrize("raises", [False, True])
def test_measure_serializes_public_output_and_removes_sql_capture_on_failure(raises):
    engine = create_engine("sqlite://")
    with engine.connect() as connection:
        listeners = tuple(connection.dispatch.before_cursor_execute)

        def route(session, **filters):
            session.connection().exec_driver_sql("SELECT ?", ("bound",))
            if raises:
                raise RuntimeError("query failed")
            return dict(sources=[], content_types=[])

        if raises:
            with pytest.raises(RuntimeError, match="query failed"):
                benchmark.measure(connection, route, {})
        else:
            response, timing, statements = benchmark.measure(connection, route, {})
            assert json.loads(response) == dict(sources=[], content_types=[])
            assert statements == [("SELECT ?", ("bound",))]
            assert timing["wall_ms"] >= 0 and timing["application_cpu_ms"] >= 0
        assert tuple(connection.dispatch.before_cursor_execute) == listeners
    engine.dispose()


def test_ingestion_failure_is_recorded_and_stops_future_batches(monkeypatch):
    stop = Mock(is_set=Mock(return_value=False))
    engine = Mock()
    engine.begin.side_effect = RuntimeError("database unavailable")
    batches = []
    benchmark.ingest(engine, 1406, 2000, stop, batches)
    assert batches == [dict(error="RuntimeError('database unavailable')")]
    stop.set.assert_called_once()
    stop.wait.assert_not_called()


def test_load_feed_uses_the_exact_revision_file(tmp_path):
    source = tmp_path / "revision.py"
    source.write_text("def feed_options(): return {'revision': 'baseline'}\n")
    assert benchmark.load_feed(source, "fixture_revision")() == {"revision": "baseline"}


@pytest.mark.parametrize(
    "payload,expected_type,message",
    [
        (b"missing\n", "commit", "valid commit object header"),
        (b"a" * 40 + b" missing\n", "commit", "valid commit object header"),
        (b"a" * 40 + b" commit 1", "commit", "valid commit object header"),
        (b"z" * 40 + b" commit 1\nx\n", "commit", "valid commit object header"),
        (b"a" * 40 + b" blob 1\nx\n", "commit", "valid commit object header"),
        (b"a" * 40 + b" commit -1\n\n", "commit", "valid commit object header"),
        (b"a" * 40 + b" commit unknown\nx\n", "commit", "valid commit object header"),
        (b"a" * 40 + b" commit 2\nx\n", "commit", "length"),
        (b"a" * 40 + b" blob 1\nx", "blob", "length"),
        (b"a" * 40 + b" blob 1\nx\nextra", "blob", "length"),
        (b"a" * 40 + b" blob 1\nxx", "blob", "length"),
    ],
)
def test_git_object_rejects_missing_malformed_and_truncated_responses(
    monkeypatch, payload, expected_type, message
):
    monkeypatch.setattr(benchmark.subprocess, "check_output", lambda *args, **kwargs: payload)
    with pytest.raises(RuntimeError, match=message):
        benchmark.git_object(BASELINE, expected_type)


@pytest.mark.parametrize("content", [b"", "def feed_options(): return 'café'\n".encode()])
def test_git_object_reads_exact_blob_bytes_using_constant_arguments(monkeypatch, content):
    sha = "c" * 40
    response = f"{sha} blob {len(content)}\n".encode() + content + b"\n"
    process = Mock(return_value=response)
    monkeypatch.setattr(benchmark.subprocess, "check_output", process)
    assert benchmark.git_object(f"{BASELINE}:apps/api/src/feed.py", "blob") == (sha, content)
    process.assert_called_once_with(
        ["git", "--no-replace-objects", "cat-file", "--batch"],
        input=f"{BASELINE}:apps/api/src/feed.py\n".encode(),
        cwd=benchmark.ROOT,
    )


def test_main_records_git_returned_commit_provenance(command, execution, monkeypatch):
    command.argv.extend(["--rows", "2000", "--repeats", "1"])
    resolved = "c" * 40
    objects = Mock(side_effect=[(resolved, b"commit body"), ("d" * 40, b"baseline code")])
    monkeypatch.setattr(benchmark, "git_object", objects)
    benchmark.main()
    result = json.loads((command.output / "results.json").read_text())
    assert result["baseline_sha"] == resolved
    assert result["baseline_sha"] != BASELINE
    assert objects.call_args_list[0].args == (f"{BASELINE}^{{commit}}", "commit")
    assert objects.call_args_list[1].args == (f"{resolved}:apps/api/src/feed.py", "blob")
