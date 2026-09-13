"""Safe validation feedback for durable inference retries; never retain model output."""

import json
import re

from pydantic import ValidationError

CODES = {
    "unknown_catalog_id",
    "evidence_not_in_input",
    "duplicate_classification",
    "unresolved_ready_analysis",
    "reasons_too_long",
    "source_sample_incomplete",
    "source_evidence_too_short",
    "source_evidence_not_in_sample",
    "schema_validation",
}
FIELDS = {
    "topics",
    "tags",
    "topic_id",
    "id",
    "role",
    "relevance",
    "evidence",
    "developer_relevance",
    "language",
    "content_type",
    "content_format",
    "outcome",
    "ai_summary",
    "ai_description",
    "reasons",
    "entries",
    "index",
    "confidence",
    "reason",
}


class InferenceValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def validation_feedback(error: Exception) -> dict | None:
    if isinstance(error, InferenceValidationError) and error.code in CODES:
        return {"code": error.code, "fields": []}
    if not isinstance(error, ValidationError):
        return None
    fields = []
    code = "schema_validation"
    for item in error.errors(include_input=False, include_url=False)[:8]:
        cause = item.get("ctx", {}).get("error")
        if isinstance(cause, InferenceValidationError) and cause.code in CODES:
            code = cause.code
        path = (
            ".".join(
                str(part) if isinstance(part, int) or part in FIELDS else "unknown"
                for part in item["loc"][:5]
            )
            or "result"
        )
        kind = item["type"]
        if not re.fullmatch(r"[a-z_]{1,60}", kind):
            kind = "invalid"
        fields.append(f"{path}:{kind}")
    return {"code": code, "fields": fields}


def feedback_prompt(feedback) -> str:
    if (
        not isinstance(feedback, dict)
        or not isinstance(feedback.get("code"), str)
        or feedback["code"] not in CODES
    ):
        return ""
    # Persisted JSON is untrusted too. Rebuild bounded feedback from safe identifiers.
    fields = (
        [
            value
            for value in feedback.get("fields", [])[:8]
            if isinstance(value, str) and re.fullmatch(r"[a-z_0-9.:]{1,140}", value)
        ]
        if isinstance(feedback.get("fields"), list)
        else []
    )
    return (
        "\nThe previous attempt failed validation. Correct these issues in a fresh result: "
        + json.dumps({"code": feedback["code"], "fields": fields})
        + ". Use only supplied IDs and exact verbatim evidence. Omit unsupported selections; "
        "return uncertain or insufficient_evidence when the input cannot support a decision."
    )
