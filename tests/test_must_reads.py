"""Stable daily ranking and account-private presentation."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from devfeed_core.models import Article, UserMustRead, UserRecommendation, utcnow
from devfeed_user_api import must_reads
from fastapi import HTTPException
from sqlalchemy import select, update
from test_user_personalization import user_data as user_data
from test_user_recommendations import prepare


def test_local_day_and_invalid_timezone(monkeypatch):
    monkeypatch.setattr(must_reads, "utcnow", lambda: datetime(2026, 10, 3, 20, tzinfo=UTC))
    assert str(must_reads.selection_day("Asia/Kolkata")) == "2026-10-04"
    assert str(must_reads.selection_day("America/New_York")) == "2026-10-03"
    with pytest.raises(HTTPException):
        must_reads.selection_day("invalid/zone")


@pytest.mark.integration
def test_ranked_snapshot_claim_and_withdrawal(user_data, database, monkeypatch):
    client, user, _ = prepare(user_data, database)
    result = client.get("/v1/user/must-reads?timezone=Asia/Kolkata").json()
    with database() as session:
        ranked = list(
            session.scalars(
                select(UserRecommendation.article_id)
                .where(UserRecommendation.user_id == user)
                .order_by(UserRecommendation.position)
                .limit(5)
            )
        )
    assert [item["id"] for item in result["items"]] == list(map(str, ranked))
    assert not result["presented"]
    current = user_data[1]
    current.user_id = str(user_data[3])
    other = client.get("/v1/user/must-reads?timezone=Asia/Kolkata").json()
    assert other["items"] == []
    current.user_id = str(user)
    payload = {"date": result["date"], "timezone": "Asia/Kolkata"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(
            pool.map(
                lambda _: client.post("/v1/user/must-reads/presentation", json=payload).json()[
                    "claimed"
                ],
                range(2),
            )
        )
    assert sorted(claims) == [False, True]
    assert client.post("/v1/user/must-reads/presentation", json=payload).json() == {
        "claimed": False
    }
    assert client.post(
        "/v1/user/must-reads/presentation", json={**payload, "automatic": False}
    ).json() == {"claimed": True}
    with database.begin() as session:
        session.execute(
            update(UserRecommendation)
            .where(UserRecommendation.user_id == user)
            .values(position=-UserRecommendation.position)
        )
        session.execute(
            update(UserRecommendation)
            .where(UserRecommendation.user_id == user)
            .values(position=111 + UserRecommendation.position)
        )
        session.get(Article, ranked[0]).publication_status = "unpublished"
    refreshed = client.get("/v1/user/must-reads?timezone=Asia/Kolkata").json()
    assert [item["id"] for item in refreshed["items"]] == list(map(str, ranked[1:]))
    assert refreshed["presented"]
    tomorrow = utcnow() + timedelta(days=1)
    monkeypatch.setattr(must_reads, "utcnow", lambda: tomorrow)
    assert client.post("/v1/user/must-reads/presentation", json=payload).status_code == 409
    assert client.get("/v1/user/must-reads?timezone=Asia/Kolkata").json()["presented"] is False
    with database() as session:
        assert (
            len(list(session.scalars(select(UserMustRead).where(UserMustRead.user_id == user))))
            == 2
        )
        assert not session.scalar(select(UserMustRead).where(UserMustRead.user_id == uuid.uuid4()))
