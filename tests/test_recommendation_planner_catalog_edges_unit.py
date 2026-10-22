"""Authoritative catalog estimates select a strategy, never reader eligibility."""

from unittest.mock import Mock

import pytest
from devfeed_core.recommendations import _use_broad_candidate_query
from sqlalchemy.orm import Session


def catalog_statistics(content_fraction, language_fraction):
    return [
        {
            "attname": "content_type",
            "identities": ["tutorial", "news"],
            "frequencies": [content_fraction, 1 - content_fraction],
        },
        {
            "attname": "language",
            "identities": ["en", "fr"],
            "frequencies": [language_fraction, 1 - language_fraction],
        },
    ]


@pytest.mark.parametrize("column", ["content_type", "language"])
def test_missing_most_common_value_statistics_keep_existing_candidate_probes(column):
    rows, session = catalog_statistics(0.5, 0.5), Mock(spec=Session)
    for row in rows:
        if row["attname"] == column:
            # PostgreSQL may omit MCV arrays for an unanalyzed/all-null column.
            row["identities"] = row["frequencies"] = None
    session.execute.return_value.mappings.return_value = rows
    assert not _use_broad_candidate_query(session, 12, ["tutorial"], ["en"])
    session.execute.assert_called_once()
    session.commit.assert_not_called()


@pytest.mark.parametrize("content_fraction,language_fraction", [(0.5, 0.5), (0.2, 0.2)])
def test_broad_profiles_with_selective_filters_use_shared_candidate_ranking(
    content_fraction, language_fraction
):
    session = Mock(spec=Session)
    session.execute.return_value.mappings.return_value = catalog_statistics(
        content_fraction, language_fraction
    )
    assert _use_broad_candidate_query(session, 12, ["tutorial"], ["en"])
    # Filter estimates suffice; no owned association or article rows are loaded.
    session.execute.assert_called_once()
    session.commit.assert_not_called()
