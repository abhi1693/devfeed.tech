"""Human-readable rendering of already-sanitized logging payloads."""

import json
import re
from datetime import datetime

_MESSAGES = {
    "taxonomy_auto_approval_blocked": (
        "Automatic approval checks did not pass; see the research run for the reason"
    ),
    "notification_delivery_started": "Delivering notification",
    "notification_delivery_succeeded": "Notification delivered",
    "notification_delivery_failed": (
        "Notification delivery failed; see the delivery run for retry status"
    ),
    "notification_runtime_failed": "Notification delivery worker crashed",
    "notification_lease_lost": "Notification ownership changed; this worker stopped",
    "notification_dispatched": "Notification sent to the common worker queue",
    "job_execution_started": "Worker received job",
    "job_execution_finished": "Worker finished handling job",
    "job_log_storage_unavailable": (
        "Job log storage unavailable; console logging continues (retry in 30s)"
    ),
    "rq_work_horse_killed": "Worker process exited unexpectedly; job may need retrying",
    "article_analysis_not_claimed": "Analysis already handled or not ready",
    "article_analysis_skipped": "Article analysis skipped",
    "article_analysis_lease_lost": "Analysis ownership changed; this worker stopped",
    "source_enrichment_not_claimed": "Source profile job already handled or not ready",
    "article_editorial_decision": "Article editorial decision recorded",
    "article_analysis_started": "Analyzing article with Codex",
    "article_analysis_completed": "Article analysis completed; see its publication decision",
    "article_analysis_failed": "Article analysis failed; see the saved job for its reason",
    "article_analysis_runtime_failed": "Article analysis worker crashed",
    "article_analysis_dispatched": "Article analysis sent to the AI worker queue",
    "article_enrichment_started": "Fetching original article",
    "article_enrichment_failed": "Original article lookup failed",
    "article_enrichment_runtime_failed": "Article enrichment worker crashed",
    "article_enrichment_dispatched": "Article enrichment job sent to the worker queue",
    "article_enrichment_dispatch_failed": (
        "Could not dispatch article lookup; saved job remains queued"
    ),
    "article_enrichment_not_claimed": "Article lookup already handled or not ready",
    "article_enrichment_lease_lost": "Article lookup ownership changed; this worker stopped",
    "article_enrichment_lease_recovered": "Interrupted article lookup recovered",
    "cache_unavailable": "Response cache unavailable; using the database (cache retry in 5s)",
    "cache_entry_invalid": "Invalid cached response ignored",
    "cache_invalidated": "User response cache invalidated after commit",
    "ingestion_source_unapproved": "Ingestion stopped because the source is no longer approved",
    "source_enrichment_started": "Looking up source profile",
    "source_enrichment_completed": "Source profile enriched",
    "source_enrichment_dispatched": "Source profile job sent to the worker queue",
    "source_enrichment_runtime_failed": "Source profile worker crashed",
    "source_enrichment_lease_lost": "Source profile job ownership changed",
    "source_enrichment_lease_recovered": "Interrupted source profile lookup recovered",
    "source_enrichment_dispatch_failed": (
        "Could not dispatch source profile lookup; saved job remains queued"
    ),
    "image_lookup_started": "Looking for article image",
    "image_lookup_failed": "Image lookup failed",
    "image_runtime_failed": "Image worker crashed",
    "image_not_claimed": "Image job already handled or not ready",
    "image_dispatched": "Image job sent to the worker queue",
    "image_lease_lost": "Image job ownership changed; this worker stopped processing it",
    "image_lease_recovered": "Interrupted image lookup recovered",
    "image_immediate_dispatch_failed": "Could not dispatch image lookup; saved job remains queued",
    "api_started": "API started",
    "api_stopped": "API stopped",
    "admin_api_started": "Admin API started",
    "admin_api_stopped": "Admin API stopped",
    "admin_login_unavailable": "Admin sign-in unavailable; check provider configuration and Redis",
    "admin_login_failed": "Admin sign-in rejected or not completed",
    "admin_signed_in": "Administrator signed in",
    "admin_signed_out": "Administrator signed out",
    "admin_database_unavailable": "Admin database unavailable; check connection and migrations",
    "worker_started": "Worker started",
    "worker_stopped": "Worker stopped",
    "worker_runtime_failed": "Worker failed",
    "analysis_queue_paused": "AI analysis paused; waiting for Codex readiness",
    "analysis_queue_resumed": "Codex is ready; AI analysis resumed",
    "scheduler_started": "Scheduler started",
    "scheduler_stopped": "Scheduler stopped",
    "scheduler_tick_failed": "Scheduler tick failed",
    "source_created": "Source added",
    "source_preview_completed": "Source details fetched; nothing saved",
    "source_preview_partial": "Feed checked; website details unavailable",
    "source_submitted": "Source submitted for review",
    "source_updated": "Source updated",
    "source_refresh_requested": "Feed refresh queued",
    "tag_created": "Tag added",
    "tag_updated": "Tag updated",
    "ingestion_started": "Fetching feed",
    "ingestion_scheduled": "Feed refresh scheduled",
    "ingestion_dispatched": "Job sent to the worker queue",
    "ingestion_immediate_dispatch_requested": "Dispatching queued job now",
    "ingestion_immediate_dispatch_coalesced": "Job already picked up by the scheduler or a worker",
    "ingestion_immediate_dispatch_failed": "Could not dispatch job; saved job remains queued",
    "ingestion_attempt_failed": "Feed fetch failed",
    "ingestion_failed": "Ingestion failed; source polling backed off",
    "ingestion_runtime_failed": "Ingestion crashed",
    "ingestion_lease_lost": "Job ownership changed; this worker stopped processing it",
    "ingestion_lease_recovered": "Interrupted job recovered",
    "ingestion_source_disabled": "Ingestion stopped because the source was disabled",
    "ingestion_not_claimed": "Job already handled or not ready",
    "rq_job_failed": "Worker job failed",
    "feed_validation_started": "Checking RSS/Atom feed",
    "feed_validation_error": "Feed validation crashed",
    "request_failed": "Request failed",
    "request_conflict": "Request conflicts with an existing record",
    "request_validation_failed": "Request validation failed",
    "readiness_failed": "Dependency readiness check failed",
    "status_dependency_failed": "Dependency status unavailable",
    "ingestion_status_unavailable": "Queue status unavailable",
    "command_started": "Command started",
    "command_succeeded": "Command succeeded",
    "command_completed": "Command finished",
    "command_rejected": "Command rejected",
    "command_conflict": "Command conflicts with an existing record",
    "command_failed": "Command failed",
    "command_interrupted": "Command interrupted",
    "command_dependencies_unavailable": "Required services are unavailable",
    "database_operation_failed": "Database operation failed",
    "redis_operation_failed": "Redis is unavailable",
    "migration_failed": "Migration command failed",
    "command_io_failed": "Could not read the requested file or access a service",
    "migration_context_started": "Applying or inspecting migrations",
    "migration_context_completed": "Migration operation finished",
    "migration_context_failed": "Migration operation failed",
}

_REASONS = {
    "page_unavailable": "page is a login, error or browser challenge",
    "unsupported_content_type": "linked resource is not an HTML page",
    "unreadable_feed": "response is not readable RSS or Atom",
    "unusable_entries": "no entries have a usable title and URL",
    "missing_body": "server did not return a complete feed",
    "private_address": "hostname resolves to a private or reserved address",
    "deadline_exceeded": "download timed out",
    "transport_error": "could not connect to or read from the server",
    "response_too_large": "response exceeds the size limit",
    "unsafe_url_or_encoding": "URL or response encoding was rejected",
    "invalid_redirect": "invalid redirect",
    "https_downgrade": "redirect from HTTPS to HTTP was blocked",
    "too_many_redirects": "too many redirects",
    "unsupported_encoding": "unsupported response encoding",
    "invalid_gzip": "invalid or incomplete compressed response",
}


def inline(value) -> str:
    """Keep values on one terminal line, without quotes around ordinary text."""
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=True)[1:-1]
    return json.dumps(value, ensure_ascii=True, allow_nan=False)


def short_id(value) -> str:
    value = str(value)
    if re.fullmatch(r"[0-9a-f-]{32,64}", value):
        return value[:8]
    return inline(value)[:40]


def local_time(value: str) -> str:
    return datetime.fromisoformat(value).astimezone().strftime("%H:%M:%S")


def reason_text(payload: dict) -> str:
    status = payload.get("upstream_status")
    if status:
        label = {
            401: "authentication required",
            403: "access denied",
            404: "not found",
            429: "rate limited",
        }.get(status)
        return f"HTTP {status}" + (f" ({label})" if label else "")
    reason = payload.get("reason")
    if reason:
        return _REASONS.get(reason, inline(reason).replace("_", " "))
    return ""


def event_text(payload: dict) -> str:
    event = payload["event"]
    if event == "article_enrichment_completed":
        return {
            "enriched": "Original article metadata enriched",
            "metadata_only": "Article image found; no readable article text",
            "not_found": "No readable article text or preview metadata found",
            "superseded": "Kept newer publisher metadata; page lookup discarded",
            "unapproved": "Article lookup stopped; no approved source remains",
        }.get(payload.get("outcome", ""), "Article lookup completed")
    if event == "article_enrichment_retry_scheduled":
        when = payload.get("available_at")
        return (
            "Original article lookup failed"
            + (f": {reason_text(payload)}" if reason_text(payload) else "")
            + (f"; retry at {local_time(when)}" if when else "; retry scheduled")
        )
    if event in {"article_languages_detected", "article_languages_backfilled"}:
        message = (
            f"Article languages: {payload.get('languages_detected', 0)} detected, "
            f"{payload.get('languages_unknown', 0)} unknown"
        )
        if event == "article_languages_backfilled":
            message += (
                " (preview; no changes)"
                if payload.get("dry_run")
                else f"; {payload.get('articles_updated', 0)} updated"
            )
        return message
    if event == "source_enrichment_failed":
        when = payload.get("available_at")
        return (
            "Source profile lookup failed"
            + (f": {reason_text(payload)}" if reason_text(payload) else "")
            + (
                f"; retry at {local_time(when)}"
                if when and payload.get("job_status") == "queued"
                else ""
            )
        )
    if event == "image_lookup_completed":
        return {
            "found": "Article image found",
            "not_found": "No article image metadata found",
            "already_present": "Article already has an image; kept it",
        }.get(payload.get("outcome", ""), "Image lookup finished")
    if event == "image_retry_scheduled":
        when = payload.get("available_at")
        return (
            "Image lookup failed"
            + (f": {reason_text(payload)}" if reason_text(payload) else "")
            + (f"; retry at {local_time(when)}" if when else "; retry scheduled")
        )
    if event == "request_completed":
        return (
            f"{inline(payload.get('method', 'HTTP'))} "
            f"{inline(payload.get('request_url', payload.get('route', '<unmatched>')))} "
            f"-> {payload.get('status_code', '?')}"
            + (
                f" [cache {inline(payload['cache_status']).lower()}"
                + (
                    f": {inline(payload['cache_bypass_reason'])}"
                    if payload.get("cache_bypass_reason")
                    else ""
                )
                + "]"
                if payload.get("cache_status")
                else ""
            )
        )
    if event == "feed_validation_succeeded":
        return (
            f"Feed validated: {payload.get('entries_seen', 0)} entries, "
            f"{payload.get('entries_skipped', 0)} skipped"
        )
    if event == "feed_validation_failed":
        return "Feed validation failed" + (
            f": {reason_text(payload)}" if reason_text(payload) else ""
        )
    if event == "ingestion_succeeded":
        if payload.get("upstream_status") == 304:
            return "Feed unchanged (HTTP 304)"
        return (
            f"Ingested {payload.get('entries_seen', 0)} entries: "
            f"{payload.get('articles_created', 0)} new, "
            f"{payload.get('entries_skipped', 0)} skipped"
        )
    if event == "scheduler_tick_completed":
        message = (
            f"Scheduler: {payload.get('scheduled', 0)} scheduled, "
            f"{payload.get('dispatched', 0)} dispatched, "
            f"{payload.get('recovered', 0)} recovered"
        )
        if payload.get("tags_scanned"):
            message += (
                f"; tags: {payload['tags_scanned']} checked, "
                f"{payload.get('tags_linked', 0)} linked, "
                f"{payload.get('tags_unlinked', 0)} unlinked, "
                f"{payload.get('tags_ambiguous', 0)} ambiguous"
            )
        if payload.get("relationship_jobs_scheduled") or payload.get(
            "relationship_scans_completed"
        ):
            message += (
                f"; relationships: {payload.get('relationship_jobs_scheduled', 0)} scheduled, "
                f"{payload.get('relationship_scans_completed', 0)} scans completed"
            )
        if payload.get("images_dispatched") or payload.get("images_recovered"):
            message += (
                f"; images: {payload.get('images_dispatched', 0)} dispatched, "
                f"{payload.get('images_recovered', 0)} recovered"
            )
        if payload.get("profiles_dispatched") or payload.get("profiles_recovered"):
            message += (
                f"; profiles: {payload.get('profiles_dispatched', 0)} dispatched, "
                f"{payload.get('profiles_recovered', 0)} recovered"
            )
        if payload.get("articles_dispatched") or payload.get("articles_recovered"):
            message += (
                f"; articles: {payload.get('articles_dispatched', 0)} dispatched, "
                f"{payload.get('articles_recovered', 0)} recovered"
            )
        return message
    if event == "ingestion_retry_scheduled":
        when = payload.get("available_at")
        return "Retry scheduled" + (f" for {local_time(when)}" if when else "")
    if event == "dependency_log":
        return f"{inline(payload['logger'])}: {payload['level'].lower()} (library message omitted)"
    message = _MESSAGES.get(event, event.replace("_", " ").capitalize())
    if payload.get("reason") or payload.get("upstream_status"):
        message += f": {reason_text(payload)}"
    return message


def error_text(payload: dict, *, verbose: bool) -> str:
    exceptions = payload.get("exception", [])
    if not exceptions:
        return inline(payload["error_type"]) if payload.get("error_type") else ""
    parts = []
    for index, detail in enumerate(exceptions):
        description = inline(detail["type"])
        if detail.get("attribute"):
            owner = detail.get("object_type", "object")
            description += f": {inline(owner)}.{inline(detail['attribute'])} is missing"
        frames = detail["frames"] if verbose else detail["frames"][-1:]
        if frames:
            locations = [
                f"{inline(frame['file'])}:{frame['line']}"
                + (f" in {inline(frame['function'])}" if verbose else "")
                for frame in frames
            ]
            description += " at " + " -> ".join(locations)
        parts.append(("caused by " if index else "") + description)
    return "; ".join(parts)


def context_text(payload: dict, *, verbose: bool) -> list[str]:
    if verbose:
        return [
            f"{key}={inline(value)}"
            for key, value in payload.items()
            if key not in {"timestamp", "level", "service", "exception"} and value is not None
        ]
    parts = []
    if payload.get("request_url") and payload["event"] != "request_completed":
        parts.append(f"{inline(payload.get('method', 'HTTP'))} {inline(payload['request_url'])}")
    for key, label in [
        ("job_id", "job"),
        ("source_id", "source"),
        ("article_id", "article"),
        ("category_id", "category"),
        ("tag_id", "tag"),
    ]:
        if payload.get(key):
            parts.append(f"{label} {short_id(payload[key])}")
            break
    if payload.get("attempt"):
        parts.append(f"attempt {payload['attempt']}")
    if payload.get("dependency"):
        parts.append(inline(payload["dependency"]))
    if payload.get("duration_ms") is not None:
        duration = payload["duration_ms"]
        parts.append(f"{duration:.0f} ms" if duration < 1000 else f"{duration / 1000:.2f} s")
    if payload.get("request_id") and payload["level"] in {"WARNING", "ERROR", "CRITICAL"}:
        parts.append(f"request {short_id(payload['request_id'])}")
    return parts
