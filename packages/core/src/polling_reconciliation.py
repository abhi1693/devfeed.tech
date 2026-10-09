"""Batch only schedules whose applied mode disagrees with the current gate."""

from datetime import timedelta

from sqlalchemy import or_, select

from devfeed_core.config import get_settings
from devfeed_core.models import IngestionJob, Source
from devfeed_core.polling import schedule


def reconcile_polling(factory, batch, now):
    settings = get_settings()
    enabled = settings.adaptive_source_polling_enabled
    active = select(IngestionJob.id).where(
        IngestionJob.source_id == Source.id, IngestionJob.status.in_(["queued", "running"])
    )
    mismatch = Source.scheduled_polling_mode == "adaptive"
    if enabled:
        mismatch = or_(
            (Source.polling_mode == "fixed") & (Source.scheduled_polling_mode == "adaptive"),
            (Source.polling_mode == "adaptive")
            & or_(
                Source.scheduled_polling_mode.is_distinct_from("adaptive"),
                Source.scheduled_interval_seconds < settings.adaptive_source_polling_min_seconds,
                Source.scheduled_interval_seconds > settings.adaptive_source_polling_max_seconds,
            ),
        )
    with factory.begin() as session:
        sources = session.scalars(
            select(Source)
            .where(
                Source.enabled.is_(True),
                Source.approval_status == "approved",
                Source.consecutive_failures == 0,
                mismatch,
                ~active.exists(),
            )
            .order_by(Source.id)
            .limit(batch)
            .with_for_update(skip_locked=True)
        ).all()
        for source in sources:
            due = schedule(source, source.last_success_at or now)
            # Spread overdue catch-up over one scheduler minute, never queue more
            # than the normal scheduler batch; preserve active jobs and cooldowns.
            source.next_fetch_at = max(due, now + timedelta(seconds=int(source.id) % 60))
    return len(sources)
