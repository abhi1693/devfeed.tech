"""Read projections retain complete series and invalidate only affected public data.

Aggregate rows and state writes are database boundaries; SQL aggregation and
commit visibility are covered separately. ORM change histories below are real
SQLAlchemy states exercised without a database connection or flush.
"""

import copy
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import cache_events
from devfeed_core.article_lifecycle import derive_publication_readiness
from devfeed_core.models import Article, ArticleTag
from devfeed_core.urls import fingerprint
from sqlalchemy import select, update
from sqlalchemy.orm import make_transient_to_detached

from tests.analysis.test_admission import admission as admission
from tests.support.partner_accounts import account, asset, campaign
from tests.support.partner_matching import product


@pytest.mark.parametrize("change", ["new", "tier", "link_rel", "name"])
def test_partner_policy_object_history_invalidates_public_sponsor_fields_only(change):
    current = account()
    with cache_events.AppSession() as session:
        if change != "new":
            make_transient_to_detached(current)
        session.add(current)
        if change == "tier":
            current.tier = "gold"
        elif change == "link_rel":
            current.link_rel = ["sponsored"]
        elif change == "name":
            current.name = "Renamed private billing account"
        cache_events.track_objects(session, None, None)
        assert bool(session.info.get(cache_events.DIRTY)) is (change != "name")
        if change != "name":
            assert session.info[cache_events.REASONS] == {"partner_accounts"}


@pytest.mark.parametrize("kind", ["campaign", "asset"])
@pytest.mark.parametrize("action", ["new", "delete", "unchanged"])
def test_public_sponsorship_associations_observe_real_orm_creation_and_deletion(kind, action):
    owner, value, article_id = account(), product(), uuid.uuid4()
    agreement = campaign(owner, ad_format="sponsored_content", article_id=article_id)
    current = (
        agreement
        if kind == "campaign"
        else asset(
            owner,
            value,
            agreement,
            ad_format="sponsored_content",
            article_id=article_id,
        )
    )
    with cache_events.AppSession() as session:
        if action != "new":
            make_transient_to_detached(current)
        session.add(current)
        if action == "delete":
            session.delete(current)
        cache_events.track_objects(session, None, None)
        assert bool(session.info.get(cache_events.DIRTY)) is (action != "unchanged")
        if action != "unchanged":
            assert session.info[cache_events.REASONS] == {
                "partner_campaigns" if kind == "campaign" else "partner_assets"
            }


@pytest.mark.parametrize("private", [False, True])
def test_article_tag_history_keeps_private_processing_out_of_public_invalidation(private):
    current = ArticleTag(article_id=uuid.uuid4(), tag_id=uuid.uuid4(), origin="source")
    with cache_events.AppSession() as session:
        if private:
            session.info[cache_events.PRIVATE_ARTICLES] = {current.article_id}
        session.add(current)
        cache_events.track_objects(session, None, None)
        assert bool(session.info.get(cache_events.DIRTY)) is (not private)


@pytest.mark.parametrize("field", ["summary", "editorial_revision"])
def test_public_article_prose_invalidates_but_private_review_bookkeeping_does_not(field, admission):
    completed = admission.article
    completed.review_status, completed.editorial_revision = "approved", 2
    completed.url_hash = fingerprint(completed.canonical_url)
    assert derive_publication_readiness(completed, **admission.facts).stage == "ready"
    completed.publication_status, completed.published_to_feed_at = (
        "published",
        admission.facts["now"],
    )
    # The observer sees a loaded column projection. Keep associations unloaded,
    # while retaining the validated publication receipt and its identities.
    current = Article(
        **{
            column.name: copy.deepcopy(getattr(completed, column.name))
            for column in Article.__table__.columns
        }
    )
    make_transient_to_detached(current)
    with cache_events.AppSession() as session:
        session.add(current)
        setattr(
            current,
            field,
            "Updated source-supported navigation details." if field == "summary" else 3,
        )
        cache_events.track_objects(session, None, None)
        assert bool(session.info.get(cache_events.DIRTY)) is (field == "summary")


@pytest.mark.parametrize("operation", ["read", "private-write"])
def test_statement_observer_ignores_reads_and_explicit_private_write_operations(operation):
    state = SimpleNamespace(
        is_insert=False,
        is_update=operation == "private-write",
        is_delete=False,
        statement=select(Article) if operation == "read" else update(Article),
        execution_options={"devfeed_private_write": True} if operation == "private-write" else {},
        update_execution_options=Mock(),
    )
    cache_events.track_statements(state)
    state.update_execution_options.assert_not_called()
