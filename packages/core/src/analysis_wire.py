"""Lossless article transport compaction; database identities never leave this mapping."""

import copy
import json

from devfeed_core.analysis import analysis_output_schema, analysis_prompt
from devfeed_core.inference_validation import InferenceValidationError


def compact_request(
    snapshot: dict, taxonomy: dict, *, short_ids: bool = False
) -> tuple[str, dict, dict]:
    catalog: dict[str, list[dict]] = {}
    identities: dict[str, dict[str, str]] = {}
    for field, prefix in (("topics", "t"), ("tags", "g")):
        catalog[field], identities[field] = [], {}
        for index, original in enumerate(taxonomy[field]):
            identifier = f"{prefix}{index}" if short_ids else original["id"]
            identities[field][identifier] = original["id"]
            item = {key: value for key, value in original.items() if value not in (None, "", [])}
            for key in ("aliases", "keywords"):
                if key in item:
                    item[key] = list(dict.fromkeys(item[key]))
            item["id"] = identifier
            catalog[field].append(item)
    schema = analysis_output_schema(taxonomy)
    for field, selection, key in (
        ("topics", "TopicSelection", "topic_id"),
        ("tags", "LabelSelection", "id"),
    ):
        prop = schema["$defs"][selection]["properties"][key]
        if short_ids:
            prop.pop("format", None)
        if identities[field]:
            prop["enum"] = list(identities[field])
    instructions = analysis_prompt({}, {}).rsplit("\n", 1)[0]
    prompt = (
        instructions
        + "\n"
        + json.dumps(
            {"catalog": catalog, "article": snapshot}, ensure_ascii=False, separators=(",", ":")
        )
    )
    return prompt, schema, identities


def restore_identities(output: dict, identities: dict) -> dict:
    result = copy.deepcopy(output)
    for field, key in (("topics", "topic_id"), ("tags", "id")):
        for selection in result.get(field, []):
            identifier = selection.get(key)
            if not isinstance(identifier, str) or identifier not in identities[field]:
                raise InferenceValidationError(
                    "unknown_catalog_id", "Unknown request-local identity"
                )
            selection[key] = identities[field][identifier]
    return result
