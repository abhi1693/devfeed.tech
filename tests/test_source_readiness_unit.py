"""Source readiness expresses processing facts without changing editorial authority."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from devfeed_core import source_lifecycle
from devfeed_core.config import Settings
from devfeed_core.models import (
    ArticleImageJob,
    IngestionJob,
    Source,
    SourceCandidate,
    SourceDiscoveryJob,
    SourceEnrichmentJob,
)
from devfeed_core.source_enrichment import source_identity
from devfeed_core.source_lifecycle import (
    SourceLifecycleFacts,
    assessment_current,
    derive_source_readiness,
    source_snapshot_token,
)
from devfeed_core.source_relevance import VERSION

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)


def configuration(**changes):
    return Settings(
        _env_file=None,
        **{
            "database_url": "postgresql+psycopg://unit@database.invalid/unit_test",
            "redis_url": "redis://redis.invalid/15",
            "codex_app_server_url": "ws://127.0.0.1:4500",
            "codex_model": "test-model",
            "ai_enabled": False,
            "full_automation": False,
            "image_storage_enabled": False,
            "image_public_url": None,
            **changes,
        },
    )


def source(*, approval="pending", **changes):
    return Source(
        **{
            "id": uuid.uuid4(),
            "name": "Engineering publisher",
            "slug": "engineering-publisher",
            "feed_url": "https://publisher.example/feed",
            "website_url": "https://publisher.example/",
            "source_type": "publisher",
            "approval_status": approval,
            "enabled": approval == "approved",
            "processing_request": {},
            "relevance_assessment": {},
            "managed_logo": {},
            "last_success_at": NOW - timedelta(days=1) if approval == "approved" else None,
            "created_at": NOW - timedelta(days=2),
            "updated_at": NOW - timedelta(days=1),
            **changes,
        }
    )


def job(model, *, status="queued", **changes):
    return model(
        **{
            "id": uuid.uuid4(),
            "status": status,
            "attempts": 0 if status == "queued" else 1,
            "available_at": NOW - timedelta(minutes=10),
            "created_at": NOW - timedelta(hours=1),
            "finished_at": NOW - timedelta(minutes=1)
            if status in {"failed", "succeeded"}
            else None,
            "lease_token": uuid.uuid4() if status == "running" else None,
            "lease_until": NOW + timedelta(minutes=5) if status == "running" else None,
            "error": "Upstream unavailable" if status == "failed" else None,
            **changes,
        }
    )


def profile(current, *, status="succeeded", **changes):
    return job(
        SourceEnrichmentJob,
        status=status,
        **{
            "source_id": current.id,
            "operation": "profile",
            "result": {"input": source_identity(current), "sample": []},
            **changes,
        },
    )


def assessment(current, current_profile, *, status="succeeded", **changes):
    output = {
        "version": VERSION,
        "feed_url": current.feed_url,
        "source_type": current.source_type,
        "relevance": "uncertain",
        "reason": "The feed sample does not establish a supported editorial decision.",
        "approval_supported": False,
        "rejection_supported": False,
    }
    if status == "succeeded":
        current.relevance_assessment = output
    return job(
        SourceEnrichmentJob,
        status=status,
        **{
            "source_id": current.id,
            "operation": "relevance",
            "result": {
                "input": source_identity(current),
                "profile_job_id": str(current_profile.id),
                "policy_version": VERSION,
                **({"output": output} if status == "succeeded" else {}),
            },
            **changes,
        },
    )


def readiness(current, *, jobs=None, **settings):
    return derive_source_readiness(current, jobs=jobs, settings=configuration(**settings), now=NOW)


def usable_logo(current):
    current.logo_url = "https://publisher.example/logo.png"
    current.managed_logo = {
        "source_url": current.logo_url,
        "version": "v1",
        "variants": [{"key": f"logos/{width}.webp", "width": width} for width in (16, 32, 64, 96)],
    }


def test_completed_profile_offers_review_without_granting_approval_or_ingestion():
    current = source()
    result = readiness(current, jobs={"profile": profile(current)})
    assert (result.stage, result.state, result.next_action) == ("review", "ready", None)
    assert result.can_approve and not result.can_ingest
    assert result.checks["assessment"].status == "disabled"
    assert current.approval_status == "pending" and not current.enabled


@pytest.mark.parametrize(
    "status,delayed,state",
    [("queued", False, "waiting"), ("running", False, "running"), ("queued", True, "retry_wait")],
)
def test_pending_profile_work_exposes_progress_without_duplicate_retries(status, delayed, state):
    current = source()
    active = profile(current, status=status)
    if delayed:
        active.attempts, active.available_at = 1, NOW + timedelta(minutes=2)
    result = readiness(current, jobs={"profile": active}, ai_enabled=True)
    assert (result.stage, result.state, result.next_action) == ("profile", state, None)
    assert result.checks["profile"].job_id == active.id
    assert result.checks["profile"].available_at == active.available_at
    assert not {"profile", "assessment"}.intersection(result.retry_targets)
    assert not result.can_approve and not result.can_ingest


def test_missing_profile_is_actionable_without_claiming_metadata_is_complete():
    result = readiness(source())
    assert (result.stage, result.state, result.next_action) == ("profile", "waiting", "profile")
    assert result.blockers == ["profile_missing"] and result.retry_targets == ["profile"]
    assert not result.can_approve and not result.can_ingest


@pytest.mark.parametrize("input_changed", [False, True])
def test_terminal_profile_failure_requires_retry_but_changed_input_is_new_work(input_changed):
    current = source()
    failed = profile(current, status="failed")
    if input_changed:
        current.website_url = "https://publisher.example/engineering/"
    result = readiness(current, jobs={"profile": failed})
    assert result.checks["profile"].status == ("stale" if input_changed else "failed")
    assert result.next_action == ("profile" if input_changed else None)
    assert result.state == "blocked" and not result.can_approve
    assert result.checks["profile"].error == "Upstream unavailable"


@pytest.mark.parametrize("automatic", [False, True])
def test_pending_source_assessment_is_required_by_automation_but_manual_review_stays_available(
    automatic,
):
    current = source()
    result = readiness(
        current, jobs={"profile": profile(current)}, ai_enabled=True, full_automation=automatic
    )
    assert result.can_approve and not result.can_ingest
    assert (result.stage, result.next_action) == (
        ("assessment", "assessment") if automatic else ("review", None)
    )
    assert current.approval_status == "pending" and not current.enabled


@pytest.mark.parametrize("changed", ["input", "profile", "policy", "output"])
def test_assessment_admission_rejects_evidence_for_other_inputs_or_policy(changed):
    current = source()
    inspected = profile(current)
    reviewed = assessment(current, inspected)
    assert assessment_current(current, inspected, reviewed)
    if changed == "input":
        current.website_url = "https://publisher.example/changed/"
    elif changed == "profile":
        inspected = profile(current)
    elif changed == "policy":
        reviewed.result = {**reviewed.result, "policy_version": "superseded-policy"}
    else:
        current.relevance_assessment = {**current.relevance_assessment, "reason": "Edited review"}
    assert not assessment_current(current, inspected, reviewed)


@pytest.mark.parametrize("changed", [False, True])
def test_failed_current_assessment_is_not_automatically_restarted_but_stale_binding_is_actionable(
    changed,
):
    current = source()
    inspected = profile(current)
    failed = assessment(current, inspected, status="failed")
    if changed:
        inspected = profile(current)
    result = readiness(
        current,
        jobs={"profile": inspected, "assessment": failed},
        full_automation=True,
    )
    assert result.checks["assessment"].status == ("stale" if changed else "failed")
    assert result.stage == "assessment" and result.state == "blocked"
    assert result.next_action == ("assessment" if changed else None)
    assert "assessment" in result.retry_targets


@pytest.mark.parametrize("changed", [None, "version", "feed_url", "source_type"])
def test_legacy_assessment_remains_valid_only_for_its_original_identity_and_policy(changed):
    current = source(approval="approved")
    legacy = profile(current, operation="legacy", result={})
    current.relevance_assessment = {
        "version": VERSION,
        "feed_url": current.feed_url,
        "source_type": current.source_type,
    }
    if changed:
        previous = {
            "version": "previous-policy",
            "feed_url": "https://publisher.example/previous-feed",
            "source_type": "aggregator",
        }
        current.relevance_assessment = {**current.relevance_assessment, changed: previous[changed]}
    assert assessment_current(current, legacy, None) is (changed is None)
    result = readiness(current, jobs={"profile": legacy}, ai_enabled=True)
    assert result.stage == "active" and result.can_ingest
    assert "assessment" not in result.retry_targets


def test_unbound_legacy_profile_cannot_admit_a_pending_source():
    current = source()
    legacy = profile(current, operation="legacy", result={})
    result = readiness(current, jobs={"profile": legacy})
    assert result.stage == "profile" and result.checks["profile"].status == "stale"
    assert result.next_action == "profile" and not result.can_approve


def test_reopening_requires_a_profile_created_after_the_explicit_review_request():
    current = source(
        processing_request={
            "kind": "reopen_review",
            "requested_at": (NOW - timedelta(minutes=10)).isoformat(),
            "manual_review": True,
            "required": ["profile", "branding"],
        }
    )
    before_reopening = readiness(current, jobs={"profile": profile(current)})
    assert before_reopening.checks["profile"].status == "stale"
    assert before_reopening.next_action == "profile" and not before_reopening.can_approve
    fresh = profile(
        current,
        created_at=NOW - timedelta(minutes=5),
        available_at=NOW - timedelta(minutes=5),
    )
    after_reinspection = readiness(current, jobs={"profile": fresh})
    assert after_reinspection.stage == "review" and after_reinspection.can_approve
    assert current.approval_status == "pending" and not current.enabled
    assert current.processing_request["manual_review"] is True


@pytest.mark.parametrize("status", [None, "failed", "queued"])
def test_ordinary_profile_refresh_never_revokes_existing_approved_polling(status):
    current = source(approval="approved", logo_url="https://publisher.example/logo.png")
    jobs = {"profile": profile(current, status=status)} if status else {}
    result = readiness(current, jobs=jobs, full_automation=True, image_storage_enabled=True)
    assert (result.stage, result.state, result.next_action) == ("active", "complete", None)
    assert result.can_ingest and not result.can_approve and result.blockers == []
    assert current.enabled and current.approval_status == "approved"
    assert current.processing_request == {}


def test_ordinary_pause_can_resume_without_backfilling_profile_or_assessment():
    result = readiness(source(approval="approved", enabled=False), full_automation=True)
    assert (result.stage, result.state, result.next_action) == ("paused", "complete", "resume")
    assert not result.can_ingest and not result.can_approve


def test_requested_assessment_stays_required_after_profile_replacement_until_explicit_resume():
    current = source(
        approval="approved",
        enabled=False,
        processing_request={
            "kind": "upstream_retry",
            "required": ["profile", "assessment", "branding"],
        },
    )
    inspected = profile(current)
    result = readiness(current, jobs={"profile": inspected}, ai_enabled=True)
    assert (result.stage, result.next_action) == ("assessment", "assessment")
    assert not result.can_ingest
    reviewed = assessment(current, inspected)
    complete = readiness(
        current, jobs={"profile": inspected, "assessment": reviewed}, ai_enabled=True
    )
    assert (complete.stage, complete.state, complete.next_action) == (
        "paused",
        "complete",
        "resume",
    )
    assert not current.enabled and current.processing_request["required"]


@pytest.mark.parametrize(
    "status,delayed,stage,state,can_ingest",
    [
        ("queued", False, "ingestion", "waiting", False),
        ("running", False, "ingestion", "running", False),
        ("queued", True, "ingestion", "retry_wait", False),
        ("failed", False, "ingestion", "blocked", True),
        ("succeeded", False, "active", "complete", True),
    ],
)
def test_ingestion_progress_and_retry_eligibility_respect_existing_delivery(
    status, delayed, stage, state, can_ingest
):
    current = source(approval="approved")
    delivery = job(IngestionJob, source_id=current.id, status=status)
    if delayed:
        delivery.attempts, delivery.available_at = 1, NOW + timedelta(minutes=2)
    result = readiness(current, jobs={"ingestion": delivery})
    assert (result.stage, result.state) == (stage, state)
    assert result.can_ingest is can_ingest
    assert ("ingestion" in result.retry_targets) is can_ingest
    assert result.blockers == (["ingestion_failed"] if status == "failed" else [])


def test_approved_source_without_any_completed_fetch_offers_initial_ingestion():
    result = readiness(source(approval="approved", last_success_at=None))
    assert (result.stage, result.state, result.next_action) == ("ingestion", "waiting", "ingestion")
    assert result.can_ingest


@pytest.mark.parametrize("complete", [False, True])
def test_rejected_source_exposes_only_editorial_reopen_or_explicit_reapproval(complete):
    current = source(approval="rejected")
    result = readiness(current, jobs={"profile": profile(current)} if complete else {})
    assert (result.stage, result.state) == ("rejected", "complete")
    assert result.can_approve is complete and not result.can_ingest
    assert result.next_action == (None if complete else "reopen")
    assert result.retry_targets == [] and current.approval_status == "rejected"


def test_rejected_website_requires_reopening_but_standalone_candidate_cannot_reopen_itself():
    rejected = source(approval="rejected", feed_url=None)
    result = readiness(rejected)
    assert result.next_action == "reopen" and not result.can_approve
    candidate = SourceCandidate(
        id=uuid.uuid4(),
        name="Publisher",
        identity_url=rejected.website_url,
        status="rejected",
        approval_status="rejected",
    )
    result = derive_source_readiness(
        None, candidates=[candidate], settings=configuration(), now=NOW
    )
    assert (result.stage, result.state, result.next_action) == ("rejected", "complete", None)
    assert result.retry_targets == []


@pytest.mark.parametrize(
    "status,delayed,state",
    [
        ("queued", False, "waiting"),
        ("running", False, "running"),
        ("queued", True, "retry_wait"),
        ("failed", False, "blocked"),
        ("succeeded", False, "blocked"),
    ],
)
def test_discovery_progress_does_not_imply_a_usable_feed(status, delayed, state):
    candidate = SourceCandidate(
        id=uuid.uuid4(),
        name="Publisher",
        identity_url="https://publisher.example/",
        status="unresolved" if status in {"failed", "succeeded"} else "pending",
        approval_status="pending",
    )
    discovery = job(
        SourceDiscoveryJob, candidate_id=candidate.id, stage="discover", result={}, status=status
    )
    if delayed:
        candidate.status = "retry_wait"
        discovery.attempts, discovery.available_at = 1, NOW + timedelta(minutes=2)
    result = derive_source_readiness(
        None,
        candidates=[candidate],
        jobs={"discovery": discovery},
        settings=configuration(),
        now=NOW,
    )
    assert (result.stage, result.state, result.next_action) == ("discovery", state, None)
    assert result.blockers == (["feed_discovery_unresolved"] if state == "blocked" else [])
    assert result.retry_targets == (["discovery"] if state == "blocked" else [])
    assert not result.can_approve and not result.can_ingest


def test_discovery_needs_work_until_selected_feed_can_enter_profile_processing():
    candidate = SourceCandidate(
        id=uuid.uuid4(),
        name="Publisher",
        identity_url="https://publisher.example/",
        status="pending",
    )
    missing = derive_source_readiness(
        None, candidates=[candidate], settings=configuration(), now=NOW
    )
    assert missing.next_action == "discovery"
    candidate.selected_feed_id, candidate.status = uuid.uuid4(), "ready"
    selected = derive_source_readiness(
        None, candidates=[candidate], settings=configuration(), now=NOW
    )
    assert selected.next_action == "profile" and not selected.can_approve


@pytest.mark.parametrize("status", ["queued", "running", "failed"])
def test_logo_refresh_must_settle_even_when_a_previous_asset_is_usable(status):
    current = source()
    usable_logo(current)
    branding = job(
        ArticleImageJob,
        source_id=current.id,
        status=status,
        operation="source-logo-refresh",
        image_url=current.logo_url,
    )
    result = readiness(
        current,
        jobs={"profile": profile(current), "branding": branding},
        image_storage_enabled=True,
        image_public_url="https://images.example",
    )
    assert result.stage == "branding" and not result.can_approve
    assert result.state == {"queued": "waiting", "running": "running", "failed": "blocked"}[status]
    assert ("branding" in result.retry_targets) is (status == "failed")
    assert result.next_action is None and current.managed_logo["variants"]


def test_current_managed_logo_is_usable_after_terminal_job_history_cleanup():
    current = source()
    usable_logo(current)
    result = readiness(
        current,
        jobs={"profile": profile(current)},
        image_storage_enabled=True,
        image_public_url="https://images.example",
    )
    assert result.checks["branding"].status == "complete"
    assert result.stage == "review" and result.can_approve


@pytest.mark.parametrize("old_asset", [False, True])
def test_missing_logo_work_and_completed_old_input_remain_actionable(old_asset):
    current = source(logo_url="https://publisher.example/new-logo.png")
    jobs = {"profile": profile(current)}
    if old_asset:
        jobs["branding"] = job(
            ArticleImageJob,
            source_id=current.id,
            status="succeeded",
            operation="source-logo",
            image_url="https://publisher.example/old-logo.png",
        )
    result = readiness(current, jobs=jobs, image_storage_enabled=True)
    assert result.checks["branding"].status == ("stale" if old_asset else "missing")
    assert result.next_action == "branding" and not result.can_approve


@pytest.mark.parametrize("active", [False, True])
def test_disabling_image_storage_allows_settled_fallback_but_does_not_hide_active_refresh(active):
    current = source(logo_url="https://publisher.example/logo.png")
    branding = job(
        ArticleImageJob,
        source_id=current.id,
        status="running" if active else "failed",
        operation="source-logo-refresh",
        image_url=current.logo_url,
    )
    result = readiness(current, jobs={"profile": profile(current), "branding": branding})
    assert result.can_approve is not active
    assert result.stage == ("branding" if active else "review")


def test_active_assessment_suppresses_profile_retries_without_blocking_independent_branding():
    current = source(logo_url="https://publisher.example/logo.png")
    inspected = profile(current)
    result = readiness(
        current,
        jobs={"profile": inspected, "assessment": assessment(current, inspected, status="running")},
        full_automation=True,
        image_storage_enabled=True,
    )
    assert not {"profile", "assessment"}.intersection(result.retry_targets)
    assert result.retry_targets == ["branding"]


def test_facts_select_candidate_owned_by_discovery_and_fall_back_without_delivery():
    candidates = [
        SourceCandidate(
            id=uuid.uuid4(),
            name=f"Publisher {index}",
            identity_url=f"https://publisher.example/{index}",
            status="pending",
            approval_status="pending",
        )
        for index in range(2)
    ]
    facts = SourceLifecycleFacts(key="candidate", source=None, candidates=candidates)
    assert facts.candidate is candidates[0]
    facts.jobs["discovery"] = job(SourceDiscoveryJob, candidate_id=candidates[1].id)
    assert facts.candidate is candidates[1]
    assert SourceLifecycleFacts(key="empty", source=None, candidates=[]).candidate is None


@pytest.mark.parametrize("change", ["pause", "candidate", "lease", "policy"])
def test_snapshot_is_order_independent_but_changes_when_reviewed_facts_change(monkeypatch, change):
    current = source(approval="approved")
    candidates = [
        SourceCandidate(
            id=uuid.uuid4(),
            name=f"Publisher {index}",
            identity_url=f"https://publisher.example/{index}",
            source_id=current.id,
            status="pending",
            approval_status="pending",
        )
        for index in range(2)
    ]
    discovery = job(SourceDiscoveryJob, candidate_id=candidates[0].id)
    facts = SourceLifecycleFacts(
        key=f"source:{current.id}",
        source=current,
        candidates=candidates,
        jobs={"discovery": discovery, "profile": None},
    )
    settings = configuration(ai_enabled=True)
    monkeypatch.setattr(source_lifecycle, "get_settings", lambda: settings)
    before = source_snapshot_token(facts)
    facts.candidates = list(reversed(candidates))
    assert source_snapshot_token(facts) == before
    if change == "pause":
        current.enabled = False
    elif change == "candidate":
        candidates[0].review = {"note": "Inspect the engineering section"}
    elif change == "lease":
        discovery.status, discovery.attempts = "running", 1
        discovery.lease_token, discovery.lease_until = uuid.uuid4(), NOW + timedelta(minutes=5)
    else:
        settings.full_automation = True
    assert source_snapshot_token(facts) != before
    assert len(before) == 64
    assert current.approval_status == "approved"
