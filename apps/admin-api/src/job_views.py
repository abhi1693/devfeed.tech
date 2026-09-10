"""Explicit presentation fields: new ORM columns are private until reviewed."""

COMMON_FIELDS = (
    "id",
    "status",
    "attempts",
    "created_at",
    "available_at",
    "dispatched_at",
    "finished_at",
    "error",
)

JOB_FIELDS = {
    "ingestion": COMMON_FIELDS
    + ("source_id", "http_status", "entries_seen", "articles_created", "entries_skipped"),
    "article-enrichment": COMMON_FIELDS
    + ("article_id", "http_status", "outcome", "changed_fields", "result"),
    "images": COMMON_FIELDS + ("article_id", "http_status", "outcome", "image_url", "method"),
    "source-enrichment": COMMON_FIELDS + ("source_id", "changed_fields"),
    "analysis": COMMON_FIELDS
    + (
        "article_id",
        "input_hash",
        "catalog_hash",
        "editorial_revision",
        "model",
        "prompt_version",
        "result",
        "outcome",
        "usage",
        "duration_ms",
    ),
    "topic-analysis": COMMON_FIELDS
    + (
        "proposal_id",
        "topic_id",
        "input_hash",
        "requested_by",
        "model",
        "prompt_version",
        "result",
        "outcome",
        "usage",
        "duration_ms",
    ),
    "notifications": COMMON_FIELDS
    + ("event_key", "dedup_key", "audience", "subscriber_id", "category", "payload"),
}
