"""Durable CLI discovery workflow. External requests never hold database transactions."""

import hashlib
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert

from devfeed_core.ai_capacity import CAPACITY_ERRORS
from devfeed_core.config import get_settings
from devfeed_core.db import session_factory
from devfeed_core.discovery_crawler import discover
from devfeed_core.discovery_import import PublisherHint
from devfeed_core.discovery_quality import VERSION, assessment_evidence, score
from devfeed_core.inference_errors import AnalysisError
from devfeed_core.models import (
    CandidateAssessment,
    CandidateDiscovery,
    CandidateFeed,
    Source,
    SourceCandidate,
    SourceDiscoveryJob,
    utcnow,
)
from devfeed_core.schemas import SourceDecision
from devfeed_core.services import OperationConflict, RecordNotFound, review_source


def record(row) -> dict:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def candidate(session, candidate_id, *, lock=False):
    query = select(SourceCandidate).where(SourceCandidate.id == candidate_id)
    if lock:
        source_id = session.scalar(
            select(SourceCandidate.source_id).where(SourceCandidate.id == candidate_id)
        )
        if source_id:
            session.scalar(select(Source).where(Source.id == source_id).with_for_update())
    row = session.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise RecordNotFound("Discovery candidate not found")
    return row


def enqueue(session, candidate_id, stage="discover", *, background=False, assess_after=False):
    session.execute(
        insert(SourceDiscoveryJob)
        .values(
            candidate_id=candidate_id,
            stage=stage,
            result={"background": True, "assess_after": assess_after} if background else {},
        )
        .on_conflict_do_nothing()
    )

    if background:
        session.execute(
            update(SourceDiscoveryJob)
            .where(
                SourceDiscoveryJob.candidate_id == candidate_id,
                SourceDiscoveryJob.stage == stage,
                SourceDiscoveryJob.status == "queued",
            )
            .values(
                result=SourceDiscoveryJob.result.op("||")(
                    {"background": True, "assess_after": assess_after}
                )
            )
        )


def attach_source(session, row):
    """Discovery is provenance/work for a normal Source, never an approval system."""
    if row.source_id:
        return session.get(Source, row.source_id)
    source = Source(
        id=row.id,
        name=row.name,
        website_url=row.identity_url,
        source_type="publisher",
        approval_status="pending",
        enabled=False,
        submission_channel="api",
    )
    session.add(source)
    session.flush()
    row.source_id = source.id
    return source


def handoff(session, row):
    from devfeed_core.source_enrichment import request_enrichment

    source = attach_source(session, row)
    if source is None or source.approval_status != "pending" or not row.selected_feed_id:
        return
    feed = session.get(CandidateFeed, row.selected_feed_id)
    if feed is None:
        return
    existing = session.scalar(
        select(Source).where(Source.feed_url == feed.url, Source.id != source.id)
    )
    if existing:
        row.source_id = existing.id
        row.status = "linked"
        session.flush()
        session.delete(source)
        return
    source.feed_url = feed.url
    request_enrichment(session, source.id)


def import_publishers(
    hints: list[PublisherHint],
    origin: str,
    *,
    seed_id=None,
    checksum=None,
    background=False,
    assess_after=False,
):
    if not origin.strip() or len(origin) > 2048:
        raise ValueError("Provenance must contain 1-2048 characters")
    created = 0
    ids = []
    with session_factory().begin() as session:
        for hint in hints:
            new_id = session.scalar(
                insert(SourceCandidate)
                .values(identity_url=hint.homepage, name=hint.name)
                .on_conflict_do_nothing()
                .returning(SourceCandidate.id)
            )
            row = session.scalar(
                select(SourceCandidate).where(SourceCandidate.identity_url == hint.homepage)
            )
            assert row is not None
            attach_source(session, row)
            created += new_id is not None
            ids.append(str(row.id))
            key = hashlib.sha256((origin + "\n" + (hint.feed_hint or "")).encode()).hexdigest()
            session.execute(
                insert(CandidateDiscovery)
                .values(
                    candidate_id=row.id,
                    origin=origin,
                    origin_key=key,
                    feed_hint=hint.feed_hint,
                    seed_id=seed_id,
                    checksum=checksum,
                )
                .on_conflict_do_nothing()
            )
            if new_id is not None:
                enqueue(session, row.id, background=background, assess_after=assess_after)
            elif background:
                session.execute(
                    update(SourceDiscoveryJob)
                    .where(
                        SourceDiscoveryJob.candidate_id == row.id,
                        SourceDiscoveryJob.status == "queued",
                    )
                    .values(
                        result=SourceDiscoveryJob.result.op("||")(
                            {"background": True, "assess_after": assess_after}
                        )
                    )
                )
            if background and assess_after and row.status == "ready" and row.selected_feed_id:
                handoff(session, row)
    return {"created": created, "candidates": list(dict.fromkeys(ids))}


def list_candidates(status=None, limit=50, offset=0):
    with session_factory()() as session:
        query = select(SourceCandidate)
        if status:
            query = query.where(SourceCandidate.status == status)
        return [
            record(row)
            for row in session.scalars(
                query.order_by(SourceCandidate.created_at, SourceCandidate.id)
                .limit(limit)
                .offset(offset)
            )
        ]


def show(candidate_id):
    with session_factory()() as session:
        row = candidate(session, candidate_id)
        result = record(row)
        source = session.get(Source, row.source_id) if row.source_id else None
        if source:
            result["approval_status"] = source.approval_status
        models: list[tuple[str, Any]] = [
            ("provenance", CandidateDiscovery),
            ("feeds", CandidateFeed),
            ("assessments", CandidateAssessment),
            ("jobs", SourceDiscoveryJob),
        ]
        for key, model in models:
            result[key] = [
                record(row)
                for row in session.scalars(
                    select(model)
                    .where(model.candidate_id == candidate_id)
                    .order_by(
                        model.created_at.desc()
                        if hasattr(model, "created_at")
                        else model.checked_at.desc()
                    )
                    .limit(100)
                )
            ]
        return result


def require_idle(session, candidate_id):
    running = session.scalar(
        select(SourceDiscoveryJob.id)
        .where(
            SourceDiscoveryJob.candidate_id == candidate_id, SourceDiscoveryJob.status == "running"
        )
        .limit(1)
    )
    if running:
        raise OperationConflict("A discovery job is running; retry after it completes")


def request(
    candidate_id, stage="discover", *, reopen=False, actor=None, reason=None, background=False
):
    with session_factory().begin() as session:
        row = candidate(session, candidate_id, lock=True)
        require_idle(session, row.id)
        if row.status in {"admitted", "linked"}:
            raise OperationConflict("Candidate already linked to a source")
        if row.status == "rejected":
            if not (reopen and actor and reason):
                raise OperationConflict("Reopening rejection requires --reopen, --by and --reason")
            audit(session, row, "reopened", actor, reason)
            source = attach_source(session, row)
            source.approval_status = "pending"
            source.enabled = False
        if stage == "assess" and not row.selected_feed_id:
            raise OperationConflict("Select a discovered feed before assessment")
        if stage == "assess":
            from devfeed_core.source_enrichment import request_source_review

            source = attach_source(session, row)
            job = request_source_review(session, source)
            return {
                "candidate_id": candidate_id,
                "stage": "source-review",
                "queued": True,
                "job_id": job.id if job else None,
            }
        row.approval_status = "pending"
        row.status = "pending" if stage == "discover" else "ready"
        row.updated_at = utcnow()
        enqueue(session, row.id, stage, background=background)
        session.execute(
            update(SourceDiscoveryJob)
            .where(
                SourceDiscoveryJob.candidate_id == row.id,
                SourceDiscoveryJob.stage == stage,
                SourceDiscoveryJob.status == "queued",
            )
            .values(available_at=utcnow())
        )
    return {"candidate_id": candidate_id, "stage": stage, "queued": True}


def audit(session, row, decision, actor, reason):
    if (
        not actor
        or not actor.strip()
        or len(actor) > 200
        or not reason
        or not reason.strip()
        or len(reason) > 1000
    ):
        raise ValueError("Review requires an actor (1-200 chars) and reason (1-1000 chars)")
    row.review = {
        "decision": decision,
        "actor": actor,
        "reason": reason,
        "at": utcnow().isoformat(),
    }
    row.updated_at = utcnow()
    session.add(
        CandidateAssessment(candidate_id=row.id, version="manual-review-v1", evidence=row.review)
    )


def select_feed(candidate_id, feed_id):
    with session_factory().begin() as session:
        row = candidate(session, candidate_id, lock=True)
        require_idle(session, row.id)
        feed = session.get(CandidateFeed, feed_id)
        if row.status in {"rejected", "admitted", "linked"}:
            raise OperationConflict("Candidate is already reviewed")
        if feed is None or feed.candidate_id != row.id:
            raise RecordNotFound("Feed does not belong to candidate")
        row.selected_feed_id = feed.id
        row.status = "ready"
        handoff(session, row)
        row.updated_at = utcnow()
    return {"candidate_id": candidate_id, "selected_feed_id": feed_id}


def reject(candidate_id, actor, reason):
    with session_factory().begin() as session:
        row = candidate(session, candidate_id, lock=True)
        source = attach_source(session, row)
        review_source(
            session, source.id, SourceDecision(decision="rejected", actor=actor, note=reason)
        )
        row.status = row.approval_status = "rejected"
    return {"candidate_id": candidate_id, "status": "rejected"}


def approve(candidate_id, actor, reason):
    with session_factory().begin() as session:
        row = candidate(session, candidate_id, lock=True)
        source = attach_source(session, row)
        if row.status == "linked":
            return {"candidate_id": candidate_id, "source_id": source.id, "status": "linked"}
        review_source(
            session, source.id, SourceDecision(decision="approved", actor=actor, note=reason)
        )
        row.approval_status = "approved"
    return {"candidate_id": candidate_id, "source_id": source.id, "status": "approved"}


def run_one(*, allow_ai=False, assessor=None, on_start=None, target_job_id=None):
    # A transaction advisory lock serializes claims. The partial unique index also
    # enforces a single running crawler across CLI processes, including stale owners.
    from sqlalchemy import text

    with session_factory().begin() as session:
        session.execute(text("SELECT pg_advisory_xact_lock(74192381)"))
        session.execute(
            update(SourceDiscoveryJob)
            .where(
                SourceDiscoveryJob.status == "running", SourceDiscoveryJob.lease_until < utcnow()
            )
            .values(status="queued", lease_token=None)
        )
        if session.scalar(
            select(SourceDiscoveryJob.id).where(SourceDiscoveryJob.status == "running").limit(1)
        ):
            return None
        query = select(SourceDiscoveryJob).where(
            SourceDiscoveryJob.status == "queued", SourceDiscoveryJob.available_at <= utcnow()
        )
        if target_job_id is not None:
            query = query.where(SourceDiscoveryJob.id == target_job_id)
        if not allow_ai:
            query = query.where(SourceDiscoveryJob.stage == "discover")
        job = session.scalar(
            query.order_by(SourceDiscoveryJob.available_at, SourceDiscoveryJob.id)
            .limit(1)
            .with_for_update()
        )
        if job is None:
            return None
        # Background discovery deliveries may already be queued in Redis. Yield
        # their global slot to due AI work so a large import cannot starve review.
        if target_job_id is not None and job.stage == "discover" and get_settings().ai_enabled:
            assessment_waiting = session.scalar(
                select(SourceDiscoveryJob.id)
                .where(
                    SourceDiscoveryJob.stage == "assess",
                    SourceDiscoveryJob.status == "queued",
                    SourceDiscoveryJob.available_at <= utcnow(),
                    SourceDiscoveryJob.result["background"].as_boolean().is_(True),
                )
                .limit(1)
            )
            if assessment_waiting:
                return None
        row = session.scalar(
            select(SourceCandidate)
            .where(SourceCandidate.id == job.candidate_id)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return None
        if job.attempts >= 5:
            job.status, job.error, job.finished_at = "failed", "attempts_exhausted", utcnow()
            if row.status not in {"rejected", "admitted", "linked"}:
                row.status = "unresolved"
            return {"job_id": job.id, "status": "failed"}
        source = session.get(Source, row.source_id) if row.source_id else None
        if row.status in {"rejected", "admitted", "linked"} or (
            source and source.approval_status == "rejected"
        ):
            job.status, job.error, job.finished_at = "failed", "candidate_reviewed", utcnow()
            return {"job_id": job.id, "status": "failed"}
        job.status, job.lease_token = "running", uuid.uuid4()
        job.lease_until = utcnow() + timedelta(minutes=30)
        job.attempts += 1
        job_id, token, candidate_id, homepage, stage = (
            job.id,
            job.lease_token,
            row.id,
            row.identity_url,
            job.stage,
        )
        hints = list(
            session.scalars(
                select(CandidateDiscovery.feed_hint)
                .where(
                    CandidateDiscovery.candidate_id == row.id,
                    CandidateDiscovery.feed_hint.is_not(None),
                )
                .limit(5)
            )
        )
        selected = (
            session.get(CandidateFeed, row.selected_feed_id) if row.selected_feed_id else None
        )
        selected_id = selected.id if selected else None
        snapshot = selected.evidence if selected else None
        background = bool(job.result.get("background"))
        assess_after = bool(job.result.get("assess_after"))
    try:
        if on_start is not None:
            on_start(
                {
                    "candidate_id": candidate_id,
                    "name": row.name,
                    "stage": stage,
                    "attempt": job.attempts,
                }
            )
        if stage == "discover":
            result = discover(homepage, [hint for hint in hints if hint is not None])
        else:
            if snapshot is None or assessor is None:
                raise ValueError("Assessment requires a selected feed and configured AI")
            snapshot = assessment_evidence(snapshot)
            result = score(
                snapshot, assessor(snapshot["sample"]) if len(snapshot["sample"]) >= 3 else None
            )
        error = None
    except Exception as exc:
        from devfeed_core.feeds.fetcher import FeedError

        # Persist codes, never arbitrary exception payloads (may contain credentials).
        error = exc.reason if isinstance(exc, FeedError) else "assessment_or_discovery_failed"
        result = {
            "retryable": isinstance(exc, FeedError) and exc.retryable,
            "retry_after": exc.retry_after if isinstance(exc, FeedError) else 0,
        }
        if isinstance(exc, AnalysisError):
            transient = CAPACITY_ERRORS | {
                "codex_timeout",
                "codex_unavailable",
                "codex_request_failed",
                "codex_handshake_failed",
                "codex_transport_or_protocol_error",
            }
            code = str(exc)
            error = (
                code
                if code in transient
                or code
                in {
                    "ai_not_configured",
                    "invalid_analysis_output",
                    "invalid_json_response",
                    "unexpected_tool_execution",
                    "unexpected_server_request",
                    "analysis_input_too_large",
                }
                else "assessment_failed"
            )
            delay = max(0, min(86400, exc.retry_after))
            if code in CAPACITY_ERRORS:
                settings = get_settings()
                delay = max(
                    delay,
                    settings.ai_server_overload_cooldown_seconds
                    if code == "codex_server_overloaded"
                    else settings.ai_capacity_cooldown_seconds,
                )
            result = {"retryable": code in transient, "retry_after": delay}
    with session_factory().begin() as session:
        try:
            row = candidate(session, candidate_id, lock=True)
        except RecordNotFound:
            return {"job_id": job_id, "status": "deleted"}
        job = session.scalar(
            select(SourceDiscoveryJob).where(SourceDiscoveryJob.id == job_id).with_for_update()
        )
        assert job is not None
        if job.status != "running" or job.lease_token != token:
            return {"job_id": job_id, "status": "superseded"}
        source = session.get(Source, row.source_id) if row.source_id else None
        if row.status in {"rejected", "admitted", "linked"} or (
            source and source.approval_status == "rejected"
        ):
            job.status, job.error, job.lease_token, job.finished_at = (
                "failed",
                "candidate_reviewed",
                None,
                utcnow(),
            )
            return {"job_id": job_id, "status": "failed", "error": "candidate_reviewed"}
        result = {**result, "background": background, "assess_after": assess_after}
        job.result, job.error, job.lease_token, job.finished_at = result, error, None, utcnow()
        if result.get("retryable") and job.attempts < 5:
            job.status = "queued"
            job.available_at = utcnow() + timedelta(
                seconds=min(86400, max(result.get("retry_after", 0), 60 * 2**job.attempts))
            )
            row.status = "retry_wait"
        else:
            job.status = "failed" if error or result.get("retryable") else "succeeded"
            if result.get("retryable") and not error:
                job.error = "attempts_exhausted"
            if stage == "discover":
                valid_ids = []
                for item in sorted(
                    result.get("feeds", []), key=lambda feed: feed.get("format") != "atom"
                ):
                    feed_id = session.scalar(
                        insert(CandidateFeed)
                        .values(candidate_id=row.id, url=item["url"], evidence=item)
                        .on_conflict_do_update(
                            index_elements=["candidate_id", "url"],
                            set_={"evidence": item, "checked_at": utcnow()},
                        )
                        .returning(CandidateFeed.id)
                    )
                    valid_ids.append(feed_id)
                    session.add(
                        CandidateAssessment(
                            candidate_id=row.id,
                            feed_id=feed_id,
                            version=VERSION,
                            evidence=score(item),
                        )
                    )
                row.selected_feed_id = valid_ids[0] if valid_ids else None
                row.status = "ready" if row.selected_feed_id else "unresolved"
                if row.selected_feed_id:
                    handoff(session, row)
            elif not error:
                row.status = "ready"
                if row.selected_feed_id == selected_id:
                    session.add(
                        CandidateAssessment(
                            candidate_id=row.id,
                            feed_id=selected_id,
                            version=VERSION,
                            evidence=result,
                        )
                    )
                else:
                    job.status, job.error = "failed", "selected_feed_changed"
        if stage == "assess" and job.status in {"succeeded", "failed"}:
            row.status = "ready" if row.selected_feed_id else "unresolved"
        row.updated_at = utcnow()
        return {
            "job_id": job_id,
            "candidate_id": candidate_id,
            "status": job.status,
            "error": job.error,
            "candidate_status": row.status,
            "feeds_found": len(result.get("feeds", [])) if stage == "discover" else None,
        }


def delete_candidates(candidate_ids):
    """Delete import records atomically, preserving linked sources and active work."""
    ids = sorted(set(candidate_ids))
    with session_factory().begin() as session:
        for identifier in ids:
            candidate(session, identifier, lock=True)
            require_idle(session, identifier)
        session.execute(
            delete(CandidateAssessment).where(CandidateAssessment.candidate_id.in_(ids))
        )
        session.execute(delete(SourceCandidate).where(SourceCandidate.id.in_(ids)))
    return {"deleted": len(ids)}
