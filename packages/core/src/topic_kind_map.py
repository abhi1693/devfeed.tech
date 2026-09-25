"""Load operator-supplied mappings from historical topic kinds."""

import json
from pathlib import Path

from devfeed_core.topic_kinds import TOPIC_KINDS


def load_topic_kind_map(path: Path) -> dict[str, str]:
    """Read and validate a mounted JSON object of old-kind to canonical-kind values."""
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Could not read topic kind map at {path}: {error}") from error
    if not isinstance(values, dict):
        raise ValueError("Topic kind map must be a JSON object")

    result: dict[str, str] = {}
    for legacy, canonical in values.items():
        if not isinstance(legacy, str) or not legacy.strip():
            raise ValueError("Topic kind map keys must be non-empty strings")
        if not isinstance(canonical, str) or canonical not in TOPIC_KINDS:
            raise ValueError(f"Invalid canonical topic kind for map key {legacy!r}")
        result[legacy] = canonical
    return result
