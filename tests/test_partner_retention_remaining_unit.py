"""Receipt cleanup bounds each ledger operation and reports repository outcomes.

The database supplies lock admission and delete row counts. These units make no
claims about PostgreSQL row selection, isolation, locks, or transaction rollback.
"""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core.models import PartnerDeliveryEvent, PartnerPlacementDelivery
from devfeed_core.partner_tracking import prune_partner_delivery_history
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from test_partner_matching_workflow_unit import NOW


@pytest.mark.parametrize("batch", [0, 4001])
def test_receipt_cleanup_rejects_unbounded_batches_before_opening_storage(batch):
    factory = Mock()
    with pytest.raises(ValueError, match="cleanup batch must be 1..4000"):
        prune_partner_delivery_history(factory, batch_size=batch, now=NOW)
    factory.begin.assert_not_called()


def test_unadmitted_cleanup_does_not_issue_any_ledger_writes():
    session, factory = Mock(spec=Session), Mock()
    session.scalar.return_value = False
    factory.begin.return_value = nullcontext(session)
    assert prune_partner_delivery_history(factory, now=NOW, batch_size=3) == 0
    session.execute.assert_not_called()


@pytest.mark.parametrize("counts", [(0, 0), (2, 3)])
def test_cleanup_returns_both_ledger_counts_and_preserves_daily_reporting_tables(counts):
    session, factory = Mock(spec=Session), Mock()
    session.scalar.return_value = True
    session.execute.side_effect = [SimpleNamespace(rowcount=count) for count in counts]
    factory.begin.return_value = nullcontext(session)
    assert prune_partner_delivery_history(factory, now=NOW, batch_size=3) == sum(counts)
    assert [call.args[0].table.name for call in session.execute.call_args_list] == [
        PartnerDeliveryEvent.__tablename__,
        PartnerPlacementDelivery.__tablename__,
    ]
    session.add.assert_not_called()


@pytest.mark.parametrize("failed_operation", [0, 1])
def test_cleanup_propagates_repository_failure_instead_of_reporting_partial_success(
    failed_operation,
):
    session, factory = Mock(spec=Session), Mock()
    session.scalar.return_value = True
    error = SQLAlchemyError("receipt ledger unavailable")
    session.execute.side_effect = [SimpleNamespace(rowcount=2)] * failed_operation + [error]
    factory.begin.return_value = nullcontext(session)
    with pytest.raises(SQLAlchemyError) as failure:
        prune_partner_delivery_history(factory, now=NOW, batch_size=3)
    assert failure.value is error
    assert session.execute.call_count == failed_operation + 1
    session.add.assert_not_called()
