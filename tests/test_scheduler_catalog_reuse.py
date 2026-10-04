"""Scheduler publication keeps authoritative guards without ranking while holding locks."""

import pytest
from devfeed_core import analysis, article_automation, publication_policy
from devfeed_core.config import get_settings
from devfeed_core.db import get_engine
from devfeed_core.models import Article, ArticleAnalysisJob, utcnow
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session
from test_automation_integration import ready, seed
from test_full_app_automation import full

pytestmark = pytest.mark.integration


def test_publication_batch_ranks_once_per_article_before_locks(database, monkeypatch):
    full(monkeypatch, AUTOMATION_BATCH_SIZE=5)
    monkeypatch.setattr(get_settings(), "cache_enabled", False)
    with database.begin() as session:
        articles = [seed(session, name=f"Framework{index}") for index in range(5)]
        for _, article, topic in articles:
            ready(session, article, topic)
    locked = [False]
    connections = [0]
    ranks, evaluations, loads = [], [], []
    rank = analysis._candidate_scores
    evaluate = publication_policy.evaluate_publication
    load = analysis._read_catalog

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "FOR UPDATE" in statement or "LOCK TABLE topics" in statement:
            locked[0] = True

    def released(session, transaction):
        if transaction.parent is None:
            locked[0] = False

    def checkout(*args):
        connections[0] += 1

    def checkin(*args):
        connections[0] -= 1

    def ranking(*args):
        assert not locked[0], "Full-catalog ranking must precede row and taxonomy locks"
        assert connections[0] == 0, "Ranking must release the read transaction"
        ranks.append(True)
        return rank(*args)

    def evaluation(*args, **kwargs):
        evaluations.append(True)
        return evaluate(*args, **kwargs)

    def loading(*args):
        loads.append(True)
        return load(*args)

    monkeypatch.setattr(analysis, "_candidate_scores", ranking)
    monkeypatch.setattr(analysis, "_read_catalog", loading)
    monkeypatch.setattr(publication_policy, "evaluate_publication", evaluation)
    monkeypatch.setattr(article_automation, "evaluate_publication", evaluation)
    event.listen(get_engine(), "before_cursor_execute", capture)
    event.listen(get_engine(), "checkout", checkout)
    event.listen(get_engine(), "checkin", checkin)
    event.listen(Session, "after_transaction_end", released)
    try:
        counts = article_automation.schedule_article_automation(database)
    finally:
        event.remove(get_engine(), "before_cursor_execute", capture)
        event.remove(get_engine(), "checkout", checkout)
        event.remove(get_engine(), "checkin", checkin)
        event.remove(Session, "after_transaction_end", released)
    assert counts["articles_published"] == 5
    assert len(ranks) == len(evaluations) == 5
    assert len(loads) == 1


@pytest.mark.parametrize("changed", ["catalog", "content", "editorial", "source"])
def test_change_between_preparation_and_locking_never_applies_stale_result(
    database, monkeypatch, changed
):
    full(monkeypatch)
    with database.begin() as session:
        source, article, topic = seed(session)
        ready(session, article, topic)
        identifier, source_id, topic_id = article.id, source.id, topic.id
    prepare = article_automation._prepare_article_candidates

    def racing_prepare(*args):
        result = prepare(*args)
        with database.begin() as editor:
            if changed == "catalog":
                editor.execute(
                    text("UPDATE topics SET name='Changed' WHERE id=:id"), {"id": topic_id}
                )
            elif changed == "content":
                editor.execute(
                    text("UPDATE articles SET summary='Changed article text' WHERE id=:id"),
                    {"id": identifier},
                )
            elif changed == "editorial":
                editor.execute(
                    text(
                        "UPDATE articles SET editorial_revision=editorial_revision+1 WHERE id=:id"
                    ),
                    {"id": identifier},
                )
            else:
                editor.execute(
                    text("UPDATE sources SET enabled=false WHERE id=:id"), {"id": source_id}
                )
        return result

    monkeypatch.setattr(article_automation, "_prepare_article_candidates", racing_prepare)
    counts = article_automation.schedule_article_automation(database)
    assert counts["articles_published"] == counts["articles_rejected"] == 0
    with database() as session:
        article = session.get(Article, identifier)
        assert article.review_status == "pending" and article.publication_status == "unpublished"
        if changed in {"catalog", "content"}:
            assert article.automation_next_check_at <= utcnow()
            assert len(session.scalars(select(ArticleAnalysisJob)).all()) == 1
