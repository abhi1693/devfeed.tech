"""Personal column suggestions use current owned signals and eligible article evidence."""

import copy
import time
import uuid
from datetime import timedelta

import pytest
from devfeed_core.models import (
    Article,
    ArticleLike,
    ArticleOrigin,
    ArticleTag,
    ArticleTopic,
    Source,
    Tag,
    Topic,
    UserAccount,
    UserInterest,
    UserRecommendationState,
    UserSource,
    UserTag,
    UserTopic,
    utcnow,
)
from devfeed_user_api.accounts import save_user
from devfeed_user_api.auth import UserIdentity, require_user
from devfeed_user_api.main import create_app
from fastapi.testclient import TestClient
from sqlalchemy import insert, update

pytestmark = pytest.mark.integration
PATH = "/v1/user/settings/columns/starters"


@pytest.fixture
def starter_data(database):
    identity = {
        "issuer": "https://identity.example",
        "subject": "user-a",
        "organization_id": "org-1",
        "name": "User",
        "email": "reader@example.test",
    }
    first = uuid.UUID(save_user(identity))
    second = uuid.UUID(save_user({**identity, "subject": "user-b"}))
    current = UserIdentity(
        user_id=str(first),
        **identity,
        expires_at=int(time.time()) + 3600,
        csrf_token="test",
    )
    app = create_app()
    app.dependency_overrides[require_user] = lambda: current
    with TestClient(app) as client:
        yield client, current, first, second


def topic(session, index, status="active"):
    identifier = uuid.UUID(int=index)
    session.execute(
        insert(Topic.__table__).values(
            id=identifier,
            name=f"Topic {index}",
            slug=f"topic-{index}",
            kind="technology",
            status=status,
        )
    )
    return identifier


def source(session, index=100, approval_status="approved", enabled=True):
    identifier = uuid.UUID(int=index)
    session.execute(
        insert(Source.__table__).values(
            id=identifier,
            name=f"Source {index}",
            source_type="publisher",
            feed_url=f"https://publisher.example/{index}/rss",
            approval_status=approval_status,
            enabled=enabled,
        )
    )
    return identifier


def tag(session, index):
    identifier = uuid.UUID(int=index)
    session.execute(
        insert(Tag.__table__).values(id=identifier, name=f"Tag {index}", slug=f"tag-{index}")
    )
    return identifier


def article(session, index, source_id, topics=(), tags=(), **settings):
    identifier = uuid.UUID(int=1000 + index)
    url = f"https://publisher.example/articles/{index}"
    session.execute(
        insert(Article.__table__).values(
            id=identifier,
            canonical_url=url,
            url_hash=identifier.hex,
            title=f"Article {index}",
            **{
                "language": "en",
                "content_type": "article",
                "publication_status": "published",
                "review_status": "approved",
                **settings,
            },
        )
    )
    if source_id is not None:
        session.execute(
            insert(ArticleOrigin.__table__).values(
                article_id=identifier,
                source_id=source_id,
                entry_key=str(index),
                original_url=url,
            )
        )
    for topic_id, role in topics:
        session.execute(
            insert(ArticleTopic.__table__).values(
                article_id=identifier, topic_id=topic_id, role=role, relevance=1, evidence="test"
            )
        )
    for tag_id in tags:
        session.execute(insert(ArticleTag.__table__).values(article_id=identifier, tag_id=tag_id))
    return identifier


def feed(kind, identifier):
    return {"kind": kind, "id": str(identifier)}


def test_starters_are_read_only_and_do_not_invent_personal_signals(starter_data, database):
    client, _, account, other = starter_data
    settings = {"view": "columns", "languages": ["fr"], "unknown": {"retain": True}}
    with database.begin() as session:
        session.get(UserAccount, account).feed_settings = copy.deepcopy(settings)
        selected = topic(session, 1)
        publisher = source(session)
        liked = article(session, 1, publisher, [(selected, "primary")], language="fr")
        session.add(ArticleLike(user_id=other, article_id=liked))
        # Prepared interest state can be stale after an unfollow/unlike; it is not a direct signal.
        session.add(
            UserInterest(
                user_id=account,
                topic_id=selected,
                seed_topic_id=selected,
                weight=100,
                reason="followed_topic",
            )
        )
        state = session.get(UserRecommendationState, account)
        before = (state.generation, state.invalidated, state.preference_revision)
    response = client.get(PATH, params={"user_id": str(other)})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"feeds": []}
    with database() as session:
        assert session.get(UserAccount, account).feed_settings == settings
        state = session.get(UserRecommendationState, account)
        assert (state.generation, state.invalidated, state.preference_revision) == before


def test_starters_round_robin_direct_signal_kinds_and_cap_at_four(starter_data, database):
    client, _, account, _ = starter_data
    with database.begin() as session:
        followed_topics = [topic(session, index) for index in (2, 1)]
        publishers = [source(session, index) for index in (102, 101)]
        tags = [tag(session, index) for index in (202, 201)]
        liked_topic = topic(session, 3)
        for index in range(2):
            article(
                session,
                index,
                publishers[index],
                [(followed_topics[index], "primary")],
                [tags[index]],
            )
        liked = article(session, 3, publishers[0], [(liked_topic, "supporting")])
        session.add_all(UserTopic(user_id=account, topic_id=value) for value in followed_topics)
        session.add_all(UserSource(user_id=account, source_id=value) for value in publishers)
        session.add_all(UserTag(user_id=account, tag_id=value) for value in tags)
        session.add(ArticleLike(user_id=account, article_id=liked))
    assert client.get(PATH).json() == {
        "feeds": [
            feed("topic", min(followed_topics)),
            feed("source", min(publishers)),
            feed("tag", min(tags)),
            feed("topic", liked_topic),
        ]
    }


def test_starters_deduplicate_followed_and_liked_topics_without_losing_other_candidates(
    starter_data, database
):
    client, _, account, _ = starter_data
    with database.begin() as session:
        followed, liked_topic = topic(session, 1), topic(session, 2)
        publisher, tagged = source(session), tag(session, 200)
        first = article(session, 1, publisher, [(followed, "primary")], [tagged])
        second = article(session, 2, publisher, [(liked_topic, "primary")])
        session.add_all(
            [
                UserTopic(user_id=account, topic_id=followed),
                UserSource(user_id=account, source_id=publisher),
                UserTag(user_id=account, tag_id=tagged),
                ArticleLike(user_id=account, article_id=first),
                ArticleLike(user_id=account, article_id=second),
            ]
        )
    assert client.get(PATH).json() == {
        "feeds": [
            feed("topic", followed),
            feed("source", publisher),
            feed("tag", tagged),
            feed("topic", liked_topic),
        ]
    }


def test_direct_follow_and_unfollow_changes_are_visible_without_background_refresh(
    starter_data, database
):
    client, _, _, _ = starter_data
    with database.begin() as session:
        selected, publisher, tagged = topic(session, 1), source(session), tag(session, 200)
        article(session, 1, publisher, [(selected, "primary")], [tagged])
    paths = [
        f"/v1/user/preferences/topics/{selected}",
        f"/v1/user/preferences/sources/{publisher}",
        f"/v1/user/preferences/tags/{tagged}",
    ]
    for path in paths:
        assert client.put(path, json={"followed": True}).status_code == 200
    assert client.get(PATH).json() == {
        "feeds": [feed("topic", selected), feed("source", publisher), feed("tag", tagged)]
    }
    for path in paths:
        assert client.put(path, json={"followed": False}).status_code == 200
    assert client.get(PATH).json() == {"feeds": []}


def test_immediate_likes_use_primary_and_supporting_topics_but_not_incidental_or_proposed(
    starter_data, database
):
    client, _, account, _ = starter_data
    with database.begin() as session:
        eligible = [topic(session, index) for index in (1, 2)]
        incidental, comparison = topic(session, 3), topic(session, 4)
        proposed = topic(session, 5, status="proposed")
        publisher = source(session)
        liked = article(
            session,
            1,
            publisher,
            [
                (eligible[0], "primary"),
                (eligible[1], "supporting"),
                (incidental, "incidental"),
                (comparison, "comparison"),
                (proposed, "primary"),
            ],
        )
        session.add(
            UserInterest(
                user_id=account,
                topic_id=incidental,
                seed_topic_id=eligible[0],
                weight=10,
                reason="related_topic",
            )
        )
    path = f"/v1/user/articles/{liked}/like"
    assert client.put(path, json={"liked": True}).status_code == 200
    assert client.get(PATH).json() == {"feeds": [feed("topic", value) for value in eligible]}
    assert client.put(path, json={"liked": False}).status_code == 200
    assert client.get(PATH).json() == {"feeds": []}


@pytest.mark.parametrize("kind", ["topic", "source", "tag", "liked_topic"])
@pytest.mark.parametrize("mismatch", [{"language": "en"}, {"content_type": "article"}])
def test_suggested_feeds_need_visible_articles_matching_both_saved_filters(
    starter_data, database, kind, mismatch
):
    client, _, account, _ = starter_data
    with database.begin() as session:
        session.get(UserAccount, account).feed_settings = {
            "languages": ["fr"],
            "content_types": ["news"],
        }
        selected, publisher, tagged = topic(session, 1), source(session), tag(session, 200)
        evidence = article(
            session,
            1,
            publisher,
            [(selected, "primary")],
            [tagged],
            **{"language": "fr", "content_type": "news", **mismatch},
        )
        if kind == "topic":
            session.add(UserTopic(user_id=account, topic_id=selected))
        elif kind == "source":
            session.add(UserSource(user_id=account, source_id=publisher))
        elif kind == "tag":
            session.add(UserTag(user_id=account, tag_id=tagged))
        else:
            session.add(ArticleLike(user_id=account, article_id=evidence))
    assert client.get(PATH).json() == {"feeds": []}
    # A like remains a direct topic signal across languages, but the column must be readable.
    with database.begin() as session:
        article(
            session,
            2,
            publisher,
            [(selected, "supporting")],
            [tagged],
            language="fr",
            content_type="news",
        )
    expected = {
        "topic": feed("topic", selected),
        "source": feed("source", publisher),
        "tag": feed("tag", tagged),
        "liked_topic": feed("topic", selected),
    }[kind]
    assert client.get(PATH).json() == {"feeds": [expected]}


@pytest.mark.parametrize(
    "hidden",
    ["unpublished", "pending_review", "rejected_origin", "no_origin"],
)
def test_hidden_article_evidence_cannot_seed_any_personal_feed(starter_data, database, hidden):
    client, _, account, _ = starter_data
    with database.begin() as session:
        selected, publisher, tagged = topic(session, 1), source(session), tag(session, 200)
        if hidden == "rejected_origin":
            session.execute(
                update(Source).where(Source.id == publisher).values(approval_status="rejected")
            )
        evidence = article(
            session,
            1,
            None if hidden == "no_origin" else publisher,
            [(selected, "primary")],
            [tagged],
            publication_status=(
                "unpublished" if hidden in {"unpublished", "pending_review"} else "published"
            ),
            review_status="pending" if hidden == "pending_review" else "approved",
        )
        session.add_all(
            [
                UserTopic(user_id=account, topic_id=selected),
                UserSource(user_id=account, source_id=publisher),
                UserTag(user_id=account, tag_id=tagged),
                ArticleLike(user_id=account, article_id=evidence),
            ]
        )
    assert client.get(PATH).json() == {"feeds": []}


def test_approved_disabled_source_remains_readable_but_rejection_removes_it(starter_data, database):
    client, _, account, _ = starter_data
    with database.begin() as session:
        publisher = source(session, enabled=False)
        article(session, 1, publisher)
        session.add(UserSource(user_id=account, source_id=publisher))
    assert client.get(PATH).json() == {"feeds": [feed("source", publisher)]}
    with database.begin() as session:
        session.execute(
            update(Source).where(Source.id == publisher).values(approval_status="rejected")
        )
    assert client.get(PATH).json() == {"feeds": []}


def test_stale_followed_catalog_moderation_and_incidental_links_are_not_suggestions(
    starter_data, database
):
    client, _, account, _ = starter_data
    with database.begin() as session:
        proposed = topic(session, 1, status="proposed")
        incidental = topic(session, 2)
        approved, rejected = source(session), source(session, 101, approval_status="rejected")
        tagged = tag(session, 200)
        evidence = article(
            session,
            1,
            approved,
            [(proposed, "primary"), (incidental, "incidental")],
            [tagged],
        )
        # A second approved origin makes the article public; it does not approve this source.
        session.execute(
            insert(ArticleOrigin.__table__).values(
                article_id=evidence,
                source_id=rejected,
                entry_key="rejected-copy",
                original_url="https://rejected.example/copy",
            )
        )
        session.add_all(
            [
                UserTopic(user_id=account, topic_id=proposed),
                UserTopic(user_id=account, topic_id=incidental),
                UserSource(user_id=account, source_id=rejected),
                UserTag(user_id=account, tag_id=tagged),
            ]
        )
    assert client.get(PATH).json() == {"feeds": [feed("tag", tagged)]}


def test_liked_topics_rank_by_evidence_count_then_stable_identity(starter_data, database):
    client, _, account, _ = starter_data
    with database.begin() as session:
        first, stronger, tied = topic(session, 1), topic(session, 2), topic(session, 3)
        publisher = source(session)
        for index, selected in enumerate((first, stronger, stronger, tied)):
            evidence = article(session, index, publisher, [(selected, "primary")])
            session.add(ArticleLike(user_id=account, article_id=evidence))
    assert client.get(PATH).json() == {
        "feeds": [feed("topic", value) for value in (stronger, first, tied)]
    }


def test_like_evidence_is_bounded_to_the_latest_hundred_user_likes(starter_data, database):
    client, _, account, _ = starter_data
    now = utcnow()
    with database.begin() as session:
        older, recent = topic(session, 1), topic(session, 2)
        publisher = source(session)
        for index in range(101):
            evidence = article(
                session, index, publisher, [(older if index == 0 else recent, "primary")]
            )
            session.add(
                ArticleLike(
                    user_id=account,
                    article_id=evidence,
                    created_at=now + timedelta(seconds=index),
                )
            )
    assert client.get(PATH).json() == {"feeds": [feed("topic", recent)]}
    newest = uuid.UUID(int=1100)
    assert client.put(f"/v1/user/articles/{newest}/like", json={"liked": False}).status_code == 200
    assert client.get(PATH).json() == {"feeds": [feed("topic", recent), feed("topic", older)]}


@pytest.mark.parametrize("field", ["user_id", "subject", "issuer", "organization_id"])
def test_personal_starters_require_the_complete_account_identity(starter_data, field):
    client, current, _, other = starter_data
    setattr(current, field, str(other) if field == "user_id" else "wrong-owner")
    assert client.get(PATH).status_code == 401


def test_personal_starters_do_not_leak_between_accounts(starter_data, database):
    client, current, first, second = starter_data
    with database.begin() as session:
        selected, publisher = topic(session, 1), source(session)
        article(session, 1, publisher, [(selected, "primary")])
        session.add(UserTopic(user_id=first, topic_id=selected))
    assert client.get(PATH).json() == {"feeds": [feed("topic", selected)]}
    current.user_id, current.subject = str(second), "user-b"
    assert client.get(PATH).json() == {"feeds": []}
    current.user_id, current.subject = str(first), "user-a"
    assert client.get(PATH).json() == {"feeds": [feed("topic", selected)]}
