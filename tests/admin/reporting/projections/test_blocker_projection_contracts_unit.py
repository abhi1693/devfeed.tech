"""Operator blocker cards preserve exact counts and bounded, revision-bound targets.

Classified rows and research-review rows are supplied repository results. These
units do not evaluate the SQL classification, latest-result joins, ranks, or
source/date/verification eligibility predicates that select those results.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_admin_api import automation
from devfeed_core import topic_verification
from sqlalchemy.orm import Session

from tests.admin.articles.test_article_board_policy_unit import history
from tests.support.article_readiness import processing_case as processing_case
from tests.support.source_readiness import configuration
from tests.support.taxonomy_command_helpers import (
    metadata_job,
    proposal,
    relationship_job,
    relationship_proposal,
    topic,
)


@pytest.mark.parametrize("include_proposals,full", [(False, False), (True, False), (True, True)])
def test_blocker_projection_preserves_global_counts_bounded_targets_and_review_context(
    processing_case, monkeypatch, include_proposals, full
):
    settings = configuration(full_automation=full)
    monkeypatch.setattr(automation, "get_settings", lambda: settings)
    articles = [history(processing_case, f"paused-source-{index}")[0] for index in range(2)]
    for article in articles:
        article.review_status = "pending"
        article.origins[0].source.enabled = False  # Approved publisher was subsequently paused.
    selected = [
        SimpleNamespace(
            code="source_ineligible",
            id=article.id,
            title=article.title,
            editorial_revision=article.editorial_revision,
            total=9,
        )
        for article in articles
    ]
    pending = proposal()
    research = metadata_job(pending)
    item = topic_verification.verification_input(pending)
    uncertain = topic_verification.TopicVerificationResult(
        proposal_id=pending.id,
        input_hash=item["input_hash"],
        verdict="uncertain",
        relevance={"verdict": "uncertain", "sources": [], "reason": "Primary evidence unavailable"},
        fields=[
            {
                "field": field,
                "supported": False,
                "sources": [],
                "reason": "Primary evidence unavailable",
            }
            for field in topic_verification.FIELDS
            if pending.proposed.get(field)
        ],
        aliases=[],
        sources=[],
    )
    research.result["topic_verification"] = topic_verification.checked_verdict(
        uncertain.model_dump(mode="json"), item
    )
    focal, peer = topic(kind="framework"), topic("JavaScript", kind="language")
    relationship = relationship_proposal(focal, peer, relationship_job(focal, peer))
    session = Mock(spec=Session)
    session.execute.side_effect = [
        selected,
        SimpleNamespace(
            all=lambda: [SimpleNamespace(id=pending.id, proposed=pending.proposed, total=1)]
        ),
    ]
    session.scalar.return_value = 1
    session.scalars.return_value = [relationship]

    result = automation.automation_blockers(
        session,
        None,  # Global count nine, with the first two already-ranked recovery targets supplied.
        include_proposals=include_proposals,
        target_limit=2,
    )

    by_code = {card.code: card for card in result}
    blocked = by_code["source_ineligible"]
    assert blocked.count == 9 and len(blocked.targets) == 2
    assert blocked.label == "No enabled, approved source" and blocked.action is None
    assert [(target.id, target.revision, target.kind) for target in blocked.targets] == [
        (article.id, article.editorial_revision, "article") for article in articles
    ]
    assert [target.title for target in blocked.targets] == [article.title for article in articles]
    assert by_code["analysis_failed"].action == "analyze"
    assert by_code["enrichment_failed"].action == "enrich"
    assert by_code["editorial_review"].label == (
        "Publication policy needs review" if full else "Classified articles awaiting review"
    )
    assert all(
        card.count == 0 and card.targets == []
        for card in result
        if card.code
        not in {"source_ineligible", "evidence_unverified", "relationship_evidence_unverified"}
    )
    if include_proposals:
        evidence = by_code["evidence_unverified"]
        assert evidence.count == 1 and len(evidence.targets) == 1
        assert evidence.targets[0].id == pending.id
        assert evidence.targets[0].title == pending.proposed["name"]
        assert evidence.targets[0].kind == "topic-proposal" and evidence.action is None
        assert evidence.label == (
            "Topic research in progress" if full else "Research evidence or identity needs review"
        )
        relation = by_code["relationship_evidence_unverified"]
        assert relation.count == 1 and len(relation.targets) == 1
        assert relation.targets[0].id == relationship.id
        assert relation.targets[0].title == "React → JavaScript"
        assert relation.targets[0].kind == "relationship-proposal"
        assert relation.label == (
            "Relationship verification in progress"
            if full
            else "Relationship evidence or meaning needs review"
        )
    else:
        assert (
            "evidence_unverified" not in by_code
            and "relationship_evidence_unverified" not in by_code
        )
        session.scalar.assert_not_called()
        session.scalars.assert_not_called()
        assert session.execute.call_count == 1
    assert [article.review_status for article in articles] == ["pending", "pending"]
    assert pending.status == relationship.status == "pending"
    session.commit.assert_not_called()
    session.add.assert_not_called()
