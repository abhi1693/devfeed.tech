"""Validated negative decisions survive temporary source-row contention."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event

import pytest
from devfeed_core.models import Article, PartnerArticleMatch, PartnerProduct
from devfeed_core.partner_matching import save_article_match
from sqlalchemy import select
from test_partner_placements_integration import catalog as catalog_fixture

catalog = catalog_fixture
pytestmark = pytest.mark.integration


@pytest.mark.parametrize("source", ["product", "article"])
def test_authoritative_negative_waits_for_busy_source_and_publishes(database, catalog, source):
    product_id, article_id = catalog["paid_product"], catalog["article"]
    with database() as session:
        previous = session.get(PartnerArticleMatch, (product_id, article_id))
        assert previous.relevant and previous.origin == "rules"
    started = Event()

    def evaluate():
        with database.begin() as session:
            product = session.get(PartnerProduct, product_id)
            article = session.get(Article, article_id)
            started.set()
            save_article_match(
                session,
                product,
                article,
                False,
                0,
                "The independently evaluated capability cannot perform this article task.",
                origin="evaluation",
            )

    model, identifier = (
        (PartnerProduct, product_id) if source == "product" else (Article, article_id)
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        with database.begin() as editor:
            editor.scalar(select(model).where(model.id == identifier).with_for_update())
            future = executor.submit(evaluate)
            assert started.wait(timeout=5)
            # A busy source must not be treated as a successful, unpublished evaluation.
            with pytest.raises(TimeoutError):
                future.result(timeout=0.1)
        future.result(timeout=5)
    with database() as session:
        decision = session.get(PartnerArticleMatch, (product_id, article_id))
        assert not decision.relevant and decision.origin == "evaluation"
