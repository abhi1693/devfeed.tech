"""Repair the confirmed September 10 audit findings, preserving review history.

Run with the application environment: python scripts/repair_identity_audit.py
Inspect the dry-run output, then add --apply. The exact before-state checks reject
unexpected edits; already corrected records are skipped. No research is rerun.
"""

import argparse
import json

from devfeed_core.analysis import snapshot_hash
from devfeed_core.db import session_factory
from devfeed_core.models import Topic, TopicAnalysisJob, TopicRelationProposal
from devfeed_core.services import OperationConflict
from devfeed_core.topic_auto_approval import retract_automatic_relationship
from devfeed_core.topic_corrections import correct_automatic_topic
from devfeed_core.topic_proposals import snapshot
from devfeed_core.topic_relationships import proposal_hash
from devfeed_core.topics import TopicWrite
from sqlalchemy import select, text

ACTOR = {
    "subject": "authorized-identity-repair-2026-09-10",
    "issuer": "devfeed",
    "organization_id": "system",
    "name": "Authorized identity audit correction",
}
CHANGES = {
    "RSS": (
        "aliases",
        ["rfc4287", "rfc-4287"],
        [],
        "RFC 4287 specifies Atom, not RSS: https://www.rfc-editor.org/info/rfc4287/",
    ),
    "Bukkit": (
        "aliases",
        ["spigot", "paper", "papermc", "craftbukkit"],
        [],
        "Server implementations/forks are distinct from the Bukkit API: "
        "https://github.com/Bukkit/Bukkit and https://docs.papermc.io/paper/migration/",
    ),
    "Verilog": (
        "aliases",
        ["hdl", "hardware-description-language"],
        [],
        "HDL is a language category, not an identity alias: https://docs.cocotb.org/en/stable/",
    ),
    "Web Monetization": (
        "aliases",
        ["webmonetization", "interledger", "ilp"],
        ["webmonetization"],
        "Web Monetization uses distinct Interledger technologies: "
        "https://webmonetization.org/specification/",
    ),
    "Mathematics": ("kind", "technology", "discipline", "Mathematics is a field of study."),
    "The University of Texas at Arlington": (
        "kind",
        "technology",
        "organization",
        "The named entity is a university: https://www.uta.edu/",
    ),
}


def repair(*, apply=False):
    changes = []
    with session_factory().begin() as session:
        if not apply:
            session.execute(text("SET TRANSACTION READ ONLY"))
        proposals = session.scalars(
            select(TopicRelationProposal).where(
                TopicRelationProposal.topic_id.in_(select(Topic.id).where(Topic.name == "WebKit")),
                TopicRelationProposal.related_topic_id.in_(
                    select(Topic.id).where(Topic.name == "XAMPP")
                ),
                TopicRelationProposal.relation == "depends_on",
                TopicRelationProposal.status == "approved",
            )
        ).all()
        if apply and proposals:
            # Workers and relationship corrections lock jobs before the catalog.
            # Acquire these first so the combined repair follows the same order.
            session.execute(
                select(TopicAnalysisJob.id)
                .where(TopicAnalysisJob.id.in_([p.job_id for p in proposals]))
                .order_by(TopicAnalysisJob.id)
                .with_for_update()
            ).all()
        for name, (field, before, after, note) in CHANGES.items():
            topic = session.scalar(select(Topic).where(Topic.name == name))
            if topic is None:
                raise OperationConflict(f"Audit topic missing: {name}")
            if getattr(topic, field) == after:
                continue
            if getattr(topic, field) != before:
                raise OperationConflict(f"Audit topic changed: {name}")
            current = snapshot(topic)
            body = TopicWrite.model_validate({**current["draft"], field: after})
            item = {
                "topic_id": str(topic.id),
                "name": name,
                "field": field,
                "before": before,
                "after": after,
                "reason": note,
            }
            if apply:
                corrected = correct_automatic_topic(
                    session,
                    topic.id,
                    expected_hash=snapshot_hash(current),
                    body=body,
                    note=note,
                    actor=ACTOR,
                )
                item["correction_proposal_id"] = str(corrected.id)
            changes.append(item)
        for proposal in proposals:
            if proposal.evidence_url != "https://docs.webkit.org/Ports/WindowsPort.html":
                raise OperationConflict("The audited relationship evidence changed")
            note = (
                "The citation supports only Windows-port layout tests; the graph cannot "
                "express this qualification as an unqualified dependency."
            )
            if apply:
                retract_automatic_relationship(
                    session,
                    proposal.id,
                    expected_hash=proposal_hash(proposal),
                    note=note,
                    actor=ACTOR,
                )
            changes.append(
                {"relationship_proposal_id": str(proposal.id), "action": "retract", "reason": note}
            )
    return {"applied": apply, "changes": changes}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(repair(apply=args.apply), indent=2))
