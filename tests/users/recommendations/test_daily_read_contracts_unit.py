"""Daily picks and public leaderboard projections retain explicit account/day context.

Ranking, visibility filters, locks, and atomic presentation claims are delegated
to repository/integration contracts; supplied rows are already eligible and ranked.
"""

import copy
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
from devfeed_core.user_settings import FeedSettings
from devfeed_user_api import leaderboard, must_reads
from devfeed_user_api.auth import UserIdentity
from fastapi import HTTPException, Response
from sqlalchemy.orm import Session

from tests.api.test_feed_snapshot_contracts_unit import published_article
from tests.support.article_readiness import processing_case as processing_case
from tests.support.reader_context import stories as stories

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
            article.id,
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
    session.scalars.side_effect = [
        SimpleNamespace(all=lambda: [source.id]),
        list(reversed(articles)),
        [articles[0].id],
    ]
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
    selection.session.scalars.side_effect = [
        SimpleNamespace(all=lambda: [article.origins[0].source_id]),
        [article],
        [article.id],
    ]

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
@pytest.mark.parametrize("username", ["reader", ""])
def test_own_reading_rank_projection_omits_identity_and_represents_unlisted_accounts_as_null(
    selection, private, username
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
                        "position": 4,
                        "username": username,
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
        assert result.longest_streak.position == result.reading_days.position == 4
        assert result.longest_streak.username == result.reading_days.username == username
    assert "private@example.test" not in result.model_dump_json()
    selection.session.commit.assert_not_called()


def test_public_leaderboards_preserve_supplied_competition_ranks_and_drop_private_columns(
    selection,
):
    rows = [
        {
            "rank": rank,
            "position": position,
            "username": name,
            "display_name": None,
            "avatar_url": None,
            "days": days,
            "user_id": uuid.uuid4(),
            "subject": "private-subject",
        }
        for position, (rank, name, days) in enumerate(
            [(1, "reader-a", 10), (1, "", 10), (3, "", 7)], start=1
        )
    ]
    selection.session.execute.return_value = SimpleNamespace(mappings=lambda: rows)
    response = Response()

    result = leaderboard.leaderboard(selection.session, response)

    assert [row.rank for row in result.longest_streak] == [1, 1, 3]
    assert [row.username for row in result.reading_days] == ["reader-a", "", ""]
    for board in (result.longest_streak, result.reading_days):
        assert [row.position for row in board] == [1, 2, 3]
    assert (
        "user_id" not in result.model_dump_json()
        and "private-subject" not in result.model_dump_json()
    )
    assert response.headers["Cache-Control"] == "no-store"


def test_metadata_only_daily_snapshot_read_preserves_saved_picks_and_never_claims_presentation(
    stories,
):
    from devfeed_core import must_reads

    from tests.support.source_readiness import NOW

    owner, missing, session = uuid.uuid4(), uuid.uuid4(), Mock(spec=Session)
    current = stories[0]
    snapshot = UserMustRead(
        user_id=owner,
        selection_date=NOW.date(),
        timezone="Asia/Kolkata",
        presented_at=None,
        picks=[
            {"id": str(missing), "reason": "followed_topic"},
            {"id": str(current.id), "reason": "followed_source"},
        ],
    )
    before = copy.deepcopy(snapshot.picks)
    session.scalars.side_effect = [[current], [current.id]]
    articles, reasons, read_ids = must_reads.read_snapshot(
        session,
        snapshot,
        FeedSettings(languages=["en"], content_types=["tutorial"]),
        include_details=False,
    )
    assert articles == [current]
    assert reasons == {str(current.id): "followed_source"} and read_ids == [current.id]
    assert snapshot.picks == before and snapshot.presented_at is None
    session.add.assert_not_called()
    session.commit.assert_not_called()


def test_daily_presentation_rejects_an_old_local_day_before_loading_any_selection(selection):
    with pytest.raises(HTTPException) as conflict:
        must_reads.present(
            must_reads.Presentation(date=NOW.date() - timedelta(days=1), timezone="UTC"),
            selection.user,
            selection.session,
        )
    assert conflict.value.status_code == 409
    assert selection.session.mock_calls == []


def test_crossing_local_midnight_during_selection_load_does_not_present_the_new_day(
    selection, monkeypatch
):
    before = NOW.replace(hour=23, minute=59, second=59)
    after = before + timedelta(seconds=2)
    selection.state.ranked_at = selection.state.computed_at = before - timedelta(minutes=1)
    selection.state.expires_at = before + timedelta(hours=8)
    reads = iter([before, after])
    monkeypatch.setattr(must_reads, "utcnow", lambda: next(reads))
    selection.session.execute.return_value.all.return_value = []
    with pytest.raises(HTTPException) as conflict:
        must_reads.present(
            must_reads.Presentation(date=before.date(), timezone="UTC"),
            selection.user,
            selection.session,
        )
    assert conflict.value.status_code == 409
    assert conflict.value.detail == "A new daily selection is available"
    selection.session.add.assert_not_called()
    selection.session.commit.assert_not_called()


def test_retained_daily_picks_with_no_remaining_visible_articles_do_not_claim_presentation(
    selection,
):
    saved = UserMustRead(
        user_id=selection.account.id,
        selection_date=NOW.date(),
        timezone="UTC",
        picks=[{"id": str(uuid.uuid4()), "reason": "Because you follow Angular"}],
        presented_at=None,
    )
    existing_read = selection.session.get.side_effect
    selection.session.get.side_effect = lambda model, identifier: (
        saved if model is UserMustRead else existing_read(model, identifier)
    )
    selection.session.scalars.side_effect = [SimpleNamespace(all=lambda: []), [], []]
    result = must_reads.present(
        must_reads.Presentation(date=NOW.date(), timezone="UTC"),
        selection.user,
        selection.session,
    )
    assert result == {"claimed": False} and saved.presented_at is None
    selection.session.execute.assert_not_called()
    selection.session.add.assert_not_called()
    selection.session.commit.assert_called_once()
