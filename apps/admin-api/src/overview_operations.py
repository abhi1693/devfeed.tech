"""Current review and recovery controls, kept outside historical analytics."""

from datetime import datetime

from devfeed_core.config import get_settings
from devfeed_core.models import Article, Source, TopicProposal, TopicRelationProposal
from sqlalchemy import func, literal, select, union_all

from devfeed_admin_api.overview_sections import (
    OperationsSection,
    OverviewSectionReport,
    ReviewQueue,
)


def operations_report(session, days: int, now: datetime) -> OverviewSectionReport:
    from devfeed_admin_api.overview_projection import projected_blockers

    reviews = session.execute(
        union_all(
            *[
                select(literal(kind), func.count(), func.min(timestamp))
                .select_from(model)
                .where(condition)
                for kind, model, timestamp, condition in (
                    (
                        "articles",
                        Article,
                        Article.discovered_at,
                        Article.review_status == "pending",
                    ),
                    ("sources", Source, Source.created_at, Source.approval_status == "pending"),
                    (
                        "topics",
                        TopicProposal,
                        TopicProposal.created_at,
                        TopicProposal.status == "pending",
                    ),
                    (
                        "relationships",
                        TopicRelationProposal,
                        TopicRelationProposal.created_at,
                        TopicRelationProposal.status == "pending",
                    ),
                )
            ]
        )
    ).all()
    failures = session.scalar(
        select(func.count())
        .select_from(Source)
        .where(
            Source.enabled, Source.approval_status == "approved", Source.consecutive_failures > 0
        )
    )
    blockers = projected_blockers(session)
    return OverviewSectionReport(
        generated_at=now,
        days=days,
        data=OperationsSection(
            full_automation=get_settings().full_automation,
            reviews=[
                ReviewQueue(kind=kind, count=count, oldest_at=oldest)
                for kind, count, oldest in reviews
            ],
            source_failures=failures or 0,
            blockers=blockers,
        ),
        snapshot=session.info.get("report_projection_snapshot"),
    )
