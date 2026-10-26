"""Typed saved-section documents for publication and HTTP lifecycle tests."""

from devfeed_admin_api.overview_sections import (
    AudienceSection,
    CoverageSection,
    InferenceSection,
    OperationsSection,
    OverviewSectionReport,
    ProcessingSection,
    PublicationSummary,
    PublishingSection,
    ReviewQueue,
)
from devfeed_admin_api.overview_windows import current_window, recent_window
from devfeed_core.models import ReadModelState, ReportSnapshot, utcnow
from devfeed_core.read_models import pipeline_policy_version

SECTION_MODELS = {
    "operations": OperationsSection,
    "publishing": PublishingSection,
    "audience": AudienceSection,
    "processing": ProcessingSection,
    "inference": InferenceSection,
    "coverage": CoverageSection,
}


def window_for(group="publishing", days=7, now=None):
    return current_window() if group == "operations" else recent_window(days, now or utcnow())


def window_fields(group="publishing", days=7, *, now=None, window=None):
    """Keep calendar identity explicit while builders retain readable day-count inputs."""
    now = now or utcnow()
    window = current_window() if group == "operations" else window or window_for(group, days, now)
    return {
        "window_key": window.key,
        "window_start": window.start,
        "window_end": window.end,
        "days": window.days,
        "last_requested_at": now,
    }


def section_document(
    *, group="publishing", generated_at=None, days=7, count=23, snapshot=None, window=None
):
    data = SECTION_MODELS[group]()
    if group == "operations":
        data.reviews = [ReviewQueue(kind="articles", count=count)]
    elif group == "publishing":
        data.summary = PublicationSummary(published=count)
    window = current_window() if group == "operations" else window or recent_window(days, utcnow())
    return OverviewSectionReport(
        generated_at=generated_at or utcnow(),
        days=window.days,
        start=window.start,
        end=window.inclusive_end,
        data=data,
        snapshot=snapshot,
    )


def save_section(
    database, group="publishing", days=7, *, generated_at=None, policy=None, window=None, count=23
):
    from devfeed_admin_api.overview_projection import report_policy_version

    now = utcnow()
    window = current_window() if group == "operations" else window or window_for(group, days, now)
    report = section_document(group=group, generated_at=generated_at, window=window, count=count)
    with database.begin() as session:
        session.merge(
            ReportSnapshot(
                group=group,
                **window_fields(group, now=now, window=window),
                payload=report.model_dump(mode="json"),
                generated_at=report.generated_at,
                policy_version=policy or report_policy_version(),
            )
        )
    return report


def ready_pipeline(database, *, now=None):
    policy = pipeline_policy_version()
    with database.begin() as session:
        session.merge(
            ReadModelState(
                name="pipeline",
                payload={
                    "serving_policy": policy,
                    "target_policy": policy,
                    "backfill_complete": True,
                },
                updated_at=now or utcnow(),
            )
        )


def publish_section(database, group="operations", days=30, *, window=None):
    """Publish real domain facts before exercising the saved HTTP boundary."""
    from devfeed_admin_api.overview_projection import (
        calculate_report,
        report_policy_version,
    )
    from devfeed_admin_api.pipeline_projection import drain_pipeline

    from tests.support.analytics import load_section

    now = utcnow()
    window = current_window() if group == "operations" else window or recent_window(days, now)
    if group == "operations":
        drain_pipeline(database)
        report = calculate_report(database, group, window, utcnow())
    else:
        report = load_section(database, group, days, now, window=window)
    payload = report.model_dump(mode="json", exclude={"snapshot"})
    if report.snapshot is not None:
        payload["_projection_snapshot"] = report.snapshot.model_dump(mode="json")
    with database.begin() as session:
        session.merge(
            ReportSnapshot(
                group=group,
                **window_fields(group, now=now, window=window),
                payload=payload,
                generated_at=report.generated_at,
                policy_version=report_policy_version(),
            )
        )
    return report
