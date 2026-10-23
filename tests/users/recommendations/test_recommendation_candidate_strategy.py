"""Planner estimates choose SQL strategy without changing eligibility or candidate budgets."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core.models import Article
from devfeed_core.recommendations import (
    _association_row_estimate,
    _reuse_candidate_visibility,
    _use_broad_candidate_query,
)
from sqlalchemy import insert, text
from sqlalchemy.orm import Session


def test_unknown_statistics_keep_the_existing_query():
    session = SimpleNamespace(execute=lambda *_: SimpleNamespace(mappings=lambda: []))
    assert not _use_broad_candidate_query(session, 20, ["article"], ["en"])


def test_narrow_profiles_do_not_read_statistics():
    session = SimpleNamespace(execute=lambda *_: pytest.fail("Unexpected statistics query"))
    assert not _use_broad_candidate_query(session, 2, ["article"], ["en"])


def association_statistics(**changes):
    return {
        "total_rows": 1000,
        "n_distinct": 100,
        "null_frac": 0.1,
        "identities": ["hot"],
        "frequencies": [0.5],
        "selected_ids": ["hot", "rare", "hot"],
        "article_rows": 400,
        **changes,
    }


def test_association_estimates_count_distinct_signals_and_residual_nonnull_mass():
    assert _association_row_estimate(association_statistics()) == pytest.approx(500 + 400 / 99)
    assert _association_row_estimate(association_statistics(n_distinct=-0.1)) == pytest.approx(
        500 + 400 / 99
    )
    assert _association_row_estimate(association_statistics(selected_ids=[])) == 0
    assert _association_row_estimate(
        association_statistics(identities=None, frequencies=None, selected_ids=["rare"])
    ) == pytest.approx(9)


@pytest.mark.parametrize(
    "changes",
    [
        {"total_rows": None},
        {"total_rows": 0},
        {"total_rows": float("inf")},
        {"n_distinct": 0},
        {"n_distinct": float("nan")},
        {"null_frac": -1},
        {"null_frac": 2},
        {"frequencies": []},
        {"frequencies": [None]},
        {"frequencies": [-0.1]},
        {"frequencies": [float("nan")]},
        {"frequencies": [1.1]},
        {"identities": ["same", "same"], "frequencies": [0.2, 0.2]},
        {"n_distinct": 1, "identities": ["first", "second"], "frequencies": [0.2, 0.2]},
        {"null_frac": 0.7, "frequencies": [0.5]},
    ],
)
def test_unknown_or_inconsistent_association_statistics_keep_existing_probes(changes):
    assert _association_row_estimate(association_statistics(**changes)) is None


@pytest.mark.parametrize(
    "rows,expected",
    [
        ([association_statistics()], True),
        ([association_statistics(selected_ids=["rare"])], False),
        ([association_statistics(article_rows=500 + 400 / 99)], True),
        ([association_statistics(article_rows=0)], False),
        ([association_statistics(article_rows=None)], False),
        ([association_statistics(article_rows=float("inf"))], False),
        ([association_statistics(n_distinct=None)], False),
        ([], False),
    ],
)
def test_visibility_reuse_requires_at_least_one_estimated_article_catalog_pass(rows, expected):
    session = SimpleNamespace(execute=lambda *_: SimpleNamespace(mappings=lambda: rows))
    assert _reuse_candidate_visibility(session, "owned-user") is expected


def test_dense_filter_selection_uses_owned_association_statistics():
    filters = [
        {"attname": "content_type", "identities": ["article"], "frequencies": [1]},
        {"attname": "language", "identities": ["en"], "frequencies": [1]},
    ]
    results = iter([filters, [association_statistics()]])
    session = SimpleNamespace(execute=lambda *_: SimpleNamespace(mappings=lambda: next(results)))
    assert _use_broad_candidate_query(session, 379, ["article"], ["en"], "owned-user")


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


@pytest.mark.integration
def test_owned_signal_statistics_keep_low_fanout_probes_and_share_repeated_visibility(database):
    from devfeed_core.db import get_engine
    from devfeed_core.models import UserInterest, UserSource, UserTag
    from devfeed_core.recommendations import interests
    from sqlalchemy import delete

    from tests.tooling.profiling.test_reader_recommendation_fixture import module

    profile = module("profile_reader_views")
    helper = profile.helper_module()
    profile.seed_fixture(get_engine(), profile.fixture_counts(0.001), helper)
    with database.begin() as session:
        for number in (3, 6):
            user_id = helper.fixture_id("user_accounts", number)
            selected = interests(session, user_id)
            if number == 6:
                topics = {helper.fixture_id("topics", index) for index in range(1, 17)}
                selected = [row for row in selected if row["topic_id"] in topics]
                for model, field, kind in (
                    (UserSource, UserSource.source_id, "sources"),
                    (UserTag, UserTag.tag_id, "tags"),
                ):
                    keep = [helper.fixture_id(kind, index) for index in range(1, 17)]
                    session.execute(
                        delete(model).where(model.user_id == user_id, field.not_in(keep))
                    )
            session.execute(
                insert(UserInterest), [dict(user_id=user_id, **row) for row in selected]
            )
        assert _use_broad_candidate_query(
            session, 379, ["article", "news"], ["en"], helper.fixture_id("user_accounts", 3)
        )
        assert not _use_broad_candidate_query(
            session, 48, ["article", "news"], ["en"], helper.fixture_id("user_accounts", 6)
        )


@pytest.mark.integration
@pytest.mark.parametrize("languages", [["en"], ["de"]])
def test_shared_visibility_preserves_exact_bounded_rankings(database, monkeypatch, languages):
    from devfeed_core import recommendations
    from devfeed_core.db import get_engine
    from devfeed_core.models import UserInterest, utcnow

    from tests.tooling.profiling.test_reader_recommendation_fixture import module

    profile = module("profile_reader_views")
    helper = profile.helper_module()
    profile.seed_fixture(get_engine(), profile.fixture_counts(0.001), helper)
    user_id = helper.fixture_id("user_accounts", 3)
    now = utcnow()
    results = []
    with database.begin() as session:
        selected = recommendations.interests(session, user_id)
        session.execute(insert(UserInterest), [dict(user_id=user_id, **row) for row in selected])
        for shared in (False, True):
            monkeypatch.setattr(
                recommendations, "_use_broad_candidate_query", lambda *_, plan=shared: plan
            )
            results.append(
                recommendations.ranked_candidates(
                    session, user_id, now, len(selected) + 200, ["article", "news"], languages
                )
            )
    assert results[0] == results[1]
    if languages == ["en"]:
        assert len(results[0]) == 500
    else:
        assert results[0] == []


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
