"""Narrow, typed analytical facts shared by capture, backfill, and ClickHouse.

Only columns required for reporting are exported. Authentication, email addresses,
article bodies, prompts, evidence, and arbitrary inference payloads stay in PostgreSQL.
"""

from dataclasses import dataclass

from devfeed_core.models import Base

# This is an explicit allowlist, not a mirror of the application schema.
FIELDS = {
    "articles": ("id discovered_at published_to_feed_at publication_status content_type"),
    "sources": "id name approval_status enabled consecutive_failures",
    "topics": "id name status",
    "article_topics": "article_id topic_id role",
    "article_origins": "article_id source_id",
    "user_accounts": "id created_at",
    "article_opens": "article_id viewer_key opened_hour",
    "user_topics": "user_id topic_id",
    "user_interests": "user_id topic_id reason",
    "user_recommendation_states": (
        "user_id generation invalidated expires_at next_refresh_at interest_count"
    ),
    "topic_proposals": "id status created_at reviewed_at",
    "topic_relation_proposals": "id status created_at",
    "topic_decision_runs": "proposal_id status reason updated_at",
    "topic_decision_calls": (
        "proposal_id ordinal stage stage_call started_at tokens charged_tokens escalated"
    ),
    "inference_calls": (
        "id job_id attempt started_at finished_at status operation "
        "model reasoning_effort tokens web_searches duration_ms"
    ),
    "article_reviews": "id article_id created_at action automation",
}
JOB_TABLES = (
    "ingestion_jobs",
    "article_enrichment_jobs",
    "article_image_jobs",
    "source_enrichment_jobs",
    "article_analysis_jobs",
    "topic_analysis_jobs",
    "research_verification_jobs",
    "notification_deliveries",
)
JOB_KINDS = dict(
    zip(
        JOB_TABLES,
        (
            "ingestion",
            "article_enrichment",
            "article_image",
            "source_enrichment",
            "analysis",
            "topic_analysis",
            "research_verification",
            "notification",
        ),
        strict=True,
    )
)

TOKEN_FIELDS = (
    "totalTokens",
    "inputTokens",
    "cachedInputTokens",
    "outputTokens",
    "reasoningOutputTokens",
)
for _job_table in JOB_TABLES:
    _available = Base.metadata.tables[_job_table].columns
    FIELDS[_job_table] = " ".join(
        name
        for name in (
            "id",
            "status",
            "attempts",
            "created_at",
            "finished_at",
            "duration_ms",
            "outcome",
            "source_id",
            "article_id",
            "proposal_id",
            "topic_id",
            *(
                ()
                if _job_table
                not in {
                    "article_analysis_jobs",
                    "topic_analysis_jobs",
                    "research_verification_jobs",
                }
                else ("usage",)
            ),
        )
        if name in _available
    )


@dataclass(frozen=True)
class AnalyticsTable:
    name: str
    fields: tuple[str, ...]
    keys: tuple[str, ...]

    def record_sql(self, alias: str) -> str:
        """Build sanitized JSON in PostgreSQL without loading unrelated TOAST data."""
        values: list[str] = []
        for name in self.fields:
            value = f'{alias}."{name}"'
            if name == "automation":
                value = (
                    f"CASE WHEN {value} = '{{}}'::jsonb THEN '{{}}'::jsonb "
                    "ELSE jsonb_build_object('set', true) END"
                )
            elif name in {"usage", "tokens"} and self.name != "topic_decision_calls":
                # The counter parser accepts at most 18 digits. A nineteenth
                # character preserves invalid/overflow semantics while bounding
                # malformed scalar values; objects and arrays never enter capture.
                value = (
                    "jsonb_build_object("
                    + ", ".join(
                        f"'{key}', CASE WHEN jsonb_typeof({value}->'{key}') "
                        f"IN ('string','number') THEN left({value}->>'{key}',19) END"
                        for key in TOKEN_FIELDS
                    )
                    + ")"
                )
            values.extend((f"'{name}'", value))
        return "jsonb_build_object(" + ", ".join(values) + ")"


def _table(name, fields):
    table = Base.metadata.tables[name]
    keys = tuple(column.name for column in table.primary_key)
    fields = list(dict.fromkeys((*keys, *fields)))
    return AnalyticsTable(name, tuple(fields), keys)


TABLES = {name: _table(name, fields.split()) for name, fields in FIELDS.items()}


# Children enrich publication facts through bounded article-ID probes. Existing
# historical articles already include those associations during initial capture.
BACKFILL_TABLES = tuple(
    name for name in TABLES if name not in {"article_topics", "article_origins", "article_reviews"}
) + ("overview_daily",)
