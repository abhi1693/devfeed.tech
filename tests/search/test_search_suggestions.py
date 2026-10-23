import pytest
from devfeed_core.models import SearchQueryStat
from devfeed_core.search_suggestions import (
    approved_suggestions,
    normalize_query,
    query_hash,
    record_successful_query,
    review_query,
)

pytestmark = pytest.mark.integration


def test_successful_queries_are_normalized_aggregated_and_identity_free(database):
    with database.begin() as session:
        first = record_successful_query(session, "  Kubernetes   Networking ")
        second = record_successful_query(session, "kubernetes networking")
        row = session.get(SearchQueryStat, first)
        assert first == second == query_hash("kubernetes networking")
        assert row.query == "kubernetes networking"
        assert row.successful_count == 2
        assert row.reviewed_by is None


def test_only_approved_queries_above_volume_threshold_are_suggested(database):
    with database.begin() as session:
        approved = record_successful_query(session, "kubernetes")
        record_successful_query(session, "kubernetes")
        record_successful_query(session, "python")
        review_query(session, approved, status="approved", reviewer="admin", note="safe")
        pending = record_successful_query(session, "kubernetes networking")
        review_query(session, pending, status="approved", reviewer="admin", note=None)
        result = approved_suggestions(session, "kube", limit=5, minimum=2)
        assert result == ["kubernetes"]


def test_rejected_query_remains_hidden_and_query_validation_is_bounded(database):
    with database.begin() as session:
        digest = record_successful_query(session, "unsafe suggestion")
        review_query(session, digest, status="rejected", reviewer="admin", note="not useful")
        assert approved_suggestions(session, "unsafe", limit=5, minimum=1) == []
    assert normalize_query("  Python\tAsync ") == "python async"
    for value in ["", "!!!", "word " * 21]:
        try:
            normalize_query(value)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid query was accepted")
