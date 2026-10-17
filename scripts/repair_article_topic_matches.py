"""Preview explicitly reviewed article-topic corrections; apply only saved plans.

Supply JSON {"corrections": [{"article_id": UUID, "remove_topic_ids": [UUID],
"topics": [], "actor": "operator", "note": "Reason supported by article evidence"}]}.
Run with --file INPUT.json and save the preview JSON. Review its exact removals,
replacements and publication reset, then run --file PREVIEW.json --apply.
For example:
  python scripts/repair_article_topic_matches.py --file requests.json > reviewed-plan.json
  python scripts/repair_article_topic_matches.py --file reviewed-plan.json --apply
Saved plans bind the source, editorial revision, assignments, and topic definitions;
changed records must be inspected again. An unchanged applied plan is a no-op.
Only an explicit restore_publication=true can restore an already published
article, using the normal editorial approval and publication requirements.
The transaction is bounded to 25 explicitly named articles, with no inference,
taxonomy edits, scheduling or automatic article enumeration.
"""

import argparse
import json
from pathlib import Path

from devfeed_core.article_topic_corrections import (
    ArticleTopicCorrection,
    TopicCorrectionIntent,
    apply_article_topic_correction,
    preview_article_topic_correction,
)
from devfeed_core.db import session_factory
from sqlalchemy import text


def repair(document: dict, *, apply=False) -> dict:
    corrections = document.get("corrections")
    if not isinstance(corrections, list) or not 1 <= len(corrections) <= 25:
        raise ValueError("Supply between 1 and 25 explicit article corrections")
    bodies = [
        ArticleTopicCorrection.model_validate(item)
        if apply
        else TopicCorrectionIntent.model_validate(
            {
                key: value
                for key, value in item.items()
                if key not in {"expected_revision", "expected_state_hash"}
            }
        )
        for item in corrections
    ]
    if len({body.article_id for body in bodies}) != len(bodies):
        raise ValueError("Each article can be corrected only once per batch")
    changes, plans = [], []
    with session_factory().begin() as session:
        if not apply:
            session.execute(text("SET TRANSACTION READ ONLY"))
        session.execute(text("SET LOCAL lock_timeout = '5s'"))
        session.execute(text("SET LOCAL statement_timeout = '30s'"))
        for body in sorted(bodies, key=lambda body: body.article_id):
            if isinstance(body, ArticleTopicCorrection):
                changes.append(apply_article_topic_correction(session, body))
                plans.append(body.model_dump(mode="json"))
            else:
                preview = preview_article_topic_correction(session, body)
                plans.append(preview.pop("correction"))
                changes.append(preview)
    return {"applied": apply, "corrections": plans, "changes": changes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.file.stat().st_size > 128_000:
        parser.error("Correction JSON exceeds 128 KB")
    print(json.dumps(repair(json.loads(args.file.read_text()), apply=args.apply), indent=2))


if __name__ == "__main__":
    main()
