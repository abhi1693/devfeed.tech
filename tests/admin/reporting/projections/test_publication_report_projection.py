"""Saved publication reports use narrow review identities with exact intervention cutoffs."""

from datetime import timedelta

import pytest
from devfeed_admin_api.automation import _publication_automation_query, publication_automation
from devfeed_core.models import ArticleReview, utcnow
from sqlalchemy import text

from tests.admin.reporting.projections.test_pipeline_projection_safety import add_article
from tests.support.source_readiness import NOW

pytestmark = pytest.mark.integration


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
        assert publication_automation(session, start, NOW) == {
            "published_in_window": 8,
            "published_without_intervention": 2,
            "automatic_publication_percent": 25.0,
            "median_ingestion_to_publication_seconds": 270.0,
        }


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
        assert publication_automation(session, start, NOW) == {
            "published_in_window": 5,
            "published_without_intervention": 2,
            "automatic_publication_percent": 40.0,
            "median_ingestion_to_publication_seconds": 60.0,
        }


def test_report_keeps_old_review_facts_and_excludes_unrelated_publications(database):
    start = NOW - timedelta(days=1)
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
            (NOW + timedelta(microseconds=1), [("approve", {}, old)]),
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
        assert publication_automation(session, start, NOW) == {
            "published_in_window": 3,
            "published_without_intervention": 1,
            "automatic_publication_percent": 33.3,
            "median_ingestion_to_publication_seconds": 120.0,
        }


def test_publication_boundaries_keep_invalid_durations_out_of_median_without_losing_counts(
    database,
):
    start = NOW - timedelta(days=1)
    with database.begin() as session:
        for published, delay in [
            (None, 0),
            (start - timedelta(microseconds=1), 900),
            (start, 0),
            (NOW, 120),
            (NOW, 480),
            (NOW, -60),
            (NOW + timedelta(microseconds=1), 900),
        ]:
            article = add_article(session, published=True, published_at=published)
            article.discovered_at = (published or NOW) - timedelta(seconds=delay)
        session.flush()
        assert publication_automation(session, start, NOW) == {
            "published_in_window": 4,
            "published_without_intervention": 0,
            "automatic_publication_percent": 0.0,
            "median_ingestion_to_publication_seconds": 120.0,
        }
        empty = {
            "published_in_window": 0,
            "published_without_intervention": 0,
            "automatic_publication_percent": None,
            "median_ingestion_to_publication_seconds": None,
        }
        assert publication_automation(session, NOW + timedelta(days=1), NOW) == empty
        assert (
            publication_automation(session, NOW + timedelta(days=1), NOW + timedelta(days=2))
            == empty
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
        assert publication_automation(session, NOW, NOW) == {
            "published_in_window": 1,
            "published_without_intervention": 1,
            "automatic_publication_percent": 100.0,
            "median_ingestion_to_publication_seconds": None,
        }


def test_report_reloads_review_facts_and_publication_cutoff_after_editorial_changes(database):
    with database.begin() as session:
        published = NOW - timedelta(hours=1)
        article = add_article(session, published=True, published_at=published)
        article.discovered_at = published - timedelta(minutes=10)

        def report():
            session.flush()
            return publication_automation(session, NOW - timedelta(days=1), NOW)

        assert report()["published_without_intervention"] == 0
        session.add(
            ArticleReview(
                article_id=article.id,
                action="publish",
                revision=1,
                automation={"policy": "automatic"},
                created_at=published,
            )
        )
        assert report()["published_without_intervention"] == 1
        manual = ArticleReview(
            article_id=article.id,
            action="approve",
            revision=2,
            automation={},
            created_at=published + timedelta(microseconds=1),
        )
        session.add(manual)
        assert report()["published_without_intervention"] == 1
        article.published_to_feed_at = manual.created_at
        changed = report()
        assert changed["published_in_window"] == 1
        assert changed["published_without_intervention"] == 0
        assert changed["median_ingestion_to_publication_seconds"] == pytest.approx(600.000001)


def test_large_review_evidence_uses_partial_indexes_without_changing_manual_cutoff(database):
    now = utcnow()
    evidence = {"policy": "automatic", "evidence": "Unicode extraction evidence 設計 " * 3000}
    with database.begin() as session:
        for index in range(2):
            article = add_article(session, published=True, published_at=now)
            article.discovered_at = now - timedelta(hours=1)
            session.add_all(
                [
                    ArticleReview(
                        article_id=article.id,
                        action="publish",
                        revision=1,
                        automation=evidence,
                        created_at=now,
                    ),
                    ArticleReview(
                        article_id=article.id,
                        action="approve",
                        revision=2,
                        automation={},
                        created_at=now + timedelta(microseconds=1) if index == 0 else now,
                    ),
                ]
            )
    with database() as session:
        result = publication_automation(session, now - timedelta(days=1), now)
        assert result == {
            "published_in_window": 2,
            "published_without_intervention": 1,
            "automatic_publication_percent": 50.0,
            "median_ingestion_to_publication_seconds": 3600.0,
        }
        # Small fixtures normally favor a table scan. This verifies that both
        # exact application predicates can use narrow indexes at large scale.
        session.execute(text("SET LOCAL enable_seqscan = off"))
        compiled = _publication_automation_query(now - timedelta(days=1), now).compile(
            session.get_bind()
        )
        plan = "\n".join(
            row[0]
            for row in session.connection().exec_driver_sql(
                "EXPLAIN " + str(compiled), compiled.params
            )
        )
    assert "ix_article_reviews_manual_publication" in plan
    assert "ix_article_reviews_automatic_publication" in plan
    assert "Seq Scan on article_reviews" not in plan
