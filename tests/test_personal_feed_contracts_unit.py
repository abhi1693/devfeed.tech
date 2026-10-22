"""Personal feed continuation/fallback over preloaded projections, not simulated SQL."""

import base64
import json
import uuid
from collections import deque
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core.cache import CacheUnavailable
from devfeed_core.models import Article, UserRecommendation, UserRecommendationState
from devfeed_user_api import recommendations as feeds
from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql.dml import Update
from sqlalchemy.sql.elements import TextClause
from test_feed_snapshot_contracts_unit import NOW, published_article


class Rows(list):
    def first(self):
        return self[0] if self else None

    def all(self):
        return list(self)


class FeedReads:
    """Return already-scoped ORM projection rows; never evaluate SQL predicates."""

    def __init__(self, state, *, article_pages=(), rank_pages=(), flags=(True, True), ordered=()):
        self.state = state
        self.article_pages = deque(article_pages)
        self.rank_pages = deque(rank_pages)
        self.flags = deque(flags)
        self.ordered = ordered
        self.reads = []
        self.commit = Mock()
        self.rollback = Mock()

    def execute(self, statement):
        if isinstance(statement, TextClause):
            return Rows()
        entity = statement.column_descriptions[0]["entity"]
        if entity is UserRecommendationState:
            self.reads.append("state")
            return Rows([(self.state, NOW, {})]) if self.state is not None else Rows()
        if entity is Article:
            self.reads.append("articles")
            return Rows(self.article_pages.popleft())
        assert entity is UserRecommendation
        self.reads.append("ranks")
        return Rows(self.rank_pages.popleft())

    def scalar(self, _):
        self.reads.append("exists")
        return self.flags.popleft()

    def scalars(self, _):
        self.reads.append("ordered")
        return self.ordered


@pytest.fixture
def scenario(monkeypatch):
    user_id = uuid.uuid4()
    state = UserRecommendationState(
        user_id=user_id,
        generation=uuid.uuid4(),
        ranked_at=NOW - timedelta(minutes=1),
        preference_revision=3,
        invalidated=False,
        expires_at=NOW + timedelta(hours=1),
        interest_count=1,
        attempts=0,
    )
    user = SimpleNamespace(user_id=str(user_id))
    monkeypatch.setattr(feeds, "utcnow", lambda: NOW)
    return user, state


def recommendation(user, article, position):
    return UserRecommendation(
        user_id=uuid.UUID(user.user_id),
        article_id=article.id,
        position=position,
        score=50.0,
        reason="followed_source",
        source_id=article.origins[0].source_id,
    )


def published_articles(*indices):
    articles = []
    source = None
    for index in indices:
        article = published_article(index, source=source)
        source = article.origins[0].source
        articles.append(article)
    return articles


def request(user, session, **changes):
    values = dict(
        limit=2, cursor=None, generation=None, sort="recommended", content_type=None, source_id=None
    )
    values.update(changes)
    return feeds.feed(user, session, **values)


def unpack(cursor):
    return json.loads(base64.urlsafe_b64decode(cursor))


def cursor(owner, generation, position=1, scope="scope"):
    return base64.urlsafe_b64encode(
        json.dumps([str(owner), str(generation), position, scope]).encode()
    ).decode()


def test_missing_owned_account_is_rejected_before_cache_or_article_reads(scenario, monkeypatch):
    user, _ = scenario
    generation_reader = Mock()
    monkeypatch.setattr(feeds, "generation_ids", generation_reader)
    session = FeedReads(None)
    with pytest.raises(HTTPException) as raised:
        request(user, session)
    assert (raised.value.status_code, raised.value.detail) == (401, "User account unavailable")
    assert session.reads == ["state"]
    generation_reader.assert_not_called()


@pytest.mark.parametrize(
    "has_work,attempts,refresh_state",
    [(False, 0, "idle"), (True, 0, "preparing"), (True, 2, "retrying")],
)
def test_unprepared_feed_explains_empty_initial_state_without_doing_graph_work(
    scenario, monkeypatch, has_work, attempts, refresh_state
):
    user, state = scenario
    state.invalidated, state.interest_count, state.attempts = True, 0, attempts
    session = FeedReads(state, flags=(has_work, False))
    generation_reader = Mock(side_effect=AssertionError("No generation is prepared"))
    monkeypatch.setattr(feeds, "generation_ids", generation_reader)
    page = request(user, session)
    assert page.items == [] and page.next_cursor is None and page.generation is None
    assert page.refresh_state == refresh_state
    assert page.status == ("refreshing" if has_work else "ready")
    assert session.reads == ["state", "exists", *(["exists"] if has_work else [])]
    session.commit.assert_not_called()


def test_sparse_generation_pages_preserve_raw_rank_cursor_and_never_skip_unreturned_articles(
    scenario, monkeypatch
):
    user, state = scenario
    articles = published_articles(*range(1, 6))
    entries = [recommendation(user, article, index + 1) for index, article in enumerate(articles)]
    ids = [article.id for article in articles]
    cache_reads = []

    def generation(owner, revision, selected, offset, count):
        cache_reads.append((owner, revision, selected, offset, count))
        return ids[offset : offset + count]

    monkeypatch.setattr(feeds, "generation_ids", generation)
    # The repository omits a currently ineligible row. This fixture supplies
    # scoped results; integration tests prove the eligibility SQL itself.
    session = FeedReads(
        state,
        article_pages=[
            [(articles[2], entries[2]), (articles[0], entries[0])],
            [(articles[3], entries[3]), (articles[4], entries[4])],
        ],
    )
    page = request(user, session)
    assert [item.id for item in page.items] == [articles[0].id, articles[2].id]
    owner, selected, position, signature = unpack(page.next_cursor)
    assert owner == user.user_id and selected == str(state.generation) and position == 3
    assert len(signature) == 16
    assert set(page.reasons) == {str(articles[0].id), str(articles[2].id)}
    assert all(reason.kind == "followed_source" for reason in page.reasons.values())
    assert page.status == "ready" and page.has_interests and page.refresh_state == "idle"
    assert [(row[3], row[4]) for row in cache_reads] == [(0, 3), (3, 24)]
    next_page = request(
        user,
        FeedReads(state, article_pages=[list(zip(articles[3:], entries[3:], strict=True))]),
        cursor=page.next_cursor,
    )
    assert [item.id for item in next_page.items] == ids[3:] and next_page.next_cursor is None
    session.commit.assert_not_called()


@pytest.mark.parametrize("cache_failure", [None, CacheUnavailable])
def test_fresh_request_can_fall_back_to_existing_database_ranks_without_cache(
    scenario, monkeypatch, cache_failure
):
    user, state = scenario
    article = published_article(1)
    entry = recommendation(user, article, 4)
    reader = Mock(return_value=None, side_effect=cache_failure)
    monkeypatch.setattr(feeds, "generation_ids", reader)
    session = FeedReads(state, rank_pages=[[(article.id, 4)]], article_pages=[[(article, entry)]])
    page = request(user, session)
    assert [item.id for item in page.items] == [article.id]
    assert page.generation == uuid.uuid5(
        state.user_id, f"ranked:{state.ranked_at}:{state.preference_revision}"
    )
    assert page.next_cursor is None and session.reads == ["state", "ranks", "articles"]


@pytest.mark.parametrize("sort", ["newest", "most_liked"])
def test_nonrecommended_order_can_use_database_generation_when_no_cache_is_prepared(
    scenario, monkeypatch, sort
):
    user, state = scenario
    articles = published_articles(1, 2)
    entries = [recommendation(user, article, i + 1) for i, article in enumerate(articles)]
    monkeypatch.setattr(feeds, "generation_ids", Mock(side_effect=CacheUnavailable))
    session = FeedReads(
        state,
        ordered=[a.id for a in articles],
        article_pages=[list(zip(articles, entries, strict=True))],
    )
    page = request(
        user,
        session,
        sort=sort,
        source_id=articles[0].origins[0].source_id,
        content_type="tutorial",
    )
    assert [item.id for item in page.items] == [article.id for article in articles]
    assert page.generation == uuid.uuid5(
        state.user_id, f"ranked:{state.ranked_at}:{state.preference_revision}"
    )
    assert session.reads == ["state", "ordered", "articles"]


@pytest.mark.parametrize("sort", ["recommended", "newest"])
def test_explicit_missing_generation_never_silently_switches_to_different_ranks(
    scenario, monkeypatch, sort
):
    user, state = scenario
    selected = uuid.uuid4()
    monkeypatch.setattr(feeds, "generation_ids", Mock(return_value=None))
    session = FeedReads(state)
    with pytest.raises(HTTPException) as raised:
        request(user, session, generation=selected, sort=sort)
    assert (raised.value.status_code, raised.value.detail) == (
        409,
        "Your feed has expired. Start from the first page.",
    )
    assert session.reads == ["state"]


def test_cache_disappearing_after_partial_scan_cannot_mix_cache_and_database_orders(
    scenario, monkeypatch
):
    user, state = scenario
    identifiers = [uuid.uuid4() for _ in range(3)]
    monkeypatch.setattr(feeds, "generation_ids", Mock(side_effect=[identifiers, None]))
    session = FeedReads(state, article_pages=[[]])
    with pytest.raises(HTTPException) as raised:
        request(user, session)
    assert raised.value.status_code == 409
    assert session.reads == ["state", "articles"]


@pytest.mark.parametrize("attempts,kind", [(0, "following"), (2, "latest")])
def test_refreshing_empty_generation_uses_bounded_starter_feed_and_explains_its_status(
    scenario, monkeypatch, attempts, kind
):
    user, state = scenario
    state.invalidated, state.attempts = True, attempts
    monkeypatch.setattr(feeds, "generation_ids", Mock(return_value=[]))
    article = published_article(1)
    starter = Mock(return_value=([article], kind))
    monkeypatch.setattr(feeds, "starter_articles", starter)
    session = FeedReads(state)
    page = request(user, session)
    assert [item.id for item in page.items] == [article.id]
    assert page.feed_kind == kind and page.status == "refreshing"
    assert page.refresh_state == ("retrying" if attempts else "preparing")
    assert page.generation is page.next_cursor is None and page.has_interests
    assert starter.call_args.args[0:2] == (session, state.user_id)
    assert starter.call_args.args[3:] == (2, None, None)


def test_continuation_can_read_its_old_generation_while_a_new_one_is_current(scenario, monkeypatch):
    user, state = scenario
    articles = published_articles(1, 2)
    entries = [recommendation(user, article, i + 1) for i, article in enumerate(articles)]
    selected = state.generation
    monkeypatch.setattr(
        feeds, "generation_ids", Mock(return_value=[article.id for article in articles])
    )
    first = request(
        user, FeedReads(state, article_pages=[list(zip(articles, entries, strict=True))]), limit=1
    )
    assert first.next_cursor
    state.generation, state.invalidated = uuid.uuid4(), True
    reader = Mock(return_value=[articles[1].id])
    monkeypatch.setattr(feeds, "generation_ids", reader)
    second = request(
        user,
        FeedReads(state, article_pages=[[(articles[1], entries[1])]]),
        limit=1,
        cursor=first.next_cursor,
    )
    assert second.generation == selected and [item.id for item in second.items] == [articles[1].id]
    assert reader.call_args.args[2] == selected


@pytest.mark.parametrize("position", [0, 501, True, "2"])
def test_recommendation_cursor_rejects_invalid_rank_types_and_bounds(scenario, position):
    user, state = scenario
    with pytest.raises(HTTPException) as raised:
        feeds.decode_position(
            cursor(state.user_id, state.generation, position), state.user_id, "scope"
        )
    assert raised.value.status_code == 422


def test_recommendation_cursor_is_bound_to_owner_scope_and_generation(scenario):
    _, state = scenario
    value = cursor(state.user_id, state.generation, 500)
    assert feeds.decode_position(value, state.user_id, "scope") == (state.generation, 500)
    with pytest.raises(HTTPException) as changed:
        feeds.decode_position(value, state.user_id, "different-scope")
    assert changed.value.status_code == 409
    with pytest.raises(HTTPException) as foreign:
        feeds.decode_position(value, uuid.uuid4(), "scope")
    assert foreign.value.status_code == 422


@pytest.mark.parametrize("age", [0, 15 * 60])
def test_recent_feed_activity_does_not_start_a_write_transaction(age):
    session = Mock()
    feeds.record_activity(session, uuid.uuid4(), NOW - timedelta(seconds=age), NOW)
    session.commit.assert_not_called()
    session.execute.assert_not_called()


def test_stale_activity_ends_the_read_snapshot_before_its_bounded_update():
    events = []
    session = Mock()
    session.commit.side_effect = lambda: events.append("commit")
    session.execute.side_effect = lambda statement: events.append(
        "update" if isinstance(statement, Update) else "timeout"
    )
    feeds.record_activity(session, uuid.uuid4(), NOW - timedelta(minutes=16), NOW)
    assert events[0] == events[-1] == "commit" and events.count("update") == 1
    session.rollback.assert_not_called()


def test_activity_update_contention_never_fails_the_feed_and_rolls_back(caplog):
    session = Mock()
    session.execute.side_effect = SQLAlchemyError("private-database-detail")
    feeds.record_activity(session, uuid.uuid4(), None, NOW)
    session.rollback.assert_called_once_with()
    assert session.commit.call_count == 1
    assert (
        "feed_activity_update_deferred" in caplog.text
        and "private-database-detail" not in caplog.text
    )
