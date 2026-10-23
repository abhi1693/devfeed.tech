"""Check the real provider schema contract, independent of mocked model replies."""

import pytest
from devfeed_core.analysis import AnalysisResult
from devfeed_core.relationship_verification import RelationshipVerificationResult
from devfeed_core.topic_analysis import TopicResearchResult
from devfeed_core.topic_relationships import RelationshipResearchResult
from devfeed_core.topic_verification import TopicVerificationResult


@pytest.mark.parametrize(
    "model",
    [
        AnalysisResult,
        TopicResearchResult,
        RelationshipResearchResult,
        TopicVerificationResult,
        RelationshipVerificationResult,
    ],
)
def test_structured_output_objects_require_all_properties(model):
    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
                assert set(node.get("required", [])) == set(node.get("properties", {}))
            for child in node.values():
                check(child)
        elif isinstance(node, list):
            for child in node:
                check(child)

    check(model.model_json_schema())
