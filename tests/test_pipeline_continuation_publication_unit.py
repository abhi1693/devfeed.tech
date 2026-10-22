"""A publisher forwards its bounded continuation plan before acknowledging events.

Scoped read results are supplied by the repository boundary. These units prove
publication ordering and authority, not PostgreSQL atomicity or row selection.
"""

import uuid
from datetime import timedelta
from unittest.mock import Mock, call

import pytest
from devfeed_core.models import ReadModelState
from test_admin_projection_worker_policy_unit import NOW, event, publish
from test_admin_projection_worker_policy_unit import publisher as publisher


@pytest.mark.parametrize("target", ["article", "source", "candidate", "source_fanout"])
def test_full_backfill_or_fanout_publishes_the_exact_next_cursor_before_acknowledgement(
    publisher, target
):
    value = publisher
    identifiers = [uuid.UUID(int=101), uuid.UUID(int=102)]
    source = uuid.UUID(int=10)
    kind = target if target == "source_fanout" else f"backfill_{target}"
    value.incoming.append(event(40, kind, source if target == "source_fanout" else None))
    value.session.scalars.side_effect = [[], identifiers]
    ordered = Mock()
    ordered.attach_mock(value.enqueue, "enqueue")
    ordered.attach_mock(value.acknowledge, "acknowledge")
    ordered.attach_mock(value.save, "save")

    assert publish(value, batch=2) == 1

    expected = {"kind": kind, "after_id": identifiers[-1]}
    if target == "source_fanout":
        expected["entity_id"] = source
    assert ordered.mock_calls[:2] == [
        call.enqueue(value.session, **expected),
        call.acknowledge(value.session, [40]),
    ]
    assert ordered.mock_calls[2][0] == "save"
    saved = value.save.call_args.args[2]
    assert saved["serving_policy"] == "previous-policy" and not saved["backfill_complete"]
    assert saved["backfill_progress"]


def test_continuation_failure_propagates_before_acknowledging_or_saving_the_plan(publisher):
    value = publisher
    value.incoming.append(event(41, "backfill_article"))
    value.session.scalars.side_effect = [[], [uuid.UUID(int=101)]]
    value.session.scalar.side_effect = [value.lease, value.lease]
    value.enqueue.side_effect = RuntimeError("outbox unavailable")

    with pytest.raises(RuntimeError, match="outbox unavailable"):
        publish(value, batch=1)

    value.enqueue.assert_called_once()
    value.acknowledge.assert_not_called()
    value.save.assert_not_called()


def test_lease_replacement_discards_a_computed_continuation_without_acknowledgement(publisher):
    value = publisher
    value.incoming.append(event(42, "backfill_article"))
    value.session.scalars.side_effect = [[], [uuid.UUID(int=101)]]
    replacement = ReadModelState(
        name="pipeline-worker",
        payload={
            "token": "replacement-owner",
            "expires_at": (NOW + timedelta(minutes=2)).isoformat(),
        },
    )
    value.session.scalar.side_effect = [replacement, replacement]

    assert publish(value, batch=1) == 0

    value.enqueue.assert_not_called()
    value.acknowledge.assert_not_called()
    value.save.assert_not_called()
    assert value.writes == []
