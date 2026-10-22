"""Tiny source context cannot authorize fabricated evidence or a primary topic."""

import copy
import json

import pytest
from devfeed_core.analysis import require_primary_topic
from devfeed_core.analysis_wire import compact_request
from test_ai_optimization import catalog_item
from test_analysis import result


@pytest.mark.parametrize("title", ["", "Go"])
def test_compact_request_retains_short_context_without_offering_unsupported_evidence(title):
    snapshot = {"title": title, "source_summary": "", "text": ""}
    taxonomy = {"topics": [catalog_item("Go")], "tags": []}
    before = copy.deepcopy((snapshot, taxonomy))

    prompt, schema, identities = compact_request(
        snapshot, taxonomy, short_ids=True, evidence_refs=True
    )

    article = json.loads(prompt.rsplit("\n", 1)[1])["article"]
    assert article["title"] == ([{"text": title}] if title else [])
    assert identities["evidence"] == identities["title_evidence"] == {}
    assert schema["properties"]["ai_title"] == {"type": "null"}
    assert schema["properties"]["title_evidence"]["anyOf"] == [{"type": "null"}]
    assert schema["properties"]["topics"]["maxItems"] == 0
    assert schema["properties"]["tags"]["maxItems"] == 0
    assert (snapshot, taxonomy) == before


@pytest.mark.parametrize(
    "relevance,page_kind", [("unrelated", "article"), ("relevant", "non_article")]
)
def test_primary_topic_admission_does_not_reclassify_an_unrelated_or_non_article_result(
    relevance, page_kind
):
    evaluated = result(
        developer_relevance=relevance,
        page_kind=page_kind,
        topics=[],
        tags=[],
        ai_summary=None,
        ai_description=None,
    )
    before = evaluated.model_dump()

    preserved, marker = require_primary_topic(evaluated)

    assert preserved is evaluated and marker is None
    assert preserved.model_dump() == before
