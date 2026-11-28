from datetime import UTC
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import search_suggestions
from devfeed_core.models import SearchQueryStat


def test_review_validates_query_identity_and_records_editorial_decision():
    session = Mock()
    session.get.return_value = None
    with pytest.raises(ValueError):
        search_suggestions.review_query(
            session, "BAD", status="approved", reviewer="admin", note=None
        )
    with pytest.raises(LookupError):
        search_suggestions.review_query(
            session, "a" * 64, status="approved", reviewer="admin", note=None
        )
    value = SimpleNamespace(status="pending", reviewed_at=None, reviewed_by=None, review_note=None)
    session.get.return_value = value
    assert (
        search_suggestions.review_query(
            session, "a" * 64, status="rejected", reviewer="admin", note="Private"
        )
        is value
    )
    session.get.assert_called_with(SearchQueryStat, "a" * 64)
    assert (value.status, value.reviewed_by, value.review_note) == ("rejected", "admin", "Private")
    assert value.reviewed_at.tzinfo == UTC


@pytest.mark.parametrize("value", ["x" * 201, "word " * 21, "!!!", ""])
def test_search_query_limits_reject_invalid_metadata_before_any_io(value):
    with pytest.raises(ValueError):
        search_suggestions.normalize_query(value)
