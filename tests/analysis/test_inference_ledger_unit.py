"""Fail-closed reservation boundaries and the operator's import receipt."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock
from uuid import uuid4

import psycopg
import pytest
from devfeed_core import inference_history_import as importer
from devfeed_core.inference_ledger import (
    QUOTA_LOCK,
    UPSERT,
    InferenceLedger,
    prune_inference_history,
)
from devfeed_core.inference_observation import observation


def invocation(**changes):
    return {
        "id": str(uuid4()),
        "operation": "relationship_verification",
        "started_at": datetime.now(UTC),
        "status": "running",
        **changes,
    }


@pytest.fixture
def connection(monkeypatch):
    conn = MagicMock()
    conn.__aenter__.return_value = conn
    conn.execute = AsyncMock()
    conn.cursor.return_value.__aenter__.return_value = AsyncMock()
    connect = AsyncMock(return_value=conn)
    monkeypatch.setattr("devfeed_core.inference_ledger.AsyncConnection.connect", connect)
    ledger = InferenceLedger(
        SimpleNamespace(
            database_url="postgresql+psycopg://test", database_connect_timeout_seconds=3
        )
    )
    return ledger, conn, connect


def returned(row):
    return SimpleNamespace(fetchone=AsyncMock(return_value=row))


@pytest.mark.parametrize(
    "limit,change",
    [(True, {}), (-1, {}), (2, {"operation": "article_analysis"}), (2, {"status": "returned"})],
)
def test_invalid_reservation_never_opens_a_connection(connection, limit, change):
    ledger, _, connect = connection
    with pytest.raises(ValueError):
        asyncio.run(ledger.admit(invocation(**change), limit))
    connect.assert_not_awaited()


@pytest.mark.parametrize(
    ("count", "inserted", "expected"),
    [(None, False, False), (2, False, False), (1, False, False), (1, True, True)],
)
def test_locked_admission_respects_cutover_and_duplicate_receipts(
    connection, count, inserted, expected
):
    ledger, conn, connect = connection
    values = invocation()
    conn.execute.side_effect = [
        None,
        None,
        returned(None if count is None else {"calls": count}),
        returned({"id": values["id"]} if inserted else None),
    ]
    assert asyncio.run(ledger.admit(values, 2)) == expected
    connect.assert_awaited_once()
    assert connect.await_args.args == ("postgresql://test",)
    statements = [call.args[0] for call in conn.execute.await_args_list]
    assert statements[1] == QUOTA_LOCK and "inference_quota_cutover" in statements[2]
    assert any("INSERT" in s for s in statements) == (count is not None and count < 2)


def test_provider_pacing_still_requires_commit_before_dispatch(connection):
    ledger, conn, _ = connection
    conn.execute.side_effect = [None, None, returned({"id": "reserved"})]
    conn.__aexit__.side_effect = psycopg.OperationalError("ambiguous commit")
    with pytest.raises(psycopg.OperationalError):
        asyncio.run(ledger.admit(invocation(), None))
    assert all("count(*)" not in call.args[0] for call in conn.execute.await_args_list)


def test_completed_observation_uses_revision_guard_and_omits_private_fields(connection):
    ledger, conn, _ = connection
    values = invocation(status="returned", tokens={"inputTokens": 23}, prompt="sensitive")
    asyncio.run(ledger.write(values))
    statement, parameters = conn.execute.await_args.args
    assert statement == UPSERT
    started, identity, operation, revision, usage = parameters
    assert (started, identity, operation, revision) == (
        values["started_at"],
        values["id"],
        values["operation"],
        1,
    )
    assert usage.obj["input_tokens"] == 23 and "sensitive" not in repr(usage.obj)


def test_cleanup_uses_bounded_skip_locked_and_retains_the_active_window():
    factory = MagicMock()
    prune_inference_history(factory)
    statement = str(factory.begin.return_value.__enter__.return_value.execute.call_args.args[0])
    assert "interval '30 days'" in statement and "LIMIT 1000 FOR UPDATE SKIP LOCKED" in statement


@pytest.mark.parametrize(
    "checkpoint,mismatches,expected_error",
    [
        ([], 0, "incomplete"),
        ([{"complete": 0}], 0, "incomplete"),
        ([{"complete": 1}], 1, "differs"),
        ([{"complete": 1}], 0, None),
    ],
)
def test_copy_and_verification_precede_marking_the_cutover(
    connection, checkpoint, mismatches, expected_error
):
    ledger, conn, _ = connection
    row = observation(invocation())
    row["started_at"] = row["started_at"].isoformat()
    cutoff = datetime.now(UTC)
    source = AsyncMock()
    source.query.side_effect = [checkpoint, [row]]
    conn.execute.side_effect = [
        None,
        returned({"cutoff": cutoff}),
        None,
        returned({"n": mismatches}),
        None,
    ]
    if expected_error:
        with pytest.raises(RuntimeError, match=expected_error):
            asyncio.run(importer.import_history(source, ledger))
    else:
        assert asyncio.run(importer.import_history(source, ledger)) == 1
    statements = [call.args[0] for call in conn.execute.await_args_list]
    assert statements[0] == QUOTA_LOCK
    assert any("INSERT INTO inference_quota_cutover" in sql for sql in statements) == (
        expected_error is None
    )
    if checkpoint == [{"complete": 1}]:
        assert source.query.await_args.args[1] == {"cutoff": cutoff}
        staged, merged = conn.cursor.return_value.__aenter__.return_value.execute.await_args_list
        assert "INSERT INTO imported_inference" in staged.args[0]
        assert merged.args[0] == UPSERT and staged.args[1][0] == merged.args[1][0]


@pytest.mark.parametrize("failure", [False, True])
def test_cli_emits_a_verified_count_or_nonzero_sanitized_failure(monkeypatch, capsys, failure):
    source = MagicMock()
    monkeypatch.setattr(importer, "AnalyticsClient", lambda: source)
    monkeypatch.setattr(importer, "get_settings", Mock())
    monkeypatch.setattr(importer, "InferenceLedger", Mock())
    copy = AsyncMock(return_value=13, side_effect=RuntimeError("private DSN") if failure else None)
    monkeypatch.setattr(importer, "import_history", copy)
    if failure:
        with pytest.raises(SystemExit, match="verification failed") as stopped:
            asyncio.run(importer.main())
        assert "private" not in str(stopped.value)
        assert not capsys.readouterr().out
    else:
        asyncio.run(importer.main())
        assert capsys.readouterr().out.strip() == "Verified 13 inference records"
    source.__aexit__.assert_awaited_once()


def test_module_entrypoint_returns_failure_when_source_cannot_connect(monkeypatch, capsys):
    import runpy

    source = MagicMock()
    source.__aenter__.side_effect = ConnectionError("private connection details")
    monkeypatch.setattr("devfeed_core.analytics.AnalyticsClient", lambda: source)
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(importer.__file__, run_name="__main__")
    assert str(stopped.value) == "Inference history verification failed (ConnectionError)"
    assert not capsys.readouterr().out
