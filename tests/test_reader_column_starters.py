"""Personal starter selection stays diverse, owned, visible, and bounded without a DB."""

import uuid
from types import SimpleNamespace

from devfeed_core.reader_column_starters import personal_column_starters
from devfeed_core.user_settings import FeedSettings
from sqlalchemy.dialects import postgresql

OWNER = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def identifier(index):
    return uuid.UUID(int=index)


class CandidateSession:
    """Supply candidate rows while retaining real SQL statements for policy assertions."""

    def __init__(self, topics=(), sources=(), tags=(), liked_topics=()):
        self.results = iter((topics, sources, tags, liked_topics))
        self.queries = []

    def scalars(self, statement):
        self.queries.append(statement)
        rows = list(next(self.results))
        return SimpleNamespace(all=lambda: rows)


def feeds(session):
    selected = personal_column_starters(session, OWNER, FeedSettings())
    return [(feed.kind, feed.id) for feed in selected]


def test_personal_starters_round_robin_signal_types_and_stop_at_four_unique_feeds():
    shared_id = identifier(1)
    session = CandidateSession(
        topics=[shared_id, identifier(2)],
        sources=[shared_id, identifier(3)],
        tags=[shared_id, identifier(4)],
        liked_topics=[identifier(5), identifier(6)],
    )
    assert feeds(session) == [
        ("topic", shared_id),
        ("source", shared_id),
        ("tag", shared_id),
        ("topic", identifier(5)),
    ]
    assert len(session.queries) == 4


def test_personal_starters_skip_repeated_liked_topics_without_losing_later_candidates():
    session = CandidateSession(
        topics=[identifier(1)],
        tags=[identifier(2), identifier(3)],
        liked_topics=[identifier(1), identifier(4)],
    )
    assert feeds(session) == [
        ("topic", identifier(1)),
        ("tag", identifier(2)),
        ("tag", identifier(3)),
        ("topic", identifier(4)),
    ]


def test_empty_personal_signals_do_not_invent_starters_and_queries_keep_ownership_and_budgets():
    session = CandidateSession()
    settings = FeedSettings(languages=["fr"], content_types=["news"])
    assert personal_column_starters(session, OWNER, settings) == []
    queries = [
        " ".join(
            str(
                statement.compile(
                    dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
                )
            ).split()
        )
        for statement in session.queries
    ]
    assert len(queries) == 4
    for query in queries:
        assert query.endswith("LIMIT 4")
        assert f"user_id = '{OWNER}'" in query
        assert "articles.language IN ('fr')" in query
        assert "articles.content_type IN ('news')" in query
        assert "articles.publication_status = 'published'" in query
        assert "articles.review_status = 'approved'" in query
        assert "sources.approval_status = 'approved'" in query
    assert "topics.status = 'active'" in queries[0]
    assert "article_topics.role IN ('primary', 'supporting')" in queries[0]
    assert "sources.enabled" not in queries[1]
    assert "article_likes.created_at DESC, article_likes.article_id LIMIT 100" in queries[3]
    assert "ORDER BY count(*) DESC, topics.id" in queries[3]
