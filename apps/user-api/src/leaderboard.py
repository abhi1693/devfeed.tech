"""Rank public reading habits without exposing account identity or private profiles."""

from __future__ import annotations

from devfeed_core.models import UserAccount, UserReadingStreak
from fastapi import APIRouter, Response
from pydantic import BaseModel, Field
from sqlalchemy import RowMapping, func, or_, select, true
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql import Subquery

from devfeed_user_api.auth import User
from devfeed_user_api.dependencies import DB
from devfeed_user_api.profile import owned_account

router = APIRouter(prefix="/v1/user/leaderboard", tags=["user-leaderboard"])


class LeaderboardEntry(BaseModel):
    rank: int = Field(ge=1)
    position: int = Field(ge=1)
    username: str
    display_name: str | None
    avatar_url: str | None
    days: int = Field(gt=0)


class ReadingLeaderboard(BaseModel):
    longest_streak: list[LeaderboardEntry]
    reading_days: list[LeaderboardEntry]


class MyReadingRanks(BaseModel):
    longest_streak: LeaderboardEntry | None
    reading_days: LeaderboardEntry | None


def ranked_readers(metric: InstrumentedAttribute[int]) -> Subquery:
    # Missing visibility uses the same public default as profile settings.
    # Ties share a competition rank; position identifies rows without exposing account IDs.
    # Profile settings normalize names on write; also ignore legacy empty/space-only names.
    display_name = func.nullif(func.btrim(UserAccount.profile["display_name"].as_string()), "")
    return (
        select(
            UserAccount.id.label("user_id"),
            func.coalesce(UserAccount.username, "").label("username"),
            display_name.label("display_name"),
            UserAccount.profile["avatar_url"].as_string().label("avatar_url"),
            metric.label("days"),
            func.row_number()
            .over(order_by=(metric.desc(), UserAccount.username, UserAccount.id))
            .label("position"),
            func.rank().over(order_by=metric.desc()).label("rank"),
        )
        .join(UserReadingStreak, UserReadingStreak.user_id == UserAccount.id)
        .where(
            func.coalesce(UserAccount.profile["visibility"]["public"].as_boolean(), true()),
            or_(UserAccount.username.is_not(None), display_name.is_not(None)),
            metric > 0,
        )
        .subquery()
    )


def entry(row: RowMapping) -> LeaderboardEntry:
    return LeaderboardEntry.model_validate(
        {
            key: row[key]
            for key in ("rank", "position", "username", "display_name", "avatar_url", "days")
        }
    )


@router.get("", response_model=ReadingLeaderboard)
def leaderboard(session: DB, response: Response) -> ReadingLeaderboard:
    response.headers["Cache-Control"] = "no-store"
    boards = {}
    for key, metric in (
        ("longest_streak", UserReadingStreak.longest_days),
        ("reading_days", UserReadingStreak.total_days),
    ):
        ranked = ranked_readers(metric)
        rows = session.execute(select(ranked).order_by(ranked.c.position).limit(10)).mappings()
        boards[key] = [entry(row) for row in rows]
    return ReadingLeaderboard.model_validate(boards)


@router.get("/me", response_model=MyReadingRanks)
def my_ranks(user: User, session: DB, response: Response) -> MyReadingRanks:
    response.headers["Cache-Control"] = "no-store"
    account = owned_account(session, user)
    ranks = {}
    for key, metric in (
        ("longest_streak", UserReadingStreak.longest_days),
        ("reading_days", UserReadingStreak.total_days),
    ):
        ranked = ranked_readers(metric)
        row = (
            session.execute(select(ranked).where(ranked.c.user_id == account.id)).mappings().first()
        )
        ranks[key] = entry(row) if row else None
    return MyReadingRanks.model_validate(ranks)
