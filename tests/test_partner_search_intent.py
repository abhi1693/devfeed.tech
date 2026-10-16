"""Search intent and every public result kind supply bounded, verified context."""

import uuid

import pytest
from devfeed_core import partner_placements as placements
from devfeed_core.models import (
    Article,
    ArticleOrigin,
    ArticleTag,
    ArticleTopic,
    PartnerAccount,
    PartnerAsset,
    PartnerCampaign,
    PartnerListing,
    PartnerPlacementDelivery,
    PartnerProduct,
    PartnerProductPromotion,
    PartnerProductTopic,
    Source,
    Tag,
    Topic,
)
from devfeed_core.partner_matching import prepare_topics
from devfeed_core.partner_promotions import ensure_automatic_assets
from devfeed_http.partner_placements import PlacementQuery
from sqlalchemy import delete, event, func, select, text, update
from sqlalchemy.exc import OperationalError
from test_partner_feed_placements import feed_catalog as feed_catalog
from test_partner_placement_query_budgets import counted_statements
from test_partner_placement_rotation import seed_rotation
from test_partner_placements_integration import KEY, deliver
from test_partner_placements_integration import catalog as catalog
from test_partner_search_placements import search_catalog as search_catalog

pytestmark = pytest.mark.integration


def result_query(catalog, query="Kubernetes", *, kind="articles", identifiers=None):
    return PlacementQuery(
        surface="search",
        ad_formats=["engagement"],
        search={
            "query": query,
            "results": {kind: identifiers if identifiers is not None else [catalog["article"]]},
        },
    )


@pytest.mark.parametrize("kind", ["articles", "topics", "sources", "tags"])
@pytest.mark.parametrize("query", ["Kubernetes", "monitoring metrics"])
def test_one_public_result_of_each_kind_can_supply_capability_grounded_search_context(
    database, search_catalog, kind, query
):
    catalog = search_catalog
    identifier = catalog[
        {"articles": "article", "topics": "topic", "sources": "source"}.get(kind, "article")
    ]
    if kind == "tags":
        identifier = uuid.uuid4()
        with database.begin() as session:
            session.add(Tag(id=identifier, name="Cluster metrics", slug="cluster-metrics"))
            session.flush()
            session.add(ArticleTag(article_id=catalog["article"], tag_id=identifier))
    query = result_query(catalog, query, kind=kind, identifiers=[identifier])
    anonymous = deliver(database, query)
    signed_in = deliver(database, query, user_id=catalog["user"])
    for rows in (anonymous, signed_in):
        assert len(rows) == 1 and rows[0]["product_id"] == catalog["paid_product"]
        assert rows[0]["ad_format"] == "engagement"


@pytest.mark.parametrize(
    "query",
    ["deploy Kubernetes", "Figma metrics", "AI developer tools", "Go metrics", "AI metrics"],
)
def test_result_topic_overlap_cannot_replace_verified_search_task_intent(
    database, search_catalog, query
):
    assert deliver(database, result_query(search_catalog, query)) == []


@pytest.mark.parametrize("change", ["implementation_only", "unsupported_task", "split_evidence"])
def test_search_intent_requires_technology_and_task_in_one_grounded_capability_quote(
    database, search_catalog, change
):
    catalog = search_catalog
    with database.begin() as session:
        for identifier in (catalog["paid_product"], catalog["free_product"]):
            product = session.get(PartnerProduct, identifier)
            if change == "implementation_only":
                product.evidence = [
                    {
                        "quote": "Our prompt testing site runs on Kubernetes infrastructure.",
                        "capability": "Test prompts and compare model responses",
                    }
                ]
            elif change == "unsupported_task":
                product.evidence = [
                    {
                        "quote": "Monitor Kubernetes clusters and inspect metrics.",
                        "capability": "Deploy Kubernetes clusters with manifests",
                    }
                ]
            else:
                product.evidence = [
                    {"quote": "Monitor Kubernetes clusters.", "capability": "Inspect metrics"},
                    {"quote": "Inspect metrics.", "capability": "Monitor Kubernetes clusters"},
                ]
            session.flush()
            assert prepare_topics(session, product, product.verified_at)
        assert (
            session.get(PartnerProductTopic, (catalog["paid_product"], catalog["topic"])).score
            == 100
        )
    text = "Kubernetes" if change == "implementation_only" else "monitor metrics"
    assert deliver(database, result_query(catalog, text)) == []


def test_supported_topic_aliases_do_not_require_the_canonical_topic_spelling(
    database, search_catalog
):
    with database.begin() as session:
        topic = session.get(Topic, search_catalog["topic"])
        topic.aliases = ["k8s"]
        session.flush()
        for identifier in (search_catalog["paid_product"], search_catalog["free_product"]):
            product = session.get(PartnerProduct, identifier)
            assert prepare_topics(session, product, product.verified_at)
    assert len(deliver(database, result_query(search_catalog, "k8s"))) == 1


@pytest.mark.parametrize(
    "query, capability, unrelated",
    [
        ("Run SQL queries", "Run SQL queries for Kubernetes clusters", "Run SQL backups"),
        ("libraries policies", "Review Kubernetes libraries and policies", "libraries alerts"),
        ("prompt-testing", "Prompt testing for Kubernetes clusters", "prompt-deployment"),
    ],
)
def test_search_and_evidence_share_inflection_and_separator_normalization(
    database, search_catalog, query, capability, unrelated
):
    with database.begin() as session:
        for identifier in (search_catalog["paid_product"], search_catalog["free_product"]):
            product = session.get(PartnerProduct, identifier)
            product.evidence = [{"capability": capability, "quote": capability + "."}]
            session.flush()
            assert prepare_topics(session, product, product.verified_at)
    assert len(deliver(database, result_query(search_catalog, query))) == 1
    assert deliver(database, result_query(search_catalog, unrelated)) == []


@pytest.mark.parametrize("kind", ["articles", "topics", "sources", "tags"])
def test_nonpublic_or_nonexistent_directory_results_supply_no_search_context(
    database, search_catalog, kind
):
    catalog = search_catalog
    with database.begin() as session:
        if kind == "articles":
            identifier = catalog["article"]
            session.get(Article, identifier).publication_status = "unpublished"
        elif kind == "topics":
            identifier = catalog["topic"]
            session.get(Topic, identifier).status = "rejected"
        elif kind == "sources":
            identifier = catalog["source"]
            session.get(Source, identifier).enabled = False
        else:
            identifier = uuid.uuid4()
    assert deliver(database, result_query(catalog, kind=kind, identifiers=[identifier])) == []


@pytest.mark.parametrize("kind", ["articles", "sources", "tags"])
def test_later_context_is_not_discarded_behind_unavailable_earlier_products(
    database, search_catalog, kind
):
    catalog = search_catalog
    identifiers = []
    with database.begin() as session:
        tag = Tag(name="Cloud", slug="cloud")
        session.add(tag)
        session.flush()
        tag_id = tag.id
        session.execute(delete(ArticleOrigin).where(ArticleOrigin.article_id == catalog["article"]))
        policy = session.get(PartnerProductPromotion, (catalog["paid"], catalog["unrelated"]))
        if policy is None:
            policy = PartnerProductPromotion(
                account_id=catalog["paid"], product_id=catalog["unrelated"]
            )
            session.add(policy)
        policy.excluded = True
        for index in range(24):
            article = Article(
                id=uuid.UUID(int=index + 1),
                canonical_url=f"https://publisher.example/figma-{index}",
                url_hash=uuid.uuid4().hex,
                title=f"Figma tutorial {index}",
                summary="A public tutorial about Figma interfaces and design tools.",
                content_type="tutorial",
                review_status="approved",
                publication_status="published",
            )
            session.add(article)
            session.flush()
            identifiers.append(article.id)
            session.add_all(
                [
                    ArticleOrigin(
                        article_id=article.id,
                        source_id=catalog["source"],
                        entry_key=f"figma-{index}",
                        original_url=article.canonical_url,
                    ),
                    ArticleTopic(
                        article_id=article.id,
                        topic_id=catalog["other_topic"],
                        role="primary",
                        relevance=1.0,
                        evidence="Figma is the topic.",
                    ),
                    ArticleTag(article_id=article.id, tag_id=tag_id),
                ]
            )
        original = session.get(Article, catalog["article"])
        session.add_all(
            [
                ArticleOrigin(
                    article_id=original.id,
                    source_id=catalog["source"],
                    entry_key="tutorial",
                    original_url=original.canonical_url,
                ),
                ArticleTag(article_id=original.id, tag_id=tag_id),
            ]
        )
    query = result_query(
        catalog,
        "monitor metrics",
        kind=kind,
        identifiers=(
            [*identifiers, catalog["article"]]
            if kind == "articles"
            else [catalog["source"]]
            if kind == "sources"
            else [tag_id]
        ),
    )
    rows = deliver(database, query)
    assert len(rows) == 1 and rows[0]["product_id"] == catalog["paid_product"]


@pytest.mark.parametrize("change", ["topic_edge", "query_capability", "source"])
def test_search_intent_and_public_context_are_rechecked_before_issuance(
    database, search_catalog, change
):
    catalog = search_catalog
    edited = False

    def invalidate(_connection, _cursor, statement, _parameters, _context, _executemany):
        nonlocal edited
        if edited or "FROM partner_products" not in statement or "FOR UPDATE" not in statement:
            return
        edited = True
        with database.begin() as editor:
            if change == "topic_edge":
                editor.execute(delete(PartnerProductTopic))
            elif change == "source":
                editor.get(Source, catalog["source"]).approval_status = "pending"
            else:
                editor.get(PartnerProduct, catalog["paid_product"]).evidence = []

    with database.begin() as reader:
        event.listen(reader.connection(), "before_cursor_execute", invalidate)
        try:
            rows = placements.deliver_placements(reader, result_query(catalog), KEY)
        finally:
            event.remove(reader.connection(), "before_cursor_execute", invalidate)
        assert edited
        assert rows == []


def test_search_keeps_its_public_source_witness_current_through_issuance(database, search_catalog):
    checked = False

    def edit_source(_connection, _cursor, statement, _parameters, _context, _executemany):
        nonlocal checked
        if checked or "INSERT INTO partner_placement_deliveries" not in statement:
            return
        checked = True
        with (
            pytest.raises(OperationalError, match="lock timeout"),
            database.begin() as editor,
        ):
            editor.execute(text("SET LOCAL lock_timeout = '100ms'"))
            editor.get(Source, search_catalog["source"]).approval_status = "pending"

    with database.begin() as reader:
        event.listen(reader.connection(), "before_cursor_execute", edit_source)
        try:
            assert (
                len(placements.deliver_placements(reader, result_query(search_catalog), KEY)) == 1
            )
        finally:
            event.remove(reader.connection(), "before_cursor_execute", edit_source)
        assert checked


def test_directory_context_uses_bounded_sql_probes(database, search_catalog):
    query = result_query(
        search_catalog,
        kind="sources",
        identifiers=[search_catalog["source"], *[uuid.uuid4() for _ in range(119)]],
    )
    with database.begin() as session:
        with counted_statements(session) as statements:
            rows = placements.deliver_placements(session, query, KEY)
        assert len(rows) == 1
        assert len(statements) <= 45, f"Executed {len(statements)} SQL statements"


@pytest.mark.parametrize("count", [105, 205])
@pytest.mark.parametrize(
    "rejection", ["intent", "weak", "audience", "exhausted", "destination", "busy_context"]
)
def test_structured_search_batches_empty_catalog_checks_before_locking(database, count, rejection):
    catalog = seed_rotation(database, count=count)
    with database.begin() as session:
        session.execute(
            update(PartnerProduct).values(
                evidence=[
                    {
                        "capability": "Test Python applications and inspect metrics",
                        "quote": "Test Python applications and inspect metrics with alerts.",
                    }
                ]
            )
        )
        session.execute(
            update(PartnerProductTopic)
            .where(PartnerProductTopic.product_id == PartnerProduct.id)
            .values(
                score=65 if rejection == "weak" else 100,
                product_revision=PartnerProduct.revision,
                product_updated_at=PartnerProduct.updated_at,
            )
        )
        session.add(
            ArticleTopic(
                article_id=catalog["article"],
                topic_id=catalog["topic"],
                role="primary",
                relevance=1.0,
                evidence="Python is the article's primary context.",
            )
        )
        account = session.get(PartnerAccount, catalog["account"])
        account.tier = "platinum"
        account.ad_formats = ["engagement"]
        if rejection == "audience":
            account.audience = {"experience_levels": ["senior"]}
        if rejection == "destination":
            account.outbound_destination = "listing_url"
            session.execute(update(PartnerListing).values(listing_url=""))
        ensure_automatic_assets(session, account.id, now=catalog["now"])
        if rejection == "exhausted":
            campaign = session.scalar(
                select(PartnerCampaign).where(
                    PartnerCampaign.account_id == account.id,
                    PartnerCampaign.ad_format == "engagement",
                )
            )
            campaign.daily_impression_limit = 1
            asset = session.scalar(
                select(PartnerAsset).where(PartnerAsset.campaign_id == campaign.id)
            )
            session.add(
                PartnerPlacementDelivery(
                    delivery_id=uuid.uuid4(),
                    asset_id=asset.id,
                    issued_at=catalog["now"],
                    surface="search",
                )
            )
        source_id = session.scalar(
            select(ArticleOrigin.source_id).where(ArticleOrigin.article_id == catalog["article"])
        )
        query = result_query(
            catalog,
            "unrelated Python drawing" if rejection == "intent" else "Python",
            kind="sources",
            identifiers=[source_id],
        )
        candidates = placements.contextual_candidates(session, query, catalog["now"], as_query=True)
        assert session.scalar(select(func.count()).select_from(candidates.subquery())) == (
            0 if rejection in {"intent", "weak"} else count
        )
    with database.begin() as editor, database.begin() as reader:
        if rejection == "busy_context":
            editor.execute(
                select(Article.id).where(Article.id == catalog["article"]).with_for_update()
            )
        with counted_statements(reader) as statements:
            assert placements.deliver_placements(reader, query, KEY, now=catalog["now"]) == []
        assert len(statements) <= 22, f"Executed {len(statements)} SQL statements"
        assert sum(
            "FROM partner_products" in value and "FOR UPDATE" in value for value in statements
        ) <= (1 if rejection == "busy_context" else 0)
