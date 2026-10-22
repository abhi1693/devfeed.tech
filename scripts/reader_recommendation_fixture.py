"""Prepare real recommendation reads and separately labeled synthetic index population.

The caller must first validate its local disposable targets and finish other timed
workloads. Only ``prepare_fixture`` writes. Importing this module does no work;
domain imports happen inside functions so the caller can select a revision first.
"""

import time
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

from sqlalchemy import (
    Integer,
    Uuid,
    column,
    event,
    func,
    literal,
    select,
    text,
    true,
    update,
    values,
)
from sqlalchemy.dialects.postgresql import insert

PRODUCTION_RECOMMENDATIONS = 12500
PRODUCTION_STATES = 33
MAX_CANDIDATES = 500


def _accounts(session, helper):
    from devfeed_core.models import UserAccount

    accounts = {}
    for account in session.scalars(
        select(UserAccount).where(UserAccount.subject.like("reader-profile-%"))
    ):
        number = int(account.subject.removeprefix("reader-profile-"))
        if account.id != helper.fixture_id("user_accounts", number):
            raise ValueError("Recommendation preparation requires deterministic fixture accounts")
        accounts[number] = account
    if not {2, 3, 6}.issubset(accounts):
        raise ValueError("Recommendation preparation requires reader fixture accounts 2, 3 and 6")
    return accounts


def _state(state):
    if state is None:
        return None
    result = {}
    for field in (
        "user_id",
        "generation",
        "invalidated",
        "candidates_dirty",
        "ranked_at",
        "computed_at",
        "expires_at",
        "next_refresh_at",
        "attempts",
        "interest_count",
        "preference_revision",
    ):
        value = getattr(state, field)
        result[field] = (
            value.isoformat()
            if hasattr(value, "isoformat")
            else str(value)
            if field in {"user_id", "generation"} and value is not None
            else value
        )
    return result


@contextmanager
def capture_sql(engine):
    """Retain SQL structure and duration, never bound values or exception messages."""
    records: list[dict[str, Any]] = []

    def before(conn, cursor, statement, parameters, context, many):
        record: dict[str, Any] = {"statement": " ".join(statement.split()), "status": "running"}
        records.append(record)
        context._reader_fixture_record = record
        context._reader_fixture_started = time.perf_counter()

    def after(conn, cursor, statement, parameters, context, many):
        record = context._reader_fixture_record
        record.update(
            ms=round((time.perf_counter() - context._reader_fixture_started) * 1000, 3),
            status="completed",
        )

    def failed(context):
        execution = context.execution_context
        if execution is None or not hasattr(execution, "_reader_fixture_record"):
            return
        execution._reader_fixture_record.update(
            ms=round((time.perf_counter() - execution._reader_fixture_started) * 1000, 3),
            status="failed",
            error_type=type(context.original_exception).__name__,
            sqlstate=getattr(context.original_exception, "sqlstate", None),
        )

    listeners = (
        ("before_cursor_execute", before),
        ("after_cursor_execute", after),
        ("handle_error", failed),
    )
    for name, listener in listeners:
        event.listen(engine, name, listener)
    try:
        yield records
    finally:
        for name, listener in listeners:
            event.remove(engine, name, listener)


def _populate_unmeasured(factory, helper):
    from devfeed_core.models import (
        Article,
        ArticleOrigin,
        Source,
        UserAccount,
        UserRecommendation,
        UserRecommendationState,
        UserSource,
        utcnow,
    )
    from devfeed_core.publication import visible_article
    from devfeed_core.user_settings import FeedSettings, normalize_feed_settings

    source_id = helper.fixture_id("sources", 3)
    with factory.begin() as session:
        accounts = _accounts(session, helper)
        filler = [account for number, account in accounts.items() if number >= 6]
        default = FeedSettings()
        if any(
            (settings := normalize_feed_settings(account.feed_settings)).languages
            != default.languages
            or settings.content_types != default.content_types
            for account in filler
        ):
            raise ValueError("Unmeasured fixture accounts must retain default content filters")
        ids = [account.id for account in filler]
        follows = session.scalar(
            select(func.count())
            .select_from(UserSource)
            .where(UserSource.user_id.in_(ids), UserSource.source_id == source_id)
        )
        if follows != len(ids):
            raise ValueError("Every unmeasured account must already follow source 3")
        articles = list(
            session.scalars(
                select(Article.id)
                .where(
                    visible_article(),
                    Article.language.in_(default.languages),
                    Article.content_type.in_(default.content_types),
                    select(ArticleOrigin.id)
                    .join(Source, Source.id == ArticleOrigin.source_id)
                    .where(
                        ArticleOrigin.article_id == Article.id,
                        ArticleOrigin.source_id == source_id,
                        Source.approval_status == "approved",
                    )
                    .exists(),
                )
                .order_by(Article.feed_at.desc(), Article.id.desc())
                .limit(MAX_CANDIDATES)
            )
        )
        if not articles:
            raise ValueError("Approved followed source 3 must have visible fixture articles")
        session.execute(
            insert(UserRecommendationState)
            .from_select(
                ["user_id"],
                select(UserAccount.id).where(
                    UserAccount.id.in_([account.id for account in accounts.values()])
                ),
            )
            .on_conflict_do_nothing()
        )
        prepared = values(
            column("article_id", Uuid), column("position", Integer), name="prepared_articles"
        ).data([(identifier, position) for position, identifier in enumerate(articles, start=1)])
        for start in range(0, len(ids), 10):
            users = (
                select(UserAccount.id).where(UserAccount.id.in_(ids[start : start + 10])).subquery()
            )
            rows = select(
                users.c.id,
                prepared.c.article_id,
                prepared.c.position,
                literal(100.0),
                literal(source_id, type_=Uuid),
                literal("followed_source"),
            ).select_from(users.join(prepared, true()))
            session.execute(
                insert(UserRecommendation)
                .from_select(
                    ["user_id", "article_id", "position", "score", "source_id", "reason"], rows
                )
                .on_conflict_do_nothing()
            )
        now = utcnow()
        session.execute(
            update(UserRecommendationState)
            .where(UserRecommendationState.user_id.in_(ids))
            .values(
                invalidated=False,
                candidates_dirty=False,
                generation=None,
                ranked_at=now,
                computed_at=now,
                expires_at=now + timedelta(hours=24),
                next_refresh_at=now + timedelta(hours=6),
                interest_count=1,
                attempts=0,
            )
        )
        # Warm the normal durable activity window before the measured GET phase.
        session.execute(
            update(UserAccount)
            .where(UserAccount.id.in_([accounts[2].id, accounts[3].id]))
            .values(last_seen_at=now)
        )
    return {
        "unmeasured_accounts": len(filler),
        "candidates_per_account": len(articles),
        "source_id": str(source_id),
        "origin": "synthetic precomputed followed-source edges; no worker claim for these users",
        "expected_rows": len(filler) * len(articles),
    }


def qualification(factory, helper):
    """Inspect the prepared population and actual ready pointer in a read-only transaction."""
    from devfeed_core.cache import CacheUnavailable
    from devfeed_core.feed_generations import generation_ids
    from devfeed_core.models import (
        Article,
        ArticleOrigin,
        Source,
        UserRecommendation,
        UserRecommendationState,
        UserSource,
        utcnow,
    )
    from devfeed_core.publication import visible_article
    from devfeed_core.user_settings import normalize_feed_settings
    from devfeed_user_api.recommendations import starter_articles

    with factory() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        accounts = _accounts(session, helper)
        population = {
            "recommendations": session.scalar(select(func.count()).select_from(UserRecommendation)),
            "states": session.scalar(select(func.count()).select_from(UserRecommendationState)),
            "fixture_users": len(accounts),
        }
        filler_ids = [account.id for number, account in accounts.items() if number >= 6]
        rows = dict(
            session.execute(
                select(UserRecommendation.user_id, func.count())
                .where(UserRecommendation.user_id.in_(filler_ids))
                .group_by(UserRecommendation.user_id)
            ).all()
        )
        source_id = helper.fixture_id("sources", 3)
        eligible = session.scalar(
            select(func.count())
            .select_from(UserRecommendation)
            .join(Article, Article.id == UserRecommendation.article_id)
            .where(
                UserRecommendation.user_id.in_(filler_ids),
                UserRecommendation.reason == "followed_source",
                UserRecommendation.source_id == source_id,
                Article.language == "en",
                visible_article(),
                select(UserSource.user_id)
                .where(
                    UserSource.user_id == UserRecommendation.user_id,
                    UserSource.source_id == UserRecommendation.source_id,
                )
                .exists(),
                select(ArticleOrigin.id)
                .join(Source, Source.id == ArticleOrigin.source_id)
                .where(
                    ArticleOrigin.article_id == UserRecommendation.article_id,
                    ArticleOrigin.source_id == UserRecommendation.source_id,
                    Source.approval_status == "approved",
                )
                .exists(),
            )
        )
        pending_state = session.get(UserRecommendationState, accounts[2].id)
        ready_state = session.get(UserRecommendationState, accounts[3].id)
        pending_items, pending_kind = starter_articles(
            session,
            accounts[2].id,
            normalize_feed_settings(accounts[2].feed_settings),
            24,
            None,
            None,
        )
        pending_count, ready_count = (
            session.scalar(
                select(func.count())
                .select_from(UserRecommendation)
                .where(UserRecommendation.user_id == accounts[number].id)
            )
            for number in (2, 3)
        )
        ready: dict[str, Any] = {
            "state": _state(ready_state),
            "materialized_count": ready_count,
            "generation": str(ready_state.generation)
            if ready_state and ready_state.generation
            else None,
            "sequence_ids": [],
            "qualified": False,
        }
        if ready_state and ready_state.generation:
            try:
                sequence = generation_ids(
                    ready_state.user_id,
                    ready_state.preference_revision,
                    ready_state.generation,
                    0,
                    MAX_CANDIDATES,
                )
                ready["sequence_ids"] = [str(identifier) for identifier in sequence or []]
            except CacheUnavailable:
                ready["sequence_error"] = "CacheUnavailable"
            expected = set(
                session.scalars(
                    select(UserRecommendation.article_id).where(
                        UserRecommendation.user_id == ready_state.user_id
                    )
                )
            )
            now = utcnow()
            ready["qualified"] = bool(
                not ready_state.invalidated
                and not ready_state.candidates_dirty
                and ready_state.ranked_at
                and ready_state.computed_at
                and ready_state.expires_at
                and ready_state.expires_at > now
                and ready_state.next_refresh_at > now
                and ready_state.attempts == 0
                and ready_state.interest_count > 0
                and 0 < ready_count <= MAX_CANDIDATES
                and len(ready["sequence_ids"]) == ready_count
                and len(set(ready["sequence_ids"])) == ready_count
                and set(ready["sequence_ids"]) == {str(identifier) for identifier in expected}
            )
        filler_total = sum(rows.values())
        return {
            "population": population,
            "above_production": population["recommendations"] > PRODUCTION_RECOMMENDATIONS
            and population["states"] > PRODUCTION_STATES,
            "production": {
                "recommendations": PRODUCTION_RECOMMENDATIONS,
                "states": PRODUCTION_STATES,
            },
            "filler": {
                "accounts": len(filler_ids),
                "populated_accounts": len(rows),
                "rows": filler_total,
                "eligible_rows": eligible,
                "minimum_per_account": min(rows.values(), default=0),
                "maximum_per_account": max(rows.values(), default=0),
                "qualified": len(rows) == len(filler_ids)
                and eligible == filler_total
                and 0
                < min(rows.values(), default=0)
                <= max(rows.values(), default=0)
                <= MAX_CANDIDATES,
            },
            "pending": {
                "state": _state(pending_state),
                "materialized_count": pending_count,
                "expected_items": len(pending_items),
                "expected_kind": pending_kind,
                "qualified": bool(
                    pending_state
                    and pending_state.invalidated
                    and pending_state.ranked_at is None
                    and pending_state.computed_at is None
                    and pending_state.generation is None
                    and pending_state.attempts == 0
                    and pending_count == 0
                ),
            },
            "ready": ready,
        }


def prepare_fixture(engine, factory, helper):
    """Populate unmeasured index rows, then run the unchanged real domain worker once."""
    from devfeed_core.config import get_settings
    from devfeed_core.recommendations import refresh_recommendations, request_recommendation_refresh

    helper.validate_targets(
        engine.url.render_as_string(hide_password=False), get_settings().redis_url
    )
    fixture = _populate_unmeasured(factory, helper)
    with engine.begin() as connection:
        connection.execute(text("ANALYZE user_recommendations, user_recommendation_states"))
    with factory.begin() as session:
        for number in (2, 3):
            if not request_recommendation_refresh(
                session, helper.fixture_id("user_accounts", number)
            ):
                raise ValueError("Measured recommendation users must have real personal signals")
    started = time.perf_counter()
    cold: dict[str, Any] = {
        "completed": False,
        "returned_candidates": None,
        "worker_statement_timeout": "5s",
    }
    with capture_sql(engine) as records:
        try:
            cold["returned_candidates"] = refresh_recommendations(
                factory, helper.fixture_id("user_accounts", 3)
            )
            cold["completed"] = True
        except Exception as error:
            # Preserve the worker's rollback, attempt increment and retry schedule.
            cold["error_type"] = type(error).__name__
    cold["seconds"] = round(time.perf_counter() - started, 3)
    cold["sql"] = records
    qualified = qualification(factory, helper)
    return {
        "fixture": fixture,
        "cold_build": cold,
        "qualification": qualified,
        "ready_proof": qualified["ready"],
    }


def _require(condition):
    if not condition:
        raise AssertionError("Invalid recommendation page proof")


def validate_pending_page(payload, *, expected_items=24):
    """Reject empty, Latest fallback, retry and accidentally ready pending responses."""
    _require(payload["status"] == "refreshing" and payload["refresh_state"] == "preparing")
    _require(payload["feed_kind"] == "following" and payload["has_interests"] is True)
    _require(payload["generation"] is None and payload["next_cursor"] is None)
    identifiers = [item["id"] for item in payload["items"]]
    _require(len(identifiers) == len(set(identifiers)) == expected_items)
    return identifiers


def validate_ready_page(payload, proof, *, previous_ids=None):
    """Require the real Redis generation and disjoint pages, rather than DB fallback."""
    _require(proof["qualified"] is True and proof["generation"] is not None)
    _require(payload["status"] == "ready" and payload["refresh_state"] == "idle")
    _require(payload["feed_kind"] == "personalized" and payload["has_interests"] is True)
    _require(payload["generation"] == proof["generation"])
    previous = set(previous_ids or [])
    identifiers = [item["id"] for item in payload["items"]]
    expected = min(24, max(0, proof["materialized_count"] - len(previous)))
    _require(len(identifiers) == len(set(identifiers)) == expected)
    _require(not previous.intersection(identifiers))
    _require(set(identifiers) == set(payload["reasons"]))
    _require(identifiers == proof["sequence_ids"][len(previous) : len(previous) + expected])
    if proof["materialized_count"] > len(previous) + expected:
        _require(payload["next_cursor"])
    return identifiers
