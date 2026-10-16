"""Reader-visible feed context selects grounded inventory without global or private fallback."""

import uuid
from datetime import timedelta
from unittest.mock import Mock

import pytest
from devfeed_core import partner_placements as placements
from devfeed_core.models import (
    Article,
    ArticleOrigin,
    ArticleTopic,
    PartnerCampaign,
    PartnerProduct,
    PartnerProductTopic,
    Source,
    Topic,
    UserTopic,
    utcnow,
)
from devfeed_core.partner_matching import save_article_match
from devfeed_http.partner_placements import PlacementQuery
from pydantic import ValidationError
from sqlalchemy import select
from test_partner_placement_api import make_client
from test_partner_placements_integration import catalog as catalog
from test_partner_placements_integration import deliver, query


@pytest.mark.parametrize("size", [0, 1, 24])
def test_feed_context_accepts_bounded_uuid_article_ids(size):
    ids = [uuid.uuid4() for _ in range(size)]
    assert PlacementQuery(surface="feed", article_ids=ids).article_ids == ids


@pytest.mark.parametrize(
    "payload",
    [
        {"surface": "feed", "article_ids": [str(uuid.uuid4())] * 25},
        {"surface": "feed", "article_ids": ["not-an-article"]},
        {"surface": "feed", "article_ids": None},
        {"surface": "feed", "article_ids": "not-a-list"},
        {"surface": "feed", "article_ids": [], "user_id": str(uuid.uuid4())},
        {"surface": "feed", "article_ids": [], "topics": ["Kubernetes"]},
        {"surface": "topic", "topic_id": str(uuid.uuid4()), "article_ids": [str(uuid.uuid4())]},
        {
            "surface": "article",
            "article_id": str(uuid.uuid4()),
            "article_ids": [str(uuid.uuid4())],
        },
    ],
)
def test_feed_context_rejects_unbounded_ambiguous_or_untrusted_shape(payload):
    with pytest.raises(ValidationError):
        PlacementQuery.model_validate(payload)


def test_repeated_visible_topics_use_strongest_context_without_frequency_inflation():
    strongest, supporting = uuid.uuid4(), uuid.uuid4()
    session = Mock()
    session.execute.return_value.all.return_value = [
        (strongest, "supporting", 0.8),
        (strongest, "primary", 0.9),
        (strongest, "primary", 0.6),
        (supporting, "supporting", 1.0),
    ]
    assert placements.feed_topic_weights(session, [uuid.uuid4()]) == {
        strongest: 90,
        supporting: 80,
    }
    assert placements.feed_topic_weights(session, []) == {}
    assert session.execute.call_count == 1


@pytest.mark.parametrize("private", [False, True])
def test_http_feed_context_forwards_validated_ids_and_rejects_oversized_lists(monkeypatch, private):
    state = make_client(monkeypatch, private=private)
    ids = [str(uuid.uuid4()) for _ in range(24)]
    response = state.client.post(
        state.path, headers=state.headers, json={"surface": "feed", "article_ids": ids}
    )
    assert response.status_code == 200
    assert [str(value) for value in state.deliver.call_args.args[1].article_ids] == ids
    state.deliver.reset_mock()
    response = state.client.post(
        state.path,
        headers=state.headers,
        json={"surface": "feed", "article_ids": [*ids, str(uuid.uuid4())]},
    )
    assert response.status_code == 422
    state.deliver.assert_not_called()


@pytest.fixture
def feed_catalog(database, catalog):
    with database.begin() as session:
        session.add(
            ArticleTopic(
                article_id=catalog["article"],
                topic_id=catalog["topic"],
                role="primary",
                relevance=1.0,
                evidence="Kubernetes is the practical focus of this article.",
            )
        )
    return catalog


def feed_query(catalog, **kwargs):
    return PlacementQuery(surface="feed", article_ids=[catalog["article"]], **kwargs)


@pytest.mark.integration
def test_public_main_grid_context_delivers_grounded_products_and_no_reader_details(
    client, feed_catalog
):
    response = client.post(
        "/v1/partner-placements/query", json=feed_query(feed_catalog).model_dump(mode="json")
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    items = response.json()["items"]
    assert [(row["name"], row["sponsored"]) for row in items] == [
        ("free_product", False),
        ("paid_product", True),
    ]
    assert all(row["reason"] == "Verified support for Kubernetes." for row in items)
    assert all(row["logo_url"] is None for row in items)
    assert all("frequency_cap" not in row and row["receipt"] for row in items)
    assert str(feed_catalog["user"]) not in response.text
    assert str(feed_catalog["article"]) not in response.text


@pytest.mark.integration
def test_supporting_topics_and_general_articles_also_supply_feed_context(database, feed_catalog):
    with database.begin() as session:
        session.get(Article, feed_catalog["article"]).content_type = "article"
        session.get(
            ArticleTopic, (feed_catalog["article"], feed_catalog["topic"])
        ).role = "supporting"
    assert len(deliver(database, feed_query(feed_catalog))) == 2
    # Preview suggestions retain the stricter tutorial/task contract.
    assert deliver(database, query(feed_catalog)) == []


@pytest.mark.integration
@pytest.mark.parametrize(
    "change",
    [
        "withdrawn",
        "pending",
        "private_source",
        "missing_source",
        "inactive_topic",
        "incidental",
        "comparison",
    ],
)
def test_unavailable_articles_and_unapproved_topic_roles_provide_no_context(
    database, feed_catalog, change
):
    with database.begin() as session:
        article = session.get(Article, feed_catalog["article"])
        if change == "withdrawn":
            article.publication_status = "unpublished"
        elif change == "pending":
            article.publication_status = "unpublished"
            article.review_status = "pending"
        elif change == "private_source":
            session.get(Source, feed_catalog["source"]).approval_status = "pending"
        elif change == "missing_source":
            for origin in session.scalars(
                select(ArticleOrigin).where(ArticleOrigin.article_id == article.id)
            ):
                session.delete(origin)
        elif change == "inactive_topic":
            session.get(Topic, feed_catalog["topic"]).status = "proposed"
        else:
            session.get(ArticleTopic, (article.id, feed_catalog["topic"])).role = change
    assert deliver(database, feed_query(feed_catalog)) == []


@pytest.mark.integration
def test_search_context_never_introduces_unrelated_personal_or_global_inventory(
    client, database, feed_catalog
):
    with database.begin() as session:
        session.add(UserTopic(user_id=feed_catalog["user"], topic_id=feed_catalog["other_topic"]))
    # Both anonymous and authenticated search stay tied to the displayed Kubernetes content.
    for kwargs in ({}, {"user_id": feed_catalog["user"]}):
        result = deliver(database, feed_query(feed_catalog), **kwargs)
        assert {row["product_id"] for row in result} == {
            feed_catalog["free_product"],
            feed_catalog["paid_product"],
        }
    assert deliver(database, PlacementQuery(surface="feed")) == []
    assert deliver(database, PlacementQuery(surface="feed", article_ids=[uuid.uuid4()])) == []
    assert (
        deliver(
            database,
            PlacementQuery(surface="feed", article_ids=[uuid.uuid4()]),
            user_id=feed_catalog["user"],
        )
        == []
    )
    response = client.post(
        "/v1/partner-placements/query",
        json={"surface": "feed", "article_ids": [str(feed_catalog["article"])]},
    )
    assert response.status_code == 200
    assert "unrelated" not in response.text


@pytest.mark.integration
@pytest.mark.parametrize("change", ["product_revision", "product_updated_at", "topic_updated_at"])
def test_feed_context_requires_current_prepared_product_and_topic_snapshots(
    database, feed_catalog, change
):
    with database.begin() as session:
        if change.startswith("product"):
            for name in ("free_product", "paid_product"):
                product = session.get(PartnerProduct, feed_catalog[name])
                if change == "product_revision":
                    product.revision += 1
                else:
                    product.updated_at += timedelta(seconds=1)
        else:
            session.get(Topic, feed_catalog["topic"]).updated_at += timedelta(seconds=1)
    assert deliver(database, feed_query(feed_catalog)) == []


@pytest.mark.integration
def test_unchanged_feed_context_outlives_preparation_age(database, feed_catalog):
    with database.begin() as session:
        for match in session.scalars(select(PartnerProductTopic)):
            match.updated_at = utcnow() - timedelta(hours=1)
    assert len(deliver(database, feed_query(feed_catalog))) == 2


@pytest.mark.integration
@pytest.mark.parametrize("source", ["article", "link", "topic"])
def test_busy_context_edits_skip_issuance_until_the_visible_snapshot_is_stable(
    database, feed_catalog, source
):
    with database.begin() as editing:
        if source == "article":
            statement = select(Article.id).where(Article.id == feed_catalog["article"])
        elif source == "link":
            statement = select(ArticleTopic.topic_id).where(
                ArticleTopic.article_id == feed_catalog["article"]
            )
        else:
            statement = select(Topic.id).where(Topic.id == feed_catalog["topic"])
        assert editing.scalar(statement.with_for_update()) is not None
        assert deliver(database, feed_query(feed_catalog)) == []
    assert len(deliver(database, feed_query(feed_catalog))) == 2


@pytest.mark.integration
def test_context_relevance_ranks_strongest_match_and_equal_matches_rotate(database, feed_catalog):
    with database.begin() as session:
        session.get(PartnerCampaign, feed_catalog["campaign"]).status = "paused"
        session.get(
            PartnerProductTopic, (feed_catalog["free_product"], feed_catalog["topic"])
        ).score = 70
        session.get(
            PartnerProductTopic, (feed_catalog["paid_product"], feed_catalog["topic"])
        ).score = 100
    assert [row["product_id"] for row in deliver(database, feed_query(feed_catalog))] == [
        feed_catalog["paid_product"]
    ]
    with database.begin() as session:
        session.get(
            PartnerProductTopic, (feed_catalog["free_product"], feed_catalog["topic"])
        ).score = 100
    first = deliver(database, feed_query(feed_catalog))
    second = deliver(database, feed_query(feed_catalog))
    assert len(first) == len(second) == 1
    assert [first[0]["product_id"], second[0]["product_id"]] == [
        feed_catalog["free_product"],
        feed_catalog["paid_product"],
    ]
    excluded = deliver(
        database, feed_query(feed_catalog, exclude_product_ids=[first[0]["product_id"]])
    )
    assert len(excluded) == 1 and excluded[0]["product_id"] != first[0]["product_id"]


@pytest.mark.integration
def test_strongest_approved_visible_topic_wins_over_weaker_context(database, feed_catalog):
    with database.begin() as session:
        session.get(PartnerCampaign, feed_catalog["campaign"]).status = "paused"
        session.get(ArticleTopic, (feed_catalog["article"], feed_catalog["topic"])).relevance = 0.6
        session.add(
            ArticleTopic(
                article_id=feed_catalog["article"],
                topic_id=feed_catalog["other_topic"],
                role="supporting",
                relevance=1.0,
                evidence="The article also discusses the Figma workflow.",
            )
        )
        session.add(UserTopic(user_id=feed_catalog["user"], topic_id=feed_catalog["topic"]))
    result = deliver(database, feed_query(feed_catalog), user_id=feed_catalog["user"])
    assert [row["product_id"] for row in result] == [feed_catalog["unrelated"]]
    with database.begin() as session:
        session.get(ArticleTopic, (feed_catalog["article"], feed_catalog["topic"])).relevance = 1.0
    result = deliver(database, feed_query(feed_catalog), user_id=feed_catalog["user"])
    assert len(result) == 1 and result[0]["product_id"] in {
        feed_catalog["free_product"],
        feed_catalog["paid_product"],
    }


@pytest.mark.integration
def test_context_uses_feed_campaign_policy_and_shared_daily_reservations(database, feed_catalog):
    with database.begin() as session:
        session.get(PartnerCampaign, feed_catalog["campaign"]).daily_impression_limit = 1
    assert sum(row["sponsored"] for row in deliver(database, feed_query(feed_catalog))) == 1
    assert not any(row["sponsored"] for row in deliver(database, feed_query(feed_catalog)))
    with database.begin() as session:
        campaign = session.get(PartnerCampaign, feed_catalog["campaign"])
        campaign.surfaces = ["article"]
    assert not any(row["sponsored"] for row in deliver(database, feed_query(feed_catalog)))


@pytest.mark.integration
def test_feed_topic_support_does_not_override_authoritative_article_preview_negative(
    database, feed_catalog
):
    with database.begin() as session:
        product = session.get(PartnerProduct, feed_catalog["paid_product"])
        article = session.get(Article, feed_catalog["article"])
        save_article_match(
            session, product, article, False, 0, "Task is incompatible.", origin="evaluation"
        )
    assert not any(row["sponsored"] for row in deliver(database, query(feed_catalog)))
    feed = deliver(database, feed_query(feed_catalog))
    assert any(row["sponsored"] for row in feed)
    assert all("task" not in row["reason"].lower() for row in feed)
