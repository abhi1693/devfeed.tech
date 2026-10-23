"""The benchmark must analyze committed fixtures without touching other databases."""

import importlib.util
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.support.paths import REPO_ROOT

spec = importlib.util.spec_from_file_location("load_seed", REPO_ROOT / "loadtests/seed.py")
assert spec and spec.loader
seed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed)


class SeedEngine:
    def __init__(self, *, existing_rows=0):
        self.existing_rows = existing_rows
        self.committed = {}
        self.pending = {}
        self.analyzed = False
        self.disposed = False

    @contextmanager
    def begin(self):
        self.pending = {}
        yield self
        self.committed.update(self.pending)

    def scalar(self, statement):
        return self.existing_rows

    def execute(self, statement, parameters=None):
        if parameters is not None:
            self.pending[statement.table.name] = len(parameters)
        elif str(statement) == "ANALYZE":
            # Analyze must see the complete committed fixture, including links.
            assert self.committed == {
                "sources": 20,
                "topics": 20,
                "articles": 100,
                "article_origins": 100,
                "article_topics": 100,
            }
            self.analyzed = True
        else:
            assert self.analyzed
            return SimpleNamespace(
                mappings=lambda: [
                    {
                        "table_name": name,
                        "estimated_rows": float(count),
                        "pages": 1,
                        "last_analyze": datetime(2026, 1, 1, tzinfo=UTC),
                        "last_autoanalyze": None,
                    }
                    for name, count in sorted(self.committed.items())
                ]
            )

    def dispose(self):
        self.disposed = True


def configure(monkeypatch, tmp_path, engine, url):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DEVFEED_DATABASE_URL", url)
    monkeypatch.setenv("DEVFEED_REDIS_URL", "redis://127.0.0.1:6379/15")
    monkeypatch.setattr(seed.sys, "argv", ["seed.py", "100"])
    monkeypatch.setattr(seed, "create_database_engine", lambda settings: engine)


def test_seed_analyzes_committed_rows_and_preserves_structured_statistics(
    monkeypatch, tmp_path, capsys
):
    engine = SeedEngine()
    configure(
        monkeypatch,
        tmp_path,
        engine,
        "postgresql+psycopg://load:load@127.0.0.1:5432/devfeed_load_test",
    )
    seed.main()
    streams = capsys.readouterr()
    statistics = json.loads(streams.out)
    assert {row["table_name"]: row["estimated_rows"] for row in statistics} == engine.committed
    assert all(row["last_analyze"] == "2026-01-01 00:00:00+00:00" for row in statistics)
    assert all(row["last_autoanalyze"] is None for row in statistics)
    assert streams.err == "Seeded 100 published articles, 20 sources and 20 topics\n"
    assert engine.analyzed and engine.disposed
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://load:load@127.0.0.1:5432/devfeed",
        "postgresql+psycopg://load:load@database:5432/devfeed_load_test",
    ],
)
def test_seed_rejects_non_runner_databases_before_analysis(monkeypatch, tmp_path, capsys, url):
    engine = SeedEngine()
    configure(monkeypatch, tmp_path, engine, url)
    with pytest.raises(ValueError, match="runner's disposable loopback"):
        seed.main()
    assert not engine.committed and not engine.analyzed
    assert not list(tmp_path.iterdir())
    assert not capsys.readouterr().out


def test_existing_rows_abort_without_analyzing_the_database(monkeypatch, tmp_path, capsys):
    engine = SeedEngine(existing_rows=1)
    configure(
        monkeypatch,
        tmp_path,
        engine,
        "postgresql+psycopg://load:load@127.0.0.1:5432/devfeed_load_test",
    )
    with pytest.raises(ValueError, match="nonempty database"):
        seed.main()
    assert not engine.committed and not engine.analyzed
    assert engine.disposed and not list(tmp_path.iterdir())
    assert not capsys.readouterr().out


@pytest.mark.parametrize("kind", ["relative", "traversal", "absolute", "symlink", "new-file"])
def test_seed_rejects_output_arguments_before_database_or_filesystem_side_effects(
    monkeypatch, tmp_path, capsys, kind
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("outside sentinel")
    existing = workspace / "existing.json"
    existing.write_text("workspace sentinel")
    symlink = workspace / "statistics.json"
    symlink.symlink_to(outside)
    paths = {
        "relative": "existing.json",
        "traversal": "../outside.json",
        "absolute": str(outside),
        "symlink": str(symlink),
        "new-file": str(tmp_path / "new.json"),
    }
    monkeypatch.chdir(workspace)
    monkeypatch.delenv("DEVFEED_DATABASE_URL", raising=False)
    monkeypatch.setattr(seed.sys, "argv", ["seed.py", "100", paths[kind]])
    factory = Mock()
    monkeypatch.setattr(seed, "create_database_engine", factory)

    with pytest.raises(SystemExit) as error:
        seed.main()

    assert error.value.code == 2
    factory.assert_not_called()
    streams = capsys.readouterr()
    assert not streams.out and "unrecognized arguments" in streams.err
    assert outside.read_text() == "outside sentinel"
    assert existing.read_text() == "workspace sentinel"
    assert symlink.is_symlink()
    assert {path.name for path in tmp_path.iterdir()} == {"workspace", "outside.json"}
    assert {path.name for path in workspace.iterdir()} == {"existing.json", "statistics.json"}


@pytest.mark.parametrize("rows", ["99", "10001", "not-a-number"])
def test_seed_rejects_invalid_row_counts_before_connecting(monkeypatch, capsys, rows):
    monkeypatch.delenv("DEVFEED_DATABASE_URL", raising=False)
    monkeypatch.setattr(seed.sys, "argv", ["seed.py", rows])
    factory = Mock()
    monkeypatch.setattr(seed, "create_database_engine", factory)
    with pytest.raises(SystemExit) as error:
        seed.main()
    assert error.value.code == 2
    factory.assert_not_called()
    assert not capsys.readouterr().out
