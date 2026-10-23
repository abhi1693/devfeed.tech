"""Usage accounting requests bounded content-free writes and cannot retry paid work.

The connection records SQLAlchemy statements and supplied values. Upsert
conflicts, locking, and transactional aggregation remain PostgreSQL contracts.
"""

import copy
import uuid
from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from devfeed_core import inference_usage

from tests.support.source_readiness import NOW


def invocation():
    return {
        "id": uuid.uuid4(),
        "started_at": NOW,
        "finished_at": NOW + timedelta(milliseconds=10),
        "operation": "source_relevance",
        "job_id": None,
        "attempt": 1,
        "reason": "source_review",
        "model": "test-model",
        "reasoning_effort": None,
        "request_hash": "a" * 64,
        "status": "returned",
        "tokens": {"inputTokens": 50, "outputTokens": 20},
        "web_searches": 2,
        "duration_ms": 10,
    }


@pytest.fixture
def ledger(monkeypatch):
    engine, connection = MagicMock(), MagicMock()
    engine.begin.return_value.__enter__.return_value = connection
    monkeypatch.setattr(inference_usage, "get_engine", lambda: engine)
    return engine, connection


def test_usage_receipt_sets_both_deadlines_before_forwarding_content_free_parameters(ledger):
    _, connection = ledger
    values = invocation()
    before = copy.deepcopy(values)

    assert inference_usage.record_call(values) is None

    requests = [call.args[0] for call in connection.execute.call_args_list]
    assert [statement.text for statement in requests[:2]] == [
        "SET LOCAL statement_timeout='3s'",
        "SET LOCAL lock_timeout='1s'",
    ]
    assert len(requests) == 3 and requests[2].table.name == "inference_calls"
    parameters = requests[2].compile().params
    assert all(parameters[key] == value for key, value in values.items())
    assert not {"prompt", "schema", "response", "credential"} & parameters.keys()
    assert values == before


@pytest.mark.parametrize("failure_at", ["begin", 0, 1, 2])
def test_accounting_dependency_failure_is_nonfatal_and_logs_only_the_invocation_identity(
    ledger, caplog, failure_at
):
    engine, connection = ledger
    error = RuntimeError("private connection string and generated response")
    if failure_at == "begin":
        engine.begin.side_effect = error
    else:
        connection.execute.side_effect = [None] * failure_at + [error]
    values = invocation()

    assert inference_usage.record_call(values) is None

    [event] = [
        record
        for record in caplog.records
        if record.message == "inference_usage_persistence_failed"
    ]
    assert event.inference_id == str(values["id"]) and event.exc_info is None
    assert "private" not in caplog.text and "generated response" not in caplog.text
    assert connection.execute.call_count == (0 if failure_at == "begin" else failure_at + 1)
