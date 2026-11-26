import pytest
from devfeed_core.models import SearchQueryStat
from devfeed_core.search_suggestions import (
    query_hash,
    record_successful_query,
)

pytestmark = pytest.mark.integration


def test_successful_query_metadata_is_normalized_without_pg_analytics_count(database):
    with database.begin() as session:
        first = record_successful_query(session, "  Kubernetes   Networking ")
        second = record_successful_query(session, "kubernetes networking")
        row = session.get(SearchQueryStat, first)
        assert first == second == query_hash("kubernetes networking")
        assert row.query == "kubernetes networking"
        assert not {"successful_count", "first_seen_at", "last_seen_at"} & set(
            SearchQueryStat.__table__.columns.keys()
        )
        assert row.reviewed_by is None
