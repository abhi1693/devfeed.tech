"""Saved publication reports use narrow review identities with exact intervention cutoffs."""

from datetime import timedelta

import pytest
from devfeed_core.models import ArticleReview

from tests.admin.reporting.projections.test_pipeline_projection_safety import add_article
from tests.support.analytics import load_section
from tests.support.source_readiness import NOW

pytestmark = pytest.mark.integration


def publication_summary(session, start, now):
    days = (now.date() - start.date()).days + 1
    return load_section(session, "publishing", days, now).data.summary.model_dump(
        exclude={"p95_seconds"}
    )


def test_review_facts_count_distinct_articles_and_do_not_weight_publication_duration(database):
    start = NOW - timedelta(days=1)
    automatic = {"policy": "automatic"}
    cases = [
        ([], 60),
        ([("approve", {})], 120),
        ([("publish", automatic)], 180),
        ([("publish", automatic), ("approve", {})], 240),
        ([("publish", automatic)] * 4, 300),
        ([("approve", automatic), ("reject", automatic)], 360),
        ([("publish", automatic), ("reject", {})], 420),
        ([("publish", automatic), ("unpublish", {})], 480),
    ]
    with database.begin() as session:
        for index, (reviews, delay) in enumerate(cases):
            # Different publication cutoffs in one report prevent treating the
            # report's end as every article's review-intervention cutoff.
            published = start + timedelta(hours=index + 1)
            article = add_article(session, published=True, published_at=published)
            article.discovered_at = published - timedelta(seconds=delay)
            session.add_all(
                ArticleReview(
                    article_id=article.id,
                    action=action,
                    revision=revision,
                    automation=evidence,
                    created_at=published,
                )
                for revision, (action, evidence) in enumerate(reviews, start=1)
            )
        session.flush()
        summary = publication_summary(session, start, NOW)
        median = summary.pop("p50_seconds")
        assert summary == {"published": 8, "automated": 2, "automatic_percent": 25.0}
        # TDigest estimates need not interpolate the two central observations.
        assert 240 <= median <= 300


def test_review_cutoffs_are_inclusive_and_specific_to_each_publication(database):
    start = NOW - timedelta(days=1)
    automatic = {"policy": "automatic"}
    with database.begin() as session:
        for index, reviews in enumerate(
            [
                [("publish", automatic, 0)],
                [("publish", automatic, 1)],
                [("publish", automatic, -1), ("approve", {}, 0)],
                [("publish", automatic, -1), ("approve", {}, 1)],
                [("publish", automatic, -1), ("approve", {}, -1)],
            ]
        ):
            published = start + timedelta(hours=index + 1)
            article = add_article(session, published=True, published_at=published)
            article.discovered_at = published - timedelta(seconds=60)
            session.add_all(
                ArticleReview(
                    article_id=article.id,
                    action=action,
                    revision=revision,
                    automation=evidence,
                    created_at=published + timedelta(microseconds=offset),
                )
                for revision, (action, evidence, offset) in enumerate(reviews, start=1)
            )
        session.flush()
        assert publication_summary(session, start, NOW) == {
            "published": 5,
            "automated": 2,
            "automatic_percent": 40.0,
            "p50_seconds": 60.0,
        }


def test_report_keeps_old_review_facts_and_excludes_unrelated_publications(database):
    start = (NOW - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    old = start - timedelta(days=90)
    automatic = {"policy": "automatic"}
    with database.begin() as session:
        for published, reviews in [
            (NOW, [("publish", automatic, old)]),
            # An old manual intervention still vetoes a later automatic publish;
            # repeated old manual facts must not duplicate the publication row.
            (
                NOW,
                [("approve", {}, old), ("reject", {}, old), ("publish", automatic, NOW)],
            ),
            (NOW, []),
            (start - timedelta(microseconds=1), [("publish", automatic, old)]),
            (NOW + timedelta(hours=1), [("approve", {}, old)]),
            (None, [("publish", automatic, old)]),
        ]:
            article = add_article(session, published=True, published_at=published)
            article.discovered_at = (published or NOW) - timedelta(seconds=120)
            session.add_all(
                ArticleReview(
                    article_id=article.id,
                    action=action,
                    revision=revision,
                    automation=evidence,
                    created_at=created_at,
                )
                for revision, (action, evidence, created_at) in enumerate(reviews, start=1)
            )
        session.flush()
        assert publication_summary(session, start, NOW) == {
            "published": 3,
            "automated": 1,
            "automatic_percent": 33.3,
            "p50_seconds": 120.0,
        }


def test_publication_boundaries_keep_invalid_durations_out_of_median_without_losing_counts(
    database,
):
    start = (NOW - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    with database.begin() as session:
        for published, delay in [
            (None, 0),
            (start - timedelta(microseconds=1), 900),
            (start, 0),
            (NOW, 120),
            (NOW, 480),
            (NOW, -60),
            (NOW + timedelta(hours=1), 900),
        ]:
            article = add_article(session, published=True, published_at=published)
            article.discovered_at = (published or NOW) - timedelta(seconds=delay)
        session.flush()
        assert publication_summary(session, start, NOW) == {
            "published": 4,
            "automated": 0,
            "automatic_percent": 0.0,
            "p50_seconds": 120.0,
        }
        empty = {
            "published": 0,
            "automated": 0,
            "automatic_percent": None,
            "p50_seconds": None,
        }
        with pytest.raises(ValueError, match="between 1 and 3660"):
            publication_summary(session, NOW + timedelta(days=1), NOW)
        assert (
            publication_summary(session, NOW + timedelta(days=1), NOW + timedelta(days=2)) == empty
        )


def test_publication_with_only_negative_durations_keeps_null_median(database):
    with database.begin() as session:
        article = add_article(session, published=True, published_at=NOW)
        article.discovered_at = NOW + timedelta(seconds=1)
        session.add(
            ArticleReview(
                article_id=article.id,
                action="publish",
                revision=1,
                automation={"policy": "automatic"},
                created_at=NOW,
            )
        )
        session.flush()
        assert publication_summary(session, NOW, NOW) == {
            "published": 1,
            "automated": 1,
            "automatic_percent": 100.0,
            "p50_seconds": None,
        }


def test_report_reloads_review_facts_and_publication_cutoff_after_editorial_changes(database):
    with database.begin() as session:
        published = NOW - timedelta(hours=1)
        article = add_article(session, published=True, published_at=published)
        article.discovered_at = published - timedelta(minutes=10)

        def report():
            session.flush()
            return publication_summary(session, NOW - timedelta(days=1), NOW)

        assert report()["automated"] == 0
        session.add(
            ArticleReview(
                article_id=article.id,
                action="publish",
                revision=1,
                automation={"policy": "automatic"},
                created_at=published,
            )
        )
        assert report()["automated"] == 1
        manual = ArticleReview(
            article_id=article.id,
            action="approve",
            revision=2,
            automation={},
            created_at=published + timedelta(microseconds=1),
        )
        session.add(manual)
        assert report()["automated"] == 1
        article.published_to_feed_at = manual.created_at
        changed = report()
        assert changed["published"] == 1
        assert changed["automated"] == 0
        assert changed["p50_seconds"] == pytest.approx(600.000001)


def test_publication_window_uses_utc_calendar_days_and_the_current_hour_bucket(database):
    start = NOW - timedelta(days=1)
    first_day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    with database.begin() as session:
        for published in (
            first_day - timedelta(microseconds=1),  # Previous calendar day.
            first_day,
            start - timedelta(microseconds=1),  # Still inside the first day.
            NOW + timedelta(microseconds=1),  # Inside the current hour cohort.
            NOW + timedelta(hours=1),  # Next hour is outside the published range.
        ):
            article = add_article(session, published=True, published_at=published)
            article.discovered_at = published - timedelta(seconds=60)
        session.flush()
        assert publication_summary(session, start, NOW) == {
            "published": 3,
            "automated": 0,
            "automatic_percent": 0.0,
            "p50_seconds": 60.0,
        }
