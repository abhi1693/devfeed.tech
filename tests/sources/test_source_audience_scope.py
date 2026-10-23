"""Source admission covers software product professionals as well as developers."""

from types import SimpleNamespace

import pytest
from devfeed_aggregator import source_relevance
from devfeed_core.feeds.parser import ParsedFeed
from devfeed_core.source_relevance import SOURCE_SCOPE_POLICY, VERSION


@pytest.mark.parametrize(
    "titles",
    [
        [
            "How to Become an AI PM without AI PM experience",
            "Stakeholder Management for Software Product Managers",
            "Product Discovery and Growth for SaaS Teams",
        ],
        [
            "How to Coach and Mentor Software Engineering Teams",
            "Building a Healthy Engineering Team Culture",
            "How to Grow Past Senior Engineer",
        ],
    ],
)
def test_broader_scope_reaches_inference_and_accepts_valid_evidence(monkeypatch, titles):
    parsed = ParsedFeed([SimpleNamespace(title=title, summary="") for title in titles], 3, 0)
    monkeypatch.setattr(source_relevance, "validate_feed", lambda *a, **kw: parsed)

    def complete(prompt, schema):
        assert SOURCE_SCOPE_POLICY in prompt
        assert "software product management" in prompt
        assert "engineering leadership" in prompt
        assert "careers in software/product roles" in prompt
        assert "An entry does not need code or implementation details" in prompt
        assert "untrusted evidence" in prompt
        assert "A topic must have a direct" not in prompt
        return {
            "relevance": "relevant",
            "confidence": 0.95,
            "reason": "Practical guidance for software product professionals.",
            "entries": [
                {"index": i, "relevance": "relevant", "evidence": f"e{i}_0"}
                for i, title in enumerate(titles)
            ],
        }

    monkeypatch.setattr(
        source_relevance, "CodexClient", lambda _: SimpleNamespace(complete=complete)
    )
    result = source_relevance.assess_source("https://example.com/feed", "publisher")
    assert result["version"] == VERSION == "source-relevance-v5"
    assert result["approval_supported"] is True
    assert result["rejection_supported"] is False
