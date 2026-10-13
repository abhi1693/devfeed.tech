"""The benchmark must analyze committed fixtures without touching other databases."""

import importlib.util
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "load_seed", Path(__file__).resolve().parents[1] / "loadtests/seed.py"
)
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
    destination = tmp_path / "database-statistics.json"
    monkeypatch.setenv("DEVFEED_DATABASE_URL", url)
    monkeypatch.setenv("DEVFEED_REDIS_URL", "redis://127.0.0.1:6379/15")
    monkeypatch.setattr(seed.sys, "argv", ["seed.py", "100", str(destination)])
    monkeypatch.setattr(seed, "create_database_engine", lambda settings: engine)
    return destination


def test_seed_analyzes_committed_rows_and_preserves_structured_statistics(monkeypatch, tmp_path):
    engine = SeedEngine()
    destination = configure(
        monkeypatch,
        tmp_path,
        engine,
        "postgresql+psycopg://load:load@127.0.0.1:5432/devfeed_load_test",
    )
    seed.main()
    statistics = json.loads(destination.read_text())
    assert {row["table_name"]: row["estimated_rows"] for row in statistics} == engine.committed
    assert all(row["last_analyze"] == "2026-01-01 00:00:00+00:00" for row in statistics)
    assert all(row["last_autoanalyze"] is None for row in statistics)
    assert engine.analyzed and engine.disposed


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://load:load@127.0.0.1:5432/devfeed",
        "postgresql+psycopg://load:load@database:5432/devfeed_load_test",
    ],
)
def test_seed_rejects_non_runner_databases_before_analysis(monkeypatch, tmp_path, url):
    engine = SeedEngine()
    destination = configure(monkeypatch, tmp_path, engine, url)
    with pytest.raises(ValueError, match="runner's disposable loopback"):
        seed.main()
    assert not engine.committed and not engine.analyzed
    assert not destination.exists()


def test_existing_rows_abort_without_analyzing_the_database(monkeypatch, tmp_path):
    engine = SeedEngine(existing_rows=1)
    destination = configure(
        monkeypatch,
        tmp_path,
        engine,
        "postgresql+psycopg://load:load@127.0.0.1:5432/devfeed_load_test",
    )
    with pytest.raises(ValueError, match="nonempty database"):
        seed.main()
    assert not engine.committed and not engine.analyzed
    assert engine.disposed and not destination.exists()
