"""Changed factual inputs drive bounded preparation; unchanged edges remain usable."""

import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest
from devfeed_core import partner_matching as matching
from devfeed_core.models import (
    Article,
    ArticleOrigin,
    ArticleTopic,
    PartnerArticleMatch,
    PartnerListing,
    PartnerMatchJob,
    PartnerProduct,
    PartnerProductTopic,
    Topic,
    TopicRelation,
    utcnow,
)
from devfeed_http.partner_placements import PlacementQuery
from sqlalchemy import delete, event, insert, select, text
from test_partner_placements_integration import catalog as catalog
from test_partner_placements_integration import deliver, finish_matching, query

pytestmark = pytest.mark.integration


def pending_jobs(database):
    with database() as session:
        return {
            (job.kind, job.entity_id): (job.generation, job.cursor)
            for job in session.scalars(select(PartnerMatchJob))
        }


@contextmanager
def observed_pairs(monkeypatch):
    pairs = []
    original = matching.article_match

    def record(product, article):
        pairs.append((product.id, article.id))
        return original(product, article)

    with monkeypatch.context() as patch:
        patch.setattr(matching, "article_match", record)
        yield pairs


def add_matching_articles(database, catalog, count=2):
    with database.begin() as session:
        original = session.get(Article, catalog["article"])
        articles = [
            Article(
                canonical_url=f"https://publisher.example/related-{uuid.uuid4().hex}",
                url_hash=uuid.uuid4().hex,
                title=original.title,
                summary=original.summary,
                content_type="tutorial",
                publication_status="published",
                review_status="approved",
            )
            for _ in range(count)
        ]
        session.add_all(articles)
        session.flush()
        return [article.id for article in articles]


def seed_sparse_noise(database, count=1000):
    """Already indexed inventory with no intersecting technology on either side."""
    now = utcnow()
    products = [uuid.uuid4() for _ in range(count)]
    articles = [uuid.uuid4() for _ in range(count)]
    with database.begin() as session:
        session.execute(
            insert(PartnerProduct),
            [
                {
                    "id": identifier,
                    "name": f"Rust observer {index}",
                    "product_url": f"https://rust{index}.example/",
                    "tagline": "Monitors Rust services and inspects metrics.",
                    "technologies": ["Rust"],
                    "evidence": [
                        {
                            "quote": "Monitor Rust services and inspect metrics.",
                            "capability": "Monitor services and inspect metrics",
                        }
                    ],
                    "status": "approved",
                    "verified_at": now,
                }
                for index, identifier in enumerate(products)
            ],
        )
        session.execute(
            insert(Article),
            [
                {
                    "id": identifier,
                    "canonical_url": f"https://publisher.example/elixir-{index}",
                    "url_hash": uuid.uuid4().hex,
                    "title": "Monitor Elixir services",
                    "summary": (
                        "Monitor Elixir services and inspect production metrics in this tutorial."
                    ),
                    "content_type": "tutorial",
                    "publication_status": "published",
                    "review_status": "approved",
                }
                for index, identifier in enumerate(articles)
            ],
        )
        # These disjoint inputs cannot produce edges. Model a catalog whose
        # initialization has completed, so observation covers only a later edit.
        session.execute(
            delete(PartnerMatchJob).where(PartnerMatchJob.entity_id.in_(products + articles))
        )
        for index in ("ix_articles_matching_tokens", "ix_partner_products_matching_tokens"):
            session.execute(
                text("SELECT gin_clean_pending_list(CAST(:index AS regclass))"), {"index": index}
            )
        for table in ("partner_products", "articles", "partner_match_jobs"):
            session.execute(text(f"ANALYZE {table}"))


@contextmanager
def captured_input_reads(database):
    with database() as session:
        engine = session.get_bind()
    statements = []

    def capture(_connection, _cursor, sql, parameters, _context, _executemany):
        if sql.lstrip().upper().startswith("SELECT") and (
            "FROM articles" in sql or "FROM partner_products" in sql
        ):
            statements.append((sql, parameters))

    event.listen(engine, "before_cursor_execute", capture)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", capture)


def visited_inputs(plan):
    own = 0
    if plan.get("Relation Name") in {"articles", "partner_products"}:
        own = plan.get("Actual Loops", 0) * sum(
            plan.get(field, 0)
            for field in ("Actual Rows", "Rows Removed by Filter", "Rows Removed by Index Recheck")
        )
    return own + sum(visited_inputs(child) for child in plan.get("Plans", []))


def assert_bounded_input_reads(database, statements):
    assert statements, "Expected actual input reads from the preparation worker"
    visited = 0
    with database.begin() as session:
        for sql, parameters in statements:
            plan = session.connection().exec_driver_sql(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql, parameters
            )
            visited += visited_inputs(plan.scalar_one()[0]["Plan"])
    assert visited <= 80, f"Preparing three candidate pairs visited {visited} input rows"


@pytest.mark.parametrize("changed", ["product", "article"])
def test_sparse_edits_evaluate_matching_candidates_without_scanning_unrelated_inputs(
    database, catalog, monkeypatch, changed
):
    if changed == "product":
        article_ids = [catalog["article"], *add_matching_articles(database, catalog)]
        expected = {(catalog["paid_product"], article_id) for article_id in article_ids}
    else:
        with database.begin() as session:
            original = session.get(PartnerProduct, catalog["paid_product"])
            additional = PartnerProduct(
                name="Another Kubernetes observer",
                product_url="https://observer.example/",
                tagline=original.tagline,
                technologies=original.technologies,
                evidence=original.evidence,
                status="approved",
                verified_at=utcnow(),
            )
            session.add(additional)
            session.flush()
            expected = {
                (product_id, catalog["article"])
                for product_id in (catalog["free_product"], catalog["paid_product"], additional.id)
            }
    finish_matching(database)
    seed_sparse_noise(database)
    assert pending_jobs(database) == {}
    with database.begin() as session:
        if changed == "product":
            product = session.get(PartnerProduct, catalog["paid_product"])
            product.name += " revised"
            product.updated_at = utcnow()
        else:
            session.get(Article, catalog["article"]).title += " in production — observability 🔎"

    with observed_pairs(monkeypatch) as pairs, captured_input_reads(database) as statements:
        processed = matching.refresh_partner_matches(database, product_limit=1, article_limit=10)
    assert processed == len(pairs) == 3
    assert set(pairs) == expected
    assert_bounded_input_reads(database, statements)
    finish_matching(database)
    with database() as session:
        assert all(session.get(PartnerArticleMatch, pair).relevant for pair in expected)


def test_idle_matching_keeps_unchanged_prepared_article_and_topic_edges_past_thirty_minutes(
    database, catalog, monkeypatch
):
    finish_matching(database)
    with database() as session:
        before = {
            (
                type(row),
                row.product_id,
                getattr(row, "article_id", getattr(row, "topic_id", None)),
            ): row.updated_at
            for model in (PartnerArticleMatch, PartnerProductTopic)
            for row in session.scalars(select(model))
        }
    later = utcnow() + timedelta(hours=1)
    with observed_pairs(monkeypatch) as pairs:
        assert matching.refresh_partner_matches(database, now=later) == 0
    assert pairs == [] and pending_jobs(database) == {}
    for surface in ("article", "topic"):
        assert len(deliver(database, query(catalog, surface), now=later)) == 2
    with database() as session:
        after = {
            (
                type(row),
                row.product_id,
                getattr(row, "article_id", getattr(row, "topic_id", None)),
            ): row.updated_at
            for model in (PartnerArticleMatch, PartnerProductTopic)
            for row in session.scalars(select(model))
        }
    assert after == before


@pytest.mark.parametrize("changed", ["product", "article"])
def test_fact_changes_and_matching_jobs_commit_or_roll_back_together(database, catalog, changed):
    finish_matching(database)
    identifier = catalog["paid_product"] if changed == "product" else catalog["article"]
    model = PartnerProduct if changed == "product" else Article

    def edit(session):
        row = session.get(model, identifier)
        if changed == "product":
            row.name += " current"
            row.updated_at = utcnow()
        else:
            row.title += " current"
        session.flush()

    with database() as session:
        transaction = session.begin()
        edit(session)
        assert session.get(PartnerMatchJob, (changed, identifier)) is not None
        transaction.rollback()
    assert pending_jobs(database) == {}
    assert len(deliver(database, query(catalog))) == 2

    with database.begin() as session:
        edit(session)
    assert (changed, identifier) in pending_jobs(database)
    rows = deliver(database, query(catalog))
    assert len(rows) == (1 if changed == "product" else 0)
    finish_matching(database)
    assert len(deliver(database, query(catalog))) == 2


@pytest.mark.parametrize("relevant", [False, True], ids=["negative", "positive"])
def test_alias_and_relation_invalidation_is_transactional_and_preserves_evaluation_decisions(
    database, catalog, monkeypatch, relevant
):
    finish_matching(database)
    with database.begin() as session:
        product = session.get(PartnerProduct, catalog["paid_product"])
        article = session.get(Article, catalog["article"])
        matching.save_article_match(
            session,
            product,
            article,
            relevant,
            100 if relevant else 0,
            "Independent current evaluation",
            origin="evaluation",
        )
        alias = Topic(
            name="Cluster orchestrator",
            slug="orchestrator",
            kind="platform",
            status="active",
            aliases=["Kubernetes"],
        )
        related = Topic(name="Containers", slug="containers", kind="concept", status="active")
        session.add_all([alias, related])
        session.flush()
        relation = TopicRelation(
            topic_id=catalog["topic"], related_topic_id=related.id, relation="part_of"
        )
        session.add(relation)
        alias_id, related_id = alias.id, related.id
        related_timestamp = related.updated_at
    with observed_pairs(monkeypatch) as pairs:
        finish_matching(database)
    assert pairs == []
    with database() as session:
        assert session.get(PartnerProductTopic, (catalog["paid_product"], alias_id)).score == 100
        assert session.get(PartnerProductTopic, (catalog["paid_product"], related_id)).score == 65
        decision = session.get(PartnerArticleMatch, (catalog["paid_product"], catalog["article"]))
        original_decision = (
            decision.origin,
            decision.relevant,
            decision.score,
            decision.reason,
            decision.updated_at,
        )

    def remove_support(session):
        session.get(Topic, alias_id).aliases = []
        session.delete(session.get(TopicRelation, (catalog["topic"], related_id, "part_of")))
        session.flush()
        for topic_id in (alias_id, related_id):
            assert session.get(PartnerProductTopic, (catalog["paid_product"], topic_id)) is None

    with database() as session:
        transaction = session.begin()
        remove_support(session)
        transaction.rollback()
    assert pending_jobs(database) == {}
    with database() as session:
        assert session.get(PartnerProductTopic, (catalog["paid_product"], alias_id)) is not None
        assert session.get(PartnerProductTopic, (catalog["paid_product"], related_id)) is not None

    with database.begin() as session:
        remove_support(session)
    with observed_pairs(monkeypatch) as pairs:
        finish_matching(database)
    assert pairs == []
    with database() as session:
        assert session.get(Topic, related_id).updated_at == related_timestamp
        for topic_id in (alias_id, related_id):
            assert session.get(PartnerProductTopic, (catalog["paid_product"], topic_id)) is None
        decision = session.get(PartnerArticleMatch, (catalog["paid_product"], catalog["article"]))
        assert (
            decision.origin,
            decision.relevant,
            decision.score,
            decision.reason,
            decision.updated_at,
        ) == original_decision


def test_coalesced_alias_edits_retain_invalidations_for_removed_identities(database, catalog):
    with database.begin() as session:
        topic = Topic(
            name="Catalog identity",
            slug="catalog-identity",
            kind="platform",
            status="active",
            aliases=["Kubernetes"],
        )
        session.add(topic)
        session.flush()
        identifier = topic.id
    finish_matching(database)
    with database.begin() as session:
        session.get(Topic, identifier).aliases = ["Figma"]
    first = pending_jobs(database)
    assert ("topics", catalog["paid_product"]) in first
    with database.begin() as session:
        session.get(Topic, identifier).aliases = ["Rust"]
    assert ("topics", catalog["paid_product"]) in pending_jobs(database)
    finish_matching(database)
    with database() as session:
        assert not session.scalars(
            select(PartnerProductTopic).where(PartnerProductTopic.topic_id == identifier)
        ).all()


@pytest.mark.parametrize("busy", ["product", "article"])
def test_busy_inputs_leave_preparation_pending_until_the_source_is_available(
    database, catalog, busy
):
    finish_matching(database)
    with database.begin() as session:
        product = session.get(PartnerProduct, catalog["paid_product"])
        product.updated_at = utcnow()
        current_timestamp = product.updated_at
    model, identifier = (
        (PartnerProduct, catalog["paid_product"])
        if busy == "product"
        else (Article, catalog["article"])
    )
    with database.begin() as editor:
        editor.scalar(select(model.id).where(model.id == identifier).with_for_update())
        assert matching.refresh_partner_matches(database, product_limit=1, article_limit=1) == 0
        assert ("product", catalog["paid_product"]) in pending_jobs(database)
    finish_matching(database, product_limit=1, article_limit=1)
    with database() as session:
        match = session.get(PartnerArticleMatch, (catalog["paid_product"], catalog["article"]))
        assert match.relevant and match.product_updated_at == current_timestamp


def test_failed_slice_rolls_back_edges_and_checkpoint_before_retry(database, catalog, monkeypatch):
    add_matching_articles(database, catalog)
    finish_matching(database)
    with database.begin() as session:
        session.get(PartnerProduct, catalog["paid_product"]).updated_at = utcnow()
    before = pending_jobs(database)
    original = matching.article_match
    calls = 0

    def fail_second_pair(product, article):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("Temporary preparation failure")
        return original(product, article)

    with monkeypatch.context() as patch:
        patch.setattr(matching, "article_match", fail_second_pair)
        with pytest.raises(RuntimeError, match="Temporary preparation failure"):
            matching.refresh_partner_matches(database, product_limit=1, article_limit=2)
    assert pending_jobs(database) == before
    with database() as session:
        product = session.get(PartnerProduct, catalog["paid_product"])
        assert all(
            match.product_updated_at != product.updated_at
            for match in session.scalars(
                select(PartnerArticleMatch).where(PartnerArticleMatch.product_id == product.id)
            )
        )
    finish_matching(database, product_limit=1, article_limit=2)
    with database() as session:
        product = session.get(PartnerProduct, catalog["paid_product"])
        matches = session.scalars(
            select(PartnerArticleMatch).where(PartnerArticleMatch.product_id == product.id)
        ).all()
        assert len(matches) == 3
        assert all(
            match.relevant and match.product_updated_at == product.updated_at for match in matches
        )


def test_new_generation_restarts_a_bounded_job_and_rechecks_previous_positive_edges(
    database, catalog
):
    article_ids = [catalog["article"], *add_matching_articles(database, catalog)]
    finish_matching(database)
    with database.begin() as session:
        session.get(PartnerProduct, catalog["paid_product"]).updated_at = utcnow()
    assert matching.refresh_partner_matches(database, product_limit=1, article_limit=1) == 1
    previous_generation, cursor = pending_jobs(database)[("product", catalog["paid_product"])]
    assert cursor is not None
    with database.begin() as session:
        product = session.get(PartnerProduct, catalog["paid_product"])
        product.technologies = []
        product.evidence = []
        product.updated_at = utcnow()
    generation, cursor = pending_jobs(database)[("product", catalog["paid_product"])]
    assert generation != previous_generation and cursor is None
    finish_matching(database, product_limit=1, article_limit=1)
    with database() as session:
        product = session.get(PartnerProduct, catalog["paid_product"])
        for article_id in article_ids:
            match = session.get(PartnerArticleMatch, (product.id, article_id))
            assert not match.relevant and match.product_updated_at == product.updated_at


@pytest.mark.parametrize("first", ["product", "article"])
def test_simultaneous_product_and_article_edits_use_current_projections_in_either_flush_order(
    database, catalog, first
):
    finish_matching(database)
    with database.begin() as session:
        product = session.get(PartnerProduct, catalog["paid_product"])
        article = session.get(Article, catalog["article"])

        def edit_product():
            product.technologies = ["OpenAPI"]
            product.evidence = [
                {
                    "quote": "Validate OpenAPI schemas and detect breaking changes.",
                    "capability": "Validate schemas and detect breaking changes",
                }
            ]
            product.updated_at = utcnow()

        def edit_article():
            article.title = "Validate OpenAPI schemas"
            article.summary = (
                "Validate OpenAPI schemas and detect breaking changes "
                "before merging in this practical tutorial."
            )

        edits = (edit_product, edit_article) if first == "product" else (edit_article, edit_product)
        for edit in edits:
            edit()
            session.flush()
    finish_matching(database, product_limit=1, article_limit=1)
    with database() as session:
        product = session.get(PartnerProduct, catalog["paid_product"])
        article = session.get(Article, catalog["article"])
        match = session.get(PartnerArticleMatch, (product.id, article.id))
        assert match.relevant
        assert match.product_updated_at == product.updated_at
        assert match.article_fingerprint == matching.article_fingerprint(article)
        assert not session.get(PartnerArticleMatch, (catalog["free_product"], article.id)).relevant


@pytest.mark.parametrize("technology", ["C++", "R", "机器学习"])
def test_unclassified_punctuated_short_and_unicode_technologies_are_not_lost_by_narrowing(
    database, catalog, technology
):
    with database.begin() as session:
        product = PartnerProduct(
            name=f"{technology} observer",
            product_url="https://specific-observer.example/",
            tagline=f"Monitor {technology} clusters and inspect metrics.",
            technologies=[technology],
            evidence=[
                {
                    "quote": f"Monitor {technology} clusters and inspect metrics.",
                    "capability": "Monitor clusters and inspect metrics",
                }
            ],
            status="approved",
            verified_at=utcnow(),
        )
        article = Article(
            canonical_url="https://publisher.example/specific-technology",
            url_hash=uuid.uuid4().hex,
            title=f"Monitor {technology} clusters",
            summary=(
                f"Monitor {technology} clusters and inspect metrics "
                "in this practical production tutorial."
            ),
            content_type="tutorial",
            publication_status="published",
            review_status="approved",
        )
        session.add_all([product, article])
        session.flush()
        session.add(
            PartnerListing(
                provider="free",
                external_id=uuid.uuid4().hex,
                product_id=product.id,
                name=product.name,
                product_url=product.product_url,
                listing_url="https://catalog.example/specific",
                tagline=product.tagline,
            )
        )
        product_id, article_id = product.id, article.id
    finish_matching(database)
    with database() as session:
        assert session.get(PartnerArticleMatch, (product_id, article_id)).relevant
        assert not session.scalars(
            select(ArticleTopic).where(ArticleTopic.article_id == article_id)
        ).all()
    payload = PlacementQuery(surface="article", article_id=article_id)
    assert deliver(database, payload) == []
    with database.begin() as session:
        session.add(
            ArticleOrigin(
                article_id=article_id,
                source_id=catalog["source"],
                entry_key="specific",
                original_url="https://publisher.example/specific-technology",
            )
        )
    assert pending_jobs(database) == {}
    assert [row["product_id"] for row in deliver(database, payload)] == [product_id]


def test_busy_taxonomy_row_keeps_topic_preparation_pending_without_waiting_on_foreign_keys(
    database, catalog
):
    finish_matching(database)
    with database.begin() as session:
        session.get(Topic, catalog["topic"]).aliases = ["K8s"]

    @contextmanager
    def bounded_transaction():
        with database.begin() as session:
            session.execute(text("SET LOCAL lock_timeout = '2s'"))
            yield session

    with database.begin() as editor:
        editor.scalar(select(Topic.id).where(Topic.id == catalog["topic"]).with_for_update())
        assert (
            matching.refresh_partner_matches(
                SimpleNamespace(begin=bounded_transaction), product_limit=1, article_limit=1
            )
            == 0
        )
        assert pending_jobs(database)
        with database() as session:
            assert (
                session.get(PartnerProductTopic, (catalog["paid_product"], catalog["topic"]))
                is None
            )
    finish_matching(database)
    with database() as session:
        assert (
            session.get(PartnerProductTopic, (catalog["paid_product"], catalog["topic"])).score
            == 100
        )


def test_competing_workers_advance_bounded_pages_without_losing_the_job(
    database, catalog, monkeypatch
):
    article_ids = [catalog["article"], *add_matching_articles(database, catalog)]
    finish_matching(database)
    with database.begin() as session:
        session.get(PartnerProduct, catalog["paid_product"]).updated_at = utcnow()
    with observed_pairs(monkeypatch) as pairs, ThreadPoolExecutor(max_workers=2) as workers:
        counts = list(
            workers.map(
                lambda _: matching.refresh_partner_matches(
                    database, product_limit=1, article_limit=1
                ),
                range(2),
            )
        )
    assert all(count <= 1 for count in counts)
    assert 1 <= sum(counts) <= 2
    assert len(pairs) == len(set(pairs)) == sum(counts)
    finish_matching(database, product_limit=1, article_limit=1)
    with database() as session:
        product = session.get(PartnerProduct, catalog["paid_product"])
        assert all(
            (match := session.get(PartnerArticleMatch, (product.id, article_id))).relevant
            and match.product_updated_at == product.updated_at
            for article_id in article_ids
        )


def test_raw_sql_mixed_case_alias_changes_update_matching_and_invalidate_removed_support(
    database, catalog
):
    with database.begin() as session:
        topic = Topic(
            name="Cluster management", slug="cluster-management", kind="platform", status="active"
        )
        session.add(topic)
        session.flush()
        identifier = topic.id
    finish_matching(database)
    with database.begin() as session:
        session.execute(
            text("UPDATE topics SET aliases = ARRAY['kUbErNeTeS'] WHERE id = :id"),
            {"id": identifier},
        )
    finish_matching(database)
    with database() as session:
        for product_id in (catalog["free_product"], catalog["paid_product"]):
            assert session.get(PartnerProductTopic, (product_id, identifier)).score == 100
    with database.begin() as session:
        session.execute(
            text("UPDATE topics SET aliases = ARRAY[]::varchar[] WHERE id = :id"),
            {"id": identifier},
        )
        assert not session.scalars(
            select(PartnerProductTopic).where(PartnerProductTopic.topic_id == identifier)
        ).all()
    finish_matching(database)
    with database() as session:
        assert not session.scalars(
            select(PartnerProductTopic).where(PartnerProductTopic.topic_id == identifier)
        ).all()


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE topics SET status = 'proposed' WHERE id = :id",
        "UPDATE topics SET name = 'Software' WHERE id = :id",
        "DELETE FROM topics WHERE id = :id",
    ],
    ids=["deactivate", "broad-name", "delete"],
)
def test_raw_sql_activation_of_an_unprepared_relation_target_rebuilds_neighbor_products(
    database, catalog, sql
):
    with database.begin() as session:
        target = Topic(name="Containers", slug="containers", kind="concept", status="proposed")
        session.add(target)
        session.flush()
        identifier = target.id
        session.add(
            TopicRelation(
                topic_id=catalog["topic"], related_topic_id=identifier, relation="part_of"
            )
        )
    finish_matching(database)
    with database() as session:
        assert not session.scalars(
            select(PartnerProductTopic).where(PartnerProductTopic.topic_id == identifier)
        ).all()

    with database.begin() as session:
        session.execute(
            text("UPDATE topics SET status = 'active' WHERE id = :id"), {"id": identifier}
        )
    finish_matching(database)
    with database() as session:
        for product_id in (catalog["free_product"], catalog["paid_product"]):
            assert session.get(PartnerProductTopic, (product_id, identifier)).score == 65

    with database.begin() as session:
        session.execute(text(sql), {"id": identifier})
        assert not session.scalars(
            select(PartnerProductTopic).where(PartnerProductTopic.topic_id == identifier)
        ).all()
    finish_matching(database)
    with database() as session:
        assert not session.scalars(
            select(PartnerProductTopic).where(PartnerProductTopic.topic_id == identifier)
        ).all()
        for product_id in (catalog["free_product"], catalog["paid_product"]):
            assert session.get(PartnerProductTopic, (product_id, catalog["topic"])).score == 100


def test_new_article_with_kelvin_sign_matches_grounded_ascii_technology(database, catalog):
    article_id = add_matching_articles(database, catalog, count=1)[0]
    with database.begin() as session:
        article = session.get(Article, article_id)
        article.title = article.title.replace("Kubernetes", "Kubernetes")
        article.summary = article.summary.replace("Kubernetes", "Kubernetes")
        session.add(
            ArticleOrigin(
                article_id=article.id,
                source_id=catalog["source"],
                entry_key="kelvin-sign",
                original_url=article.canonical_url,
            )
        )
    finish_matching(database)
    with database() as session:
        for product_id in (catalog["free_product"], catalog["paid_product"]):
            assert session.get(PartnerArticleMatch, (product_id, article_id)).relevant
    rows = deliver(database, PlacementQuery(surface="article", article_id=article_id))
    assert {row["product_id"] for row in rows} == {catalog["free_product"], catalog["paid_product"]}


def test_large_incompressible_article_word_keeps_index_tokens_bounded_and_matches_current(
    database, catalog
):
    oversized_word = "".join(
        hashlib.sha256(f"partner-matching-{index}".encode()).hexdigest() for index in range(160)
    )[:10000]
    with database.begin() as session:
        original = session.get(Article, catalog["article"])
        original_summary = original.summary
        article = Article(
            canonical_url="https://publisher.example/large-index-word",
            url_hash=uuid.uuid4().hex,
            title=original.title,
            summary=original_summary + "\n" + oversized_word,
            content_type="tutorial",
            publication_status="published",
            review_status="approved",
        )
        session.add(article)
        session.flush()
        article_id = article.id
        session.add(
            ArticleOrigin(
                article_id=article_id,
                source_id=catalog["source"],
                entry_key="large-index-word",
                original_url=article.canonical_url,
            )
        )
        tokens = session.scalar(select(Article.matching_tokens).where(Article.id == article_id))
        assert tokens and max(map(len, tokens)) <= 200
    finish_matching(database)
    payload = PlacementQuery(surface="article", article_id=article_id)
    expected = {catalog["free_product"], catalog["paid_product"]}
    assert {row["product_id"] for row in deliver(database, payload)} == expected

    with database.begin() as session:
        article = session.get(Article, article_id)
        article.title += " in production"
        article.summary = original_summary + "\n" + oversized_word[::-1]
        session.flush()
        tokens = session.scalar(select(Article.matching_tokens).where(Article.id == article_id))
        assert tokens and max(map(len, tokens)) <= 200
    assert deliver(database, payload) == []
    finish_matching(database)
    assert {row["product_id"] for row in deliver(database, payload)} == expected
    with database() as session:
        article = session.get(Article, article_id)
        for product_id in expected:
            decision = session.get(PartnerArticleMatch, (product_id, article_id))
            assert decision.relevant
            assert decision.article_fingerprint == matching.article_fingerprint(article)
