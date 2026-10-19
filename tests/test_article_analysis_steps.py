import copy
import json
import uuid

import pytest
from devfeed_core.analysis import ARTICLE_INPUT_BYTES, analysis_candidates, validate_evidence
from devfeed_core.article_analysis_steps import (
    STEP_NAMES,
    STEP_SPECS,
    article_step_feedback,
    assemble_article_analysis,
    next_article_step,
    prepare_article_step,
    validate_article_step,
)
from devfeed_core.inference_validation import InferenceValidationError
from pydantic import ValidationError

TOPIC = str(uuid.uuid4())
TAG = str(uuid.uuid4())
SOURCE = {
    "url": "https://publisher.example/angular",
    "title": "Angular routing",
    "source_summary": "An Angular routing tutorial.",
    "text": (
        "Angular routing helps developers organize application navigation. "
        "Angular 0.18.1 adds typed route helpers."
    ),
    "text_source": "body",
}
TAXONOMY = {
    "topics": [{"id": TOPIC, "name": "Angular", "description": "Angular applications."}],
    "tags": [{"id": TAG, "name": "Routing"}],
}
FIELDS = {
    "developer_relevance": {"developer_relevance"},
    "page_kind": {"page_kind"},
    "language": {"language"},
    "content_type": {"content_type"},
    "content_format": {"content_format"},
    "topics": {"topics"},
    "tags": {"tags"},
    "identifying_title": {"ai_title", "title_evidence"},
    "overview": {"ai_summary"},
    "description": {"ai_description"},
}


def outputs():
    fields = {
        "developer_relevance": {"developer_relevance": "relevant"},
        "page_kind": {"page_kind": "article"},
        "language": {"language": "en"},
        "content_type": {"content_type": "tutorial"},
        "content_format": {"content_format": "article"},
        "topics": {
            "topics": [
                {
                    "topic_id": TOPIC,
                    "role": "primary",
                    "relevance": 1,
                    "evidence": "Angular routing",
                }
            ]
        },
        "tags": {"tags": []},
        "identifying_title": {
            "ai_title": "Angular 0.18.1 adds typed route helpers",
            "title_evidence": "Angular 0.18.1 adds typed route helpers.",
        },
        "overview": {"ai_summary": "Angular routing organizes application navigation."},
        "description": {"ai_description": "A tutorial on navigation with Angular routing."},
    }
    return {
        name: {"outcome": "ready", "reason": f"Source evidence supports {name}.", **value}
        for name, value in fields.items()
    }


@pytest.mark.parametrize("name", STEP_NAMES)
def test_each_specialist_schema_owns_exactly_one_responsibility(name):
    prompt, schema, digest = prepare_article_step(name, SOURCE, TAXONOMY, outputs())
    assert set(schema["properties"]) == {"outcome", "reason", *FIELDS[name]}
    assert schema["additionalProperties"] is False
    assert len(digest) == 64
    assert STEP_SPECS[name].prompt_version
    assert "Do exactly the assigned task" in prompt
    validate_article_step(name, outputs()[name], SOURCE, TAXONOMY, outputs())
    invalid = {**outputs()[name], "author": "Invented author"}
    with pytest.raises(ValidationError, match="Extra inputs"):
        validate_article_step(name, invalid, SOURCE, TAXONOMY, outputs())


def test_taxonomy_and_prior_outputs_are_sent_only_to_the_job_that_needs_them():
    previous = outputs()
    previous["language"]["private_other_task"] = "Never send this to prose generation"
    for name in STEP_NAMES:
        prompt, schema, _ = prepare_article_step(name, SOURCE, TAXONOMY, previous)
        context = json.loads(prompt.rsplit("\n", 1)[1])
        assert "private_other_task" not in prompt
        if name == "description":
            assert context == {
                "source_title": SOURCE["title"],
                "overview": previous["overview"]["ai_summary"],
            }
            assert SOURCE["text"] not in prompt
        elif name in {"topics", "tags"}:
            assert set(context["catalog"]) == {name}
            definition, field = (
                ("TopicSelection", "topic_id") if name == "topics" else ("LabelSelection", "id")
            )
            assert schema["$defs"][definition]["properties"][field]["enum"] == [
                TAXONOMY[name][0]["id"]
            ]
        else:
            assert "catalog" not in context


@pytest.mark.parametrize(
    "name,limit", [("language", 3000), ("content_type", 6000), ("content_format", 4000)]
)
def test_simple_classification_uses_explicit_bounded_excerpts(name, limit):
    snapshot = {
        **SOURCE,
        "text": "Source evidence. " * 1000,
        "source_summary": "Source teaser. " * 200,
    }
    prompt, _, _ = prepare_article_step(name, snapshot, TAXONOMY, {})
    context = json.loads(prompt.rsplit("\n", 1)[1])["source"]
    assert len(context["text"]) == limit
    assert len(context["source_summary"]) == 1500
    assert context["text_is_excerpt"] is context["source_summary_is_excerpt"] is True
    if name == "language":
        assert "title" not in context and "url" not in context


def test_step_hashes_follow_exact_context_instead_of_all_parent_outputs():
    previous = outputs()
    _, _, overview = prepare_article_step("overview", SOURCE, TAXONOMY, previous)
    _, _, description = prepare_article_step("description", SOURCE, TAXONOMY, previous)
    changed_catalog = copy.deepcopy(TAXONOMY)
    changed_catalog["topics"][0]["description"] = "Changed catalog semantics."
    assert prepare_article_step("overview", SOURCE, changed_catalog, previous)[2] == overview
    assert (
        prepare_article_step("topics", SOURCE, changed_catalog, previous)[2]
        != prepare_article_step("topics", SOURCE, TAXONOMY, previous)[2]
    )
    previous["overview"]["ai_summary"] += " Typed helpers are described."
    assert prepare_article_step("overview", SOURCE, TAXONOMY, previous)[2] == overview
    assert prepare_article_step("description", SOURCE, TAXONOMY, previous)[2] != description


def test_every_specialist_bounds_combined_utf8_prompt_and_schema_for_large_publisher_inputs():
    source = {
        **SOURCE,
        "title": "大規模な開発ツール " * 10_000,
        "source_summary": "概要🌍\x00" * 30_000,
        "text": "Source beginning. " + "開発🌍\x00" * 60_000 + " Source ending.",
    }
    taxonomy = {
        field: [
            {
                "id": str(uuid.uuid4()),
                "name": f"Catalog {index:03}",
                "slug": f"catalog-{index}",
                "aliases": ["工具" * 100] * 10,
                "description": "意味のある分類 " * 1000,
            }
            for index in range(100)
        ]
        for field in ("topics", "tags")
    }
    candidates = analysis_candidates(taxonomy, source)
    assert candidates["topics"] and candidates["tags"]
    for name in STEP_NAMES:
        prompt, schema, _ = prepare_article_step(name, source, candidates, outputs())
        feedback = article_step_feedback(name, {"code": "schema_validation"})
        assert (
            len((prompt + feedback).encode()) + len(json.dumps(schema).encode())
            <= ARTICLE_INPUT_BYTES
        )
        assert "�" not in prompt
        context = json.loads(prompt.rsplit("\n", 1)[1])
        if name in {"overview", "topics", "identifying_title"}:
            assert context["source"]["text_is_excerpt"] is True
            assert context["source"]["source_summary_is_excerpt"] is True
            assert context["source"]["text"].startswith("Source beginning.")
            assert context["source"]["text"].endswith("Source ending.")
            assert "[... source excerpt omitted ...]" in context["source"]["text"]
            assert "do not imply full-document coverage" in prompt


def test_regular_sixty_thousand_character_body_is_preserved_without_excerpting():
    source = {**SOURCE, "text": "A practical software development guide. " * 1500}
    assert len(source["text"]) > 55_000
    prompt, _, _ = prepare_article_step("overview", source, TAXONOMY, {})
    evidence = json.loads(prompt.rsplit("\n", 1)[1])["source"]
    assert evidence["text"] == source["text"]
    assert not evidence.get("text_is_excerpt")


@pytest.mark.parametrize("name", ["topics", "tags", "identifying_title"])
def test_step_evidence_cannot_quote_source_material_omitted_from_its_excerpt(name):
    hidden = "The hidden section documents Rust release stability."
    source = {**SOURCE, "title": "v0.18.1", "text": "開発" * 50_000 + hidden + "結論" * 50_000}
    candidate = copy.deepcopy(outputs()[name])
    if name == "identifying_title":
        candidate.update(ai_title="Rust release stability", title_evidence=hidden)
    elif name == "topics":
        candidate["topics"][0]["evidence"] = hidden
    else:
        candidate["tags"] = [{"id": TAG, "evidence": hidden}]
    prompt, _, _ = prepare_article_step(name, source, TAXONOMY, outputs())
    assert hidden not in prompt
    with pytest.raises(InferenceValidationError, match="not present|missing from supplied"):
        validate_article_step(name, candidate, source, TAXONOMY, {})


def test_direct_oversized_catalog_fails_before_any_model_call():
    taxonomy = {"topics": [{**TAXONOMY["topics"][0], "aliases": ["🌍" * 100_000]}]}
    with pytest.raises(InferenceValidationError, match="input budget") as failure:
        prepare_article_step("topics", SOURCE, taxonomy, {})
    assert failure.value.code == "analysis_input_too_large"


def test_identifying_source_title_skips_the_model_and_preserves_original_headline():
    previous = outputs()
    previous.pop("identifying_title")
    assert next_article_step(SOURCE, previous) is None
    result = assemble_article_analysis(SOURCE, previous)
    assert result.outcome == "ready"
    assert result.ai_title is result.title_evidence is None
    validate_evidence(result, SOURCE, TAXONOMY)
    before_overview = {
        name: value for name, value in previous.items() if name not in {"overview", "description"}
    }
    assert next_article_step(SOURCE, before_overview) == "overview"


def test_version_only_title_is_its_own_required_grounded_step():
    source = {**SOURCE, "title": "v0.18.1"}
    previous = outputs()
    del previous["identifying_title"]
    assert next_article_step(source, previous) == "identifying_title"
    assert assemble_article_analysis(source, previous).outcome == "insufficient_evidence"
    title = outputs()["identifying_title"]
    validate_article_step("identifying_title", title, source, TAXONOMY, previous)
    previous["identifying_title"] = title
    result = assemble_article_analysis(source, previous)
    assert result.outcome == "ready" and result.ai_title == title["ai_title"]
    validate_evidence(result, source, TAXONOMY)
    with pytest.raises(InferenceValidationError, match="source body"):
        validate_article_step(
            "identifying_title",
            {**title, "title_evidence": "Invented support"},
            source,
            TAXONOMY,
            previous,
        )


@pytest.mark.parametrize(
    "name,field,negative",
    [
        ("developer_relevance", "developer_relevance", "unrelated"),
        ("page_kind", "page_kind", "non_article"),
    ],
)
def test_eligibility_negative_stops_other_jobs_but_preserves_rejection_evidence(
    name, field, negative
):
    previous = outputs()
    if name == "developer_relevance":
        previous = {name: previous[name]}
    else:
        previous = {key: previous[key] for key in ("developer_relevance", "page_kind")}
    previous[name][field] = negative
    assert next_article_step(SOURCE, previous) is None
    result = assemble_article_analysis(SOURCE, previous)
    assert getattr(result, field) == negative
    assert result.outcome == "insufficient_evidence"
    assert result.ai_summary is result.ai_description is None


def test_unknown_classification_fails_closed_without_generating_prose():
    previous = {
        name: value
        for name, value in outputs().items()
        if name in {"developer_relevance", "page_kind", "language"}
    }
    previous["language"].update(language=None, outcome="insufficient_evidence")
    assert next_article_step(SOURCE, previous) is None
    result = assemble_article_analysis(SOURCE, previous)
    assert result.outcome == "insufficient_evidence"
    assert result.reasons == [value["reason"] for value in previous.values()]
    previous["language"]["outcome"] = "ready"
    with pytest.raises(InferenceValidationError, match="resolve its task"):
        validate_article_step("language", previous["language"], SOURCE, TAXONOMY, previous)


def test_uncertain_relevance_still_checks_page_kind_and_preserves_independent_rejection():
    previous = {
        "developer_relevance": {
            "outcome": "insufficient_evidence",
            "reason": "Audience is unresolved from the available evidence.",
            "developer_relevance": "uncertain",
        }
    }
    assert next_article_step(SOURCE, previous) == "page_kind"
    previous["page_kind"] = {
        "outcome": "ready",
        "reason": "The page only lists subscription feeds.",
        "page_kind": "non_article",
    }
    assert next_article_step(SOURCE, previous) is None
    result = assemble_article_analysis(SOURCE, previous)
    assert result.developer_relevance == "uncertain"
    assert result.page_kind == "non_article"
    assert result.outcome == "insufficient_evidence"
    assert result.ai_summary is result.ai_description is None
    assert result.reasons == [value["reason"] for value in previous.values()]


def test_topic_step_cannot_force_a_match_or_quote_catalog_definitions():
    empty = {"outcome": "ready", "reason": "No matching catalog topic.", "topics": []}
    validated = validate_article_step("topics", empty, SOURCE, TAXONOMY, {})
    assert validated["outcome"] == "insufficient_evidence"
    previous = outputs()
    previous["topics"] = validated
    assert next_article_step(SOURCE, previous) is None
    assert assemble_article_analysis(SOURCE, previous).ai_summary is None
    invalid = copy.deepcopy(outputs()["topics"])
    invalid["topics"][0]["evidence"] = TAXONOMY["topics"][0]["description"]
    with pytest.raises(InferenceValidationError, match="not present"):
        validate_article_step("topics", invalid, SOURCE, TAXONOMY, {})
    invalid["topics"][0].update(topic_id=str(uuid.uuid4()), evidence="Angular routing")
    with pytest.raises(InferenceValidationError, match="unknown"):
        validate_article_step("topics", invalid, SOURCE, TAXONOMY, {})


@pytest.mark.parametrize(
    "name,field", [("overview", "ai_summary"), ("description", "ai_description")]
)
def test_prose_jobs_require_readable_text_but_have_no_minimum_word_count(name, field):
    result = {"outcome": "ready", "reason": "Supported by the source.", field: "Go."}
    assert validate_article_step(name, result, SOURCE, TAXONOMY, outputs())[field] == "Go."
    result[field] = "<p></p>"
    with pytest.raises(InferenceValidationError, match="readable"):
        validate_article_step(name, result, SOURCE, TAXONOMY, outputs())
    result["outcome"] = "insufficient_evidence"
    assert validate_article_step(name, result, SOURCE, TAXONOMY, outputs())[field] is None


def test_retry_feedback_cannot_reintroduce_other_tasks_or_untrusted_error_text():
    feedback = article_step_feedback(
        "overview", {"code": "missing_ai_summary", "private": "secret"}
    )
    assert '"task_fields": ["ai_summary"]' in feedback
    assert "private" not in feedback and "secret" not in feedback
    assert "topic_id" not in feedback and "ai_description" not in feedback
    assert article_step_feedback("overview", {"code": ["malformed"]}) == ""
