"""Private account inspection composes scoped rows without regenerating reader state."""

import uuid
from datetime import timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from devfeed_admin_api import users
from devfeed_core.models import (
    Article,
    ArticleBookmark,
    ArticleLike,
    ArticleOrigin,
    ArticleTopic,
    Topic,
    UserAccount,
    UserInterest,
    UserMustRead,
    UserReadingDay,
    UserReadingEvent,
    UserRecommendation,
    UserSource,
    UserTopic,
)
from devfeed_core.services import OperationConflict, RecordNotFound
from devfeed_http.pagination import ListQuery
from fastapi import HTTPException

from tests.support.source_readiness import NOW, source


def account(**changes):
    return UserAccount(
        **{
            "id": uuid.uuid4(),
            "issuer": "https://issuer.example",
            "subject": "private-subject",
            "organization_id": "private-org",
            "name": "Sign-in identity",
            "email": "ada@example.test",
            "username": "ada-reader",
            "profile": {"display_name": "Ada", "private": "never-expose"},
            "appearance_settings": {},
            "feed_settings": {},
            "created_at": NOW - timedelta(days=10),
            "last_seen_at": NOW,
            **changes,
        }
    )


def article(**changes):
    identifier = uuid.uuid4()
    publisher = source(approval="approved")
    url = f"https://publisher.example/{identifier}"
    return Article(
        **{
            "id": identifier,
            "title": "Engineering article",
            "canonical_url": url,
            "url_hash": identifier.hex * 2,
            "review_status": "approved",
            "publication_status": "unpublished",
            "discovered_at": NOW - timedelta(days=2),
            "language": "en",
            "content_type": "tutorial",
            "origins": [
                ArticleOrigin(
                    article_id=identifier,
                    source=publisher,
                    source_id=publisher.id,
                    entry_key=str(identifier),
                    original_url=url,
                )
            ],
            **changes,
        }
    )


def topic(**changes):
    return Topic(
        **{
            "id": uuid.uuid4(),
            "name": "Databases",
            "slug": "databases",
            "kind": "concept",
            "status": "active",
            **changes,
        }
    )


def preloaded_page(monkeypatch, owner, rows, **changes):
    """The pagination service has already scoped and paged these records in SQL."""
    parents, requested = [], []
    page = {"items": rows, "total": 4 + len(rows), "offset": 4, "limit": 2, **changes}

    def require(session, model, identifier):
        parents.append((model, identifier))
        assert model is UserAccount and identifier == owner.id

    monkeypatch.setattr(users, "require_record", require)
    monkeypatch.setattr(users, "paginate", lambda *args: requested.append(args) or page)
    return page, parents, requested


@pytest.mark.parametrize(
    "collection",
    [
        "topics",
        "sources",
        "likes",
        "bookmarks",
        "reads",
        "reading_days",
        "inferred_interests",
        "recommendations",
    ],
)
def test_nested_inspection_rejects_unknown_parent_before_reading_collections(
    monkeypatch, collection
):
    def absent(*args):
        raise RecordNotFound("UserAccount not found")

    monkeypatch.setattr(users, "require_record", absent)
    monkeypatch.setattr(
        users, "paginate", lambda *_: pytest.fail("Missing account leaked a collection read")
    )
    with pytest.raises(RecordNotFound, match="UserAccount not found"):
        getattr(users, collection)(uuid.uuid4(), object(), object())


@pytest.mark.parametrize("collection", ["topics", "likes", "bookmarks", "sources"])
def test_relationship_tabs_preserve_follow_save_dates_and_catalog_status(monkeypatch, collection):
    owner, target = (
        account(),
        topic()
        if collection == "topics"
        else source(approval="approved")
        if collection == "sources"
        else article(),
    )
    model = {
        "topics": UserTopic,
        "sources": UserSource,
        "likes": ArticleLike,
        "bookmarks": ArticleBookmark,
    }[collection]
    foreign = (
        "topic_id"
        if collection == "topics"
        else "source_id"
        if collection == "sources"
        else "article_id"
    )
    linked = model(user_id=owner.id, created_at=NOW, **{foreign: target.id})
    _, parents, requested = preloaded_page(monkeypatch, owner, [linked])
    if collection == "topics":
        monkeypatch.setattr(users, "topic_labels", lambda *_: {target.id: target})
    elif collection != "sources":
        monkeypatch.setattr(users, "article_labels", lambda *_: {target.id: target})
    session = SimpleNamespace(execute=lambda _: [target])
    query = ListQuery(q="", sort=None, limit=2, offset=4)

    result = getattr(users, collection)(owner.id, session, query)

    assert parents == [(UserAccount, owner.id)] and requested[0][2] is query
    assert {key: result[key] for key in ("total", "offset", "limit")} == {
        "total": 5,
        "offset": 4,
        "limit": 2,
    }
    row = result["items"][0]
    assert row["id"] == target.id
    if collection in {"topics", "sources"}:
        assert set(row) == {"id", "name", "status", "followed_at"}
        assert row["name"] == target.name and row["followed_at"] == NOW
        assert row["status"] == (
            target.status if collection == "topics" else target.approval_status
        )
    else:
        date_field = "liked_at" if collection == "likes" else "bookmarked_at"
        assert set(row) == {"id", "title", "publication_status", date_field}
        assert row["title"] == target.title and row["publication_status"] == "unpublished"
        assert row["liked_at" if collection == "likes" else "bookmarked_at"] == NOW


def test_removed_articles_remain_in_distinct_daily_click_history_without_a_catalog_link(
    monkeypatch,
):
    owner, retained, removed = account(), article(), uuid.uuid4()
    history = [
        UserReadingEvent(
            user_id=owner.id, article_id=retained.id, read_date=NOW.date(), occurred_at=NOW
        ),
        UserReadingEvent(
            user_id=owner.id,
            article_id=removed,
            read_date=NOW.date(),
            occurred_at=NOW - timedelta(hours=1),
        ),
    ]
    preloaded_page(monkeypatch, owner, history)
    monkeypatch.setattr(users, "article_labels", lambda *_: {retained.id: retained})

    result = users.reads(owner.id, object(), ListQuery(q="", sort=None, limit=2, offset=4))

    assert result["items"] == [
        {
            "id": f"{NOW.date()}:{retained.id}",
            "article_id": retained.id,
            "title": retained.title,
            "publication_status": "unpublished",
            "opened_at": NOW,
            "read_date": NOW.date(),
        },
        {
            "id": f"{NOW.date()}:{removed}",
            "article_id": removed,
            "title": None,
            "publication_status": None,
            "opened_at": NOW - timedelta(hours=1),
            "read_date": NOW.date(),
        },
    ]


def test_reading_day_projection_reports_distinct_count_without_inventing_completion(monkeypatch):
    owner = account()
    day = UserReadingDay(user_id=owner.id, read_date=NOW.date(), article_count=3, last_read_at=NOW)
    preloaded_page(monkeypatch, owner, [day])
    result = users.reading_days(owner.id, object(), ListQuery(q="", sort=None, limit=2, offset=4))
    assert result["items"] == [
        {"id": NOW.date(), "read_date": NOW.date(), "article_count": 3, "last_read_at": NOW}
    ]


def test_inferred_interests_retain_removed_topic_ids_and_reasons_until_refresh(monkeypatch):
    owner, seed, removed = account(), topic(), uuid.uuid4()
    records = [
        UserInterest(
            user_id=owner.id,
            topic_id=seed.id,
            seed_topic_id=seed.id,
            weight=100,
            reason="followed_topic",
        ),
        UserInterest(
            user_id=owner.id,
            topic_id=removed,
            seed_topic_id=uuid.uuid4(),
            weight=40,
            reason="related_topic",
        ),
    ]
    preloaded_page(monkeypatch, owner, records)
    received = []
    monkeypatch.setattr(
        users, "topic_labels", lambda session, ids: received.append(ids) or {seed.id: seed}
    )

    result = users.inferred_interests(
        owner.id, object(), ListQuery(q="", sort=None, limit=2, offset=4)
    )

    assert received == [{seed.id, removed, records[1].seed_topic_id}]
    assert result["items"][0]["name"] == result["items"][0]["seed_topic_name"] == seed.name
    assert result["items"][1] == {
        "id": removed,
        "name": None,
        "status": None,
        "seed_topic_id": records[1].seed_topic_id,
        "seed_topic_name": None,
        "weight": 40,
        "reason": "related_topic",
    }


@pytest.mark.parametrize("with_source", [False, True])
def test_recommendation_inspection_preserves_stored_rank_score_and_optional_attribution(
    monkeypatch, with_source
):
    owner, target, seed = account(), article(), topic()
    publisher = target.origins[0].source
    target.topic_links = [
        ArticleTopic(
            article_id=target.id,
            topic_id=seed.id,
            topic=seed,
            role="primary",
            relevance=1.0,
            evidence="The article discusses database engineering.",
            origin="manual",
        )
    ]
    row = UserRecommendation(
        user_id=owner.id,
        article_id=target.id,
        position=5,
        score=0.75,
        reason="followed_source" if with_source else "followed_topic",
        source_id=publisher.id if with_source else None,
        topic_id=seed.id if not with_source else None,
        seed_topic_id=seed.id if not with_source else None,
    )
    preloaded_page(monkeypatch, owner, [row])
    monkeypatch.setattr(users, "article_labels", lambda *_: {target.id: target})
    monkeypatch.setattr(
        users, "topic_labels", lambda *_: {seed.id: seed} if not with_source else {}
    )
    session = SimpleNamespace(execute=lambda _: [publisher])
    if not with_source:
        session.execute = lambda _: pytest.fail("No source attribution needs loading")

    result = users.recommendations(owner.id, session, ListQuery(q="", sort=None, limit=2, offset=4))

    assert result["items"] == [
        {
            "id": target.id,
            "title": target.title,
            "publication_status": "unpublished",
            "source_id": row.source_id,
            "source_name": publisher.name if with_source else None,
            "position": 5,
            "score": 0.75,
            "reason": row.reason,
            "topic_id": row.topic_id,
            "topic_name": seed.name if not with_source else None,
            "seed_topic_id": row.seed_topic_id,
            "seed_topic_name": seed.name if not with_source else None,
        }
    ]


@pytest.mark.parametrize(
    "configured,previous,expected",
    [
        ("local", None, "UTC"),
        ("Asia/Kolkata", None, "Asia/Kolkata"),
        ("local", "America/Los_Angeles", "America/Los_Angeles"),
    ],
)
def test_must_reads_uses_account_or_persisted_zone_without_creating_a_selection(
    monkeypatch, configured, previous, expected
):
    owner = account(appearance_settings={"timezone": configured})
    now = NOW.replace(hour=0, minute=30)
    monkeypatch.setattr(users, "utcnow", lambda: now)
    monkeypatch.setattr(users, "record", lambda *_: owner)
    selected = []
    session = SimpleNamespace(
        scalar=lambda _: (
            UserMustRead(
                user_id=owner.id,
                selection_date=now.date() - timedelta(days=1),
                timezone=previous,
                picks=[],
            )
            if previous
            else None
        ),
        get=lambda model, key: selected.append((model, key)) or None,
    )
    received = []
    monkeypatch.setattr(
        users, "read_snapshot", lambda *args, **kw: received.append((args, kw)) or ([], {}, [])
    )

    result = users.must_reads(owner.id, session, timezone=None)

    day = now.astimezone(ZoneInfo(expected)).date()
    assert selected == [(UserMustRead, (owner.id, day))]
    assert result.selection_date == day and result.timezone == expected
    assert not result.generated and result.presented_at is None and result.items == []
    assert received[0][0][1] is None and received[0][1] == {"include_details": False}


def test_must_reads_keeps_original_positions_after_visibility_pruning_and_never_claims_presentation(
    monkeypatch,
):
    owner, hidden, retained = account(), article(), article()
    # The visibility service has removed a now-unpublished first pick. Remaining
    # picks are already public; this test covers composition, not SQL visibility.
    retained.publication_status, retained.published_to_feed_at = (
        "published",
        NOW - timedelta(days=1),
    )
    snapshot = UserMustRead(
        user_id=owner.id,
        selection_date=NOW.date(),
        timezone="UTC",
        picks=[
            {"id": str(hidden.id), "reason": "followed_topic"},
            {"id": str(retained.id), "reason": "followed_source"},
        ],
        presented_at=None,
    )
    monkeypatch.setattr(users, "record", lambda *_: owner)
    monkeypatch.setattr(users, "utcnow", lambda: NOW)
    monkeypatch.setattr(
        users,
        "read_snapshot",
        lambda *args, **kw: ([retained], {str(retained.id): "followed_source"}, [retained.id]),
    )
    session = SimpleNamespace(get=lambda *_: snapshot)

    result = users.must_reads(owner.id, session, timezone="UTC")

    assert result.generated and result.presented_at is None and snapshot.presented_at is None
    assert result.items == [
        users.AdminUserMustRead(
            id=retained.id, title=retained.title, position=2, reason="followed_source", read=True
        )
    ]
    assert snapshot.picks[0]["id"] == str(hidden.id)


def test_invalid_must_read_timezone_fails_before_selection_read(monkeypatch):
    owner = account()
    monkeypatch.setattr(users, "record", lambda *_: owner)
    with pytest.raises(HTTPException, match="Invalid timezone") as error:
        users.must_reads(owner.id, object(), timezone="not/a-zone")
    assert error.value.status_code == 422


@pytest.mark.parametrize("eligible", [False, True])
def test_analysis_requires_personalization_work_and_commits_only_a_real_request(
    monkeypatch, eligible
):
    owner, events = account(), []
    monkeypatch.setattr(users, "require_record", lambda *args: events.append("parent"))
    monkeypatch.setattr(
        users,
        "request_recommendation_refresh",
        lambda session, identifier: events.append(identifier) or eligible,
    )
    monkeypatch.setattr(
        users, "user", lambda identifier, session: events.append("detail") or {"id": identifier}
    )
    session = SimpleNamespace(commit=lambda: events.append("commit"))
    if not eligible:
        with pytest.raises(
            OperationConflict, match="No follows, likes, interests, or recommendations"
        ):
            users.analyze_user(owner.id, session)
        assert events == ["parent", owner.id]
    else:
        assert users.analyze_user(owner.id, session) == {"id": owner.id}
        assert events == ["parent", owner.id, "commit", "detail"]
