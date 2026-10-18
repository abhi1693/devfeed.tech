"""Planner estimates choose SQL strategy without changing eligibility or candidate budgets."""

from types import SimpleNamespace

import pytest
from devfeed_core.models import Article
from devfeed_core.recommendations import _use_broad_candidate_query
from sqlalchemy import insert, text


def test_unknown_statistics_keep_the_existing_query():
    session = SimpleNamespace(execute=lambda *_: SimpleNamespace(mappings=lambda: []))
    assert not _use_broad_candidate_query(session, 20, ["article"], ["en"])


def test_narrow_profiles_do_not_read_statistics():
    session = SimpleNamespace(execute=lambda *_: pytest.fail("Unexpected statistics query"))
    assert not _use_broad_candidate_query(session, 2, ["article"], ["en"])


@pytest.mark.integration
def test_postgres_statistics_separate_dense_sparse_and_unmatched_preferences(database):
    with database.begin() as session:
        session.execute(
            insert(Article),
            [
                dict(
                    title=f"Profile {index}",
                    canonical_url=f"https://example.test/profile/{index}",
                    url_hash=f"{index:064x}",
                    language="en" if index % 10 else "fr",
                    content_type="tutorial" if index % 5 == 0 else "article",
                )
                for index in range(1000)
            ],
        )
        session.execute(text("ANALYZE articles"))
        assert not _use_broad_candidate_query(session, 16, ["article", "tutorial"], ["en", "fr"])
        assert _use_broad_candidate_query(session, 16, ["tutorial"], ["en"])
        assert _use_broad_candidate_query(session, 16, ["comparison"], ["ja"])
