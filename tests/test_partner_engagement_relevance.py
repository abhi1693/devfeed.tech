"""Engagement uses current tutorial tasks instead of borrowing broad feed relevance."""

import uuid
from datetime import timedelta

import pytest
from devfeed_core.models import (
    Article,
    ArticleOrigin,
    ArticleTopic,
    PartnerAccount,
    PartnerArticleMatch,
    PartnerCampaign,
    PartnerPlacementDelivery,
    PartnerProduct,
    PartnerProductTopic,
    Source,
    utcnow,
)
from devfeed_core.partner_matching import article_match, prepare_topics, save_article_match
from devfeed_core.partner_placements import deliver_placements
from devfeed_core.partner_promotions import ensure_automatic_assets
from devfeed_http.partner_placements import PlacementQuery
from sqlalchemy import delete, event, func, select
from test_partner_feed_placements import feed_catalog as feed_catalog
from test_partner_native_formats import feed_request, native_account
from test_partner_placement_query_budgets import counted_statements
from test_partner_placement_rotation import seed_rotation
from test_partner_placements_integration import KEY, deliver
from test_partner_placements_integration import catalog as catalog

pytestmark = pytest.mark.integration


def support_kubernetes(session, product):
    product.technologies = ["Kubernetes"]
    product.evidence = [
        {
            "url": product.product_url,
            "quote": "Inspect Kubernetes metrics for active clusters.",
            "capability": "Inspect Kubernetes metrics.",
        }
    ]
    session.flush()
    assert prepare_topics(session, product, utcnow())


@pytest.mark.parametrize("decision", ["missing", "negative"])
def test_topic_overlap_cannot_replace_an_engagement_task_match(database, feed_catalog, decision):
    native_account(database, feed_catalog, ["feed", "engagement"])
    with database.begin() as session:
        product = session.get(PartnerProduct, feed_catalog["paid_product"])
        article = session.get(Article, feed_catalog["article"])
        if decision == "negative":
            assert save_article_match(
                session, product, article, False, 0, "Unrelated task.", origin="evaluation"
            )
        else:
            session.execute(
                delete(PartnerArticleMatch).where(PartnerArticleMatch.product_id == product.id)
            )
    assert deliver(database, feed_request(feed_catalog, ["engagement"])) == []
    for formats in (["feed"], ["feed", "engagement"]):
        rows = deliver(database, feed_request(feed_catalog, formats))
        assert len(rows) == 2
        assert {row["ad_format"] for row in rows} == {"feed"}
        assert next(row for row in rows if row["sponsored"])["product_id"] == product.id


@pytest.mark.parametrize(
    "change",
    [
        "product_revision",
        "product_updated_at",
        "fingerprint",
        "editorial_revision",
        "score",
        "private_source",
        "unpublished",
        "pending_review",
        "non_tutorial",
    ],
)
def test_engagement_requires_current_public_tutorial_task_inputs(database, feed_catalog, change):
    native_account(database, feed_catalog, ["engagement"])
    payload = feed_request(feed_catalog, ["engagement"])
    assert len(deliver(database, payload)) == 1
    with database.begin() as session:
        match = session.get(
            PartnerArticleMatch, (feed_catalog["paid_product"], feed_catalog["article"])
        )
        if change == "product_revision":
            session.get(PartnerProduct, feed_catalog["paid_product"]).revision += 1
        elif change == "product_updated_at":
            session.get(PartnerProduct, feed_catalog["paid_product"]).updated_at += timedelta(
                seconds=1
            )
        elif change == "fingerprint":
            match.article_fingerprint = "0" * 64
        elif change == "editorial_revision":
            match.editorial_revision += 1
        elif change == "score":
            match.score = 74
        elif change == "private_source":
            session.get(Source, feed_catalog["source"]).approval_status = "pending"
        else:
            article = session.get(Article, feed_catalog["article"])
            if change == "unpublished":
                article.publication_status = "unpublished"
            elif change == "pending_review":
                article.review_status = "pending"
                article.publication_status = "unpublished"
            else:
                article.content_type = "news"
    assert deliver(database, payload) == []


def test_mixed_feed_can_use_one_current_tutorial_without_topic_labels(database, feed_catalog):
    native_account(database, feed_catalog, ["engagement"])
    with database.begin() as session:
        session.execute(
            delete(ArticleTopic).where(ArticleTopic.article_id == feed_catalog["article"])
        )
        news = Article(
            canonical_url="https://publisher.example/news",
            url_hash=uuid.uuid4().hex,
            title="Kubernetes news",
            summary="News about Kubernetes operations, monitoring and metrics.",
            content_type="news",
            review_status="approved",
            publication_status="published",
        )
        session.add(news)
        session.flush()
        session.add(
            ArticleOrigin(
                article_id=news.id,
                source_id=feed_catalog["source"],
                entry_key="news",
                original_url=news.canonical_url,
            )
        )
        news_id = news.id
    payload = PlacementQuery(
        surface="feed",
        article_ids=[news_id, uuid.uuid4(), feed_catalog["article"]],
        ad_formats=["engagement"],
    )
    rows = deliver(database, payload)
    assert len(rows) == 1
    assert rows[0]["product_id"] == feed_catalog["paid_product"]
    assert "tutorial's task" in rows[0]["reason"]
    assert deliver(database, PlacementQuery(surface="feed", ad_formats=["engagement"])) == []


def test_topic_engagement_requires_direct_support_instead_of_relationship_adjacency(
    database, feed_catalog
):
    native_account(database, feed_catalog, ["feed", "engagement"])
    with database.begin() as session:
        edge = session.get(
            PartnerProductTopic, (feed_catalog["paid_product"], feed_catalog["topic"])
        )
        edge.score = 65
        edge.reason = "Related technology."
    payload = dict(surface="topic", topic_id=feed_catalog["topic"])
    assert deliver(database, PlacementQuery(**payload, ad_formats=["engagement"])) == []
    assert any(
        row["product_id"] == feed_catalog["paid_product"]
        for row in deliver(database, PlacementQuery(**payload, ad_formats=["feed"]))
    )
    with database.begin() as session:
        session.get(
            PartnerProductTopic, (feed_catalog["paid_product"], feed_catalog["topic"])
        ).score = 100
    assert len(deliver(database, PlacementQuery(**payload, ad_formats=["engagement"]))) == 1


def test_campaign_priority_cannot_outrank_a_stronger_engagement_task(database, feed_catalog):
    native_account(database, feed_catalog, ["engagement"])
    with database.begin() as session:
        weaker = session.get(PartnerProduct, feed_catalog["unrelated"])
        article = session.get(Article, feed_catalog["article"])
        support_kubernetes(session, weaker)
        relevant, score, reason = article_match(weaker, article)
        assert relevant and score == 83
        assert save_article_match(session, weaker, article, relevant, score, reason)
        session.add(
            PartnerCampaign(
                account_id=feed_catalog["paid"],
                name="High priority weaker task",
                ad_format="engagement",
                targeting="selected",
                product_ids=[str(weaker.id)],
                status="active",
                priority=100,
                starts_at=utcnow() - timedelta(days=1),
                ends_at=utcnow() + timedelta(days=1),
            )
        )
        session.flush()
        ensure_automatic_assets(session, feed_catalog["paid"])
    rows = deliver(database, feed_request(feed_catalog, ["engagement"]))
    assert len(rows) == 1 and rows[0]["product_id"] == feed_catalog["paid_product"]


def test_task_intent_outranks_topic_score_in_a_mixed_format_request(database, feed_catalog):
    native_account(database, feed_catalog, ["feed", "homepage_sponsorship", "engagement"])
    with database.begin() as session:
        weaker = session.get(PartnerProduct, feed_catalog["unrelated"])
        support_kubernetes(session, weaker)
    rows = deliver(
        database,
        feed_request(feed_catalog, ["feed", "homepage_sponsorship", "engagement"], fallback=True),
    )
    assert len(rows) == 2 and len({row["product_id"] for row in rows}) == 2
    engagement = next(row for row in rows if row["ad_format"] == "engagement")
    assert engagement["product_id"] == feed_catalog["paid_product"]
    assert "tutorial's task" in engagement["reason"]
    topic_placement = next(row for row in rows if row["ad_format"] != "engagement")
    assert topic_placement["ad_format"] in {"feed", "homepage_sponsorship"}
    assert topic_placement["reason"] == "Verified support for Kubernetes."


@pytest.mark.parametrize("surface", ["article", "feed"])
def test_negative_committed_after_candidate_ranking_prevents_task_receipt(
    database, feed_catalog, surface
):
    native_account(database, feed_catalog, ["engagement"])
    payload = (
        PlacementQuery(
            surface="article", article_id=feed_catalog["article"], ad_formats=["engagement"]
        )
        if surface == "article"
        else feed_request(feed_catalog, ["engagement"])
    )
    published = False

    def publish_negative_before_product_lock(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ):
        nonlocal published
        if published or "FROM partner_products" not in statement or "FOR UPDATE" not in statement:
            return
        published = True
        with database.begin() as evaluator:
            product = evaluator.get(PartnerProduct, feed_catalog["paid_product"])
            article = evaluator.get(Article, feed_catalog["article"])
            assert save_article_match(
                evaluator,
                product,
                article,
                False,
                0,
                "Not this tutorial's task.",
                origin="evaluation",
            )

    with database.begin() as session:
        connection = session.connection()
        event.listen(connection, "before_cursor_execute", publish_negative_before_product_lock)
        try:
            assert deliver_placements(session, payload, KEY) == []
        finally:
            event.remove(connection, "before_cursor_execute", publish_negative_before_product_lock)
        assert published
        assert session.scalar(select(func.count()).select_from(PartnerPlacementDelivery)) == 0


def test_audience_rejected_engagement_inventory_has_bounded_reader_sql(database):
    inventory = seed_rotation(database, count=105)
    with database.begin() as session:
        account = session.get(PartnerAccount, inventory["account"])
        account.tier = "platinum"
        account.ad_formats = ["engagement"]
        account.audience = {"experience_levels": ["senior"]}
        ensure_automatic_assets(session, account.id, now=inventory["now"])
    with database.begin() as session:
        with counted_statements(session) as statements:
            rows = deliver_placements(
                session,
                PlacementQuery(
                    surface="feed",
                    article_ids=[inventory["article"]],
                    ad_formats=["engagement"],
                ),
                KEY,
                now=inventory["now"],
            )
        assert rows == []
        assert len(statements) <= 8, f"Executed {len(statements)} SQL statements"
