"""Daily picks and public leaderboard projections retain explicit account/day context.

Ranking, visibility filters, locks, and atomic presentation claims are delegated
to repository/integration contracts; supplied rows are already eligible and ranked.
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core.models import (
    ArticleTopic,
    Source,
    Tag,
    Topic,
    UserAccount,
    UserMustRead,
    UserRecommendation,
    UserRecommendationState,
)
from devfeed_core.schemas import ArticleOut
from devfeed_user_api import leaderboard, must_reads
from devfeed_user_api.auth import UserIdentity
from fastapi import HTTPException, Response
from sqlalchemy.orm import Session
from test_feed_snapshot_contracts_unit import published_article

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)


@pytest.fixture
def selection(monkeypatch):
    account = UserAccount(
        id=uuid.uuid4(),
        issuer="https://identity.example",
        subject="reader",
        organization_id="readers",
        username="reader",
        feed_settings={"languages": ["en"]},
        profile={},
    )
    state = UserRecommendationState(
        user_id=account.id,
        generation=uuid.uuid4(),
        ranked_at=NOW - timedelta(minutes=1),
        computed_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(hours=1),
        invalidated=False,
        preference_revision=2,
        interest_count=1,
        attempts=0,
    )
    session = Mock(spec=Session)
    session.scalar.return_value = account
    session.get.side_effect = lambda model, _: state if model is UserRecommendationState else None
    monkeypatch.setattr(must_reads, "utcnow", lambda: NOW)
    user = UserIdentity(
        user_id=str(account.id),
        issuer=account.issuer,
        subject=account.subject,
        organization_id=account.organization_id,
        expires_at=4_102_444_800,
        csrf_token="test-csrf",
    )
    return SimpleNamespace(account=account, state=state, session=session, user=user)


def test_prepared_daily_picks_explain_each_source_of_relevance_without_changing_rank_order(
    selection,
):
    account, session = selection.account, selection.session
    articles = [published_article(index) for index in range(1, 6)]
    topic = Topic(id=uuid.uuid4(), name="Python", slug="python", kind="language", status="active")
    seed = Topic(
        id=uuid.uuid4(), name="Databases", slug="databases", kind="concept", status="active"
    )
    source = articles[0].origins[0].source
    for article in articles[1:4]:
        article.topic_links = [
            ArticleTopic(
                article_id=article.id, topic_id=topic.id, topic=topic, role="primary", relevance=0.9
            )
        ]
    tag = Tag(id=uuid.uuid4(), name="Python tips", slug="python-tips", aliases=[])
    articles[4].tags = [tag]
    reasons = ["followed_source", "followed_topic", "liked_topic", "related_topic", "followed_tag"]
    rows = [
        (
            article,
            UserRecommendation(
                user_id=account.id,
                article_id=article.id,
                position=index + 1,
                score=10.0 - index,
                reason=reason,
                source_id=source.id if index == 0 else None,
                topic_id=topic.id if index in {1, 2, 3} else None,
                seed_topic_id=seed.id if index == 3 else topic.id if index in {1, 2} else None,
                tag_id=tag.id if index == 4 else None,
            ),
        )
        for index, (article, reason) in enumerate(zip(articles, reasons, strict=True))
    ]
    session.execute.return_value = SimpleNamespace(all=lambda: rows)
    session.scalars.side_effect = [list(reversed(articles)), [articles[0].id]]
    session.get.side_effect = lambda model, identifier: (
        selection.state
        if model is UserRecommendationState
        else source
        if model is Source and identifier == source.id
        else topic
        if model is Topic and identifier == topic.id
        else seed
        if model is Topic and identifier == seed.id
        else None
    )

    snapshot, result = must_reads.load_selection(session, account.id, "UTC")

    assert isinstance(snapshot, UserMustRead) and snapshot.user_id == account.id
    assert snapshot.selection_date == NOW.date() and snapshot.timezone == "UTC"
    assert [pick["id"] for pick in snapshot.picks] == [str(article.id) for article in articles]
    assert [pick["reason"] for pick in snapshot.picks] == [
        f"Because you follow {source.name}",
        "Because you follow Python",
        "Based on articles you liked about Python",
        "Related to your interest in Databases",
        "Selected from your recommendations",
    ]
    assert [item.id for item in result.items] == [article.id for article in articles]
    assert result.read_ids == [articles[0].id] and not result.preparing and not result.presented
    session.add.assert_called_once_with(snapshot)
    session.flush.assert_called_once()
    session.commit.assert_not_called()


@pytest.mark.parametrize("unprepared", ["missing", "invalidated", "not-ranked"])
def test_unprepared_recommendations_do_not_invent_or_persist_daily_picks(selection, unprepared):
    if unprepared == "missing":
        selection.session.get.side_effect = lambda *_: None
    elif unprepared == "invalidated":
        selection.state.invalidated = True
    else:
        selection.state.ranked_at = None
        selection.state.generation = None
        selection.state.expires_at = None

    snapshot, result = must_reads.load_selection(selection.session, selection.account.id, "UTC")

    assert snapshot is None and result.items == [] and result.preparing
    selection.session.execute.assert_not_called()
    selection.session.add.assert_not_called()
    selection.session.commit.assert_not_called()


def test_existing_daily_snapshot_keeps_its_timezone_and_picks_while_rankings_refresh(selection):
    article = published_article(1)
    snapshot = UserMustRead(
        user_id=selection.account.id,
        selection_date=NOW.date(),
        timezone="Europe/London",
        picks=[{"id": str(article.id), "reason": "Saved daily reason"}],
        presented_at=NOW - timedelta(minutes=5),
    )
    selection.state.invalidated = True
    selection.session.get.side_effect = lambda model, _: (
        snapshot if model is UserMustRead else selection.state
    )
    selection.session.scalars.side_effect = [[article], [article.id]]

    saved, result = must_reads.load_selection(selection.session, selection.account.id, "UTC")

    assert saved is snapshot and result.timezone == "Europe/London"
    assert result.presented and not result.preparing
    assert result.reasons == {str(article.id): "Saved daily reason"}
    selection.session.execute.assert_not_called()
    selection.session.add.assert_not_called()


def test_missing_daily_selection_account_is_unauthorized_before_snapshot_reads(selection):
    selection.session.scalar.return_value = None

    with pytest.raises(HTTPException) as error:
        must_reads.load_selection(selection.session, selection.account.id, "UTC")

    assert error.value.status_code == 401
    selection.session.get.assert_not_called()
    selection.session.add.assert_not_called()


def test_daily_read_commits_the_requested_snapshot_and_returns_the_preparing_state(selection):
    selection.state.invalidated = True

    result = must_reads.daily(selection.user, selection.session, timezone="UTC")

    assert result.date == NOW.date() and result.preparing and result.items == []
    selection.session.commit.assert_called_once()


@pytest.mark.parametrize("automatic,claimed", [(True, True), (True, False), (False, True)])
def test_presentation_reports_the_repository_claim_without_regenerating_picks(
    selection, monkeypatch, automatic, claimed
):
    article = published_article(1)
    snapshot = UserMustRead(
        user_id=selection.account.id,
        selection_date=NOW.date(),
        timezone="UTC",
        picks=[{"id": str(article.id), "reason": "Saved reason"}],
        presented_at=NOW - timedelta(minutes=1) if not automatic or not claimed else None,
    )
    result = must_reads.DailySelection(
        date=NOW.date(),
        timezone="UTC",
        items=[ArticleOut.from_article(article)],
        reasons={},
        read_ids=[],
        presented=bool(snapshot.presented_at),
    )
    loader = Mock(return_value=(snapshot, result))
    monkeypatch.setattr(must_reads, "load_selection", loader)
    selection.session.execute.return_value = SimpleNamespace(
        scalar_one_or_none=lambda: selection.account.id if claimed else None
    )

    response = must_reads.present(
        must_reads.Presentation(date=NOW.date(), timezone="UTC", automatic=automatic),
        selection.user,
        selection.session,
    )

    assert response == {"claimed": claimed}
    loader.assert_called_once_with(selection.session, selection.account.id, "UTC")
    selection.session.commit.assert_called_once()


def test_midnight_rollover_rejects_presentation_of_the_previous_days_selection(
    selection, monkeypatch
):
    before = NOW.replace(hour=23, minute=59, second=59)
    times = iter([before, before + timedelta(seconds=2)])
    monkeypatch.setattr(must_reads, "utcnow", lambda: next(times))
    selection.session.get.side_effect = lambda *_: None

    with pytest.raises(HTTPException) as error:
        must_reads.present(
            must_reads.Presentation(date=before.date(), timezone="UTC"),
            selection.user,
            selection.session,
        )

    assert error.value.status_code == 409
    selection.session.commit.assert_not_called()


@pytest.mark.parametrize("private", [False, True])
def test_own_reading_rank_projection_omits_identity_and_represents_unlisted_accounts_as_null(
    selection, private
):
    selection.account.profile = {"visibility": {"public": not private}}
    selection.session.execute.side_effect = [
        SimpleNamespace(
            mappings=lambda days=days: SimpleNamespace(
                first=lambda: (
                    None
                    if private
                    else {
                        "user_id": selection.account.id,
                        "rank": 3,
                        "username": "reader",
                        "display_name": "Reader",
                        "avatar_url": None,
                        "days": days,
                        "email": "private@example.test",
                    }
                )
            )
        )
        for days in [7, 12]
    ]
    response = Response()

    result = leaderboard.my_ranks(selection.user, selection.session, response)

    assert response.headers["Cache-Control"] == "no-store"
    if private:
        assert result.longest_streak is result.reading_days is None
    else:
        assert result.longest_streak.days == 7 and result.reading_days.days == 12
        assert result.longest_streak.rank == result.reading_days.rank == 3
    assert "private@example.test" not in result.model_dump_json()
    selection.session.commit.assert_not_called()


def test_public_leaderboards_preserve_supplied_competition_ranks_and_drop_private_columns(
    selection,
):
    rows = [
        {
            "rank": rank,
            "username": name,
            "display_name": None,
            "avatar_url": None,
            "days": days,
            "user_id": uuid.uuid4(),
            "subject": "private-subject",
        }
        for rank, name, days in [(1, "reader-a", 10), (1, "reader-b", 10), (3, "reader-c", 7)]
    ]
    selection.session.execute.return_value = SimpleNamespace(mappings=lambda: rows)
    response = Response()

    result = leaderboard.leaderboard(selection.session, response)

    assert [row.rank for row in result.longest_streak] == [1, 1, 3]
    assert [row.username for row in result.reading_days] == ["reader-a", "reader-b", "reader-c"]
    assert (
        "user_id" not in result.model_dump_json()
        and "private-subject" not in result.model_dump_json()
    )
    assert response.headers["Cache-Control"] == "no-store"
