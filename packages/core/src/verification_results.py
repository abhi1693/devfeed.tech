"""Typed, defensive users for persisted verdicts, including legacy/missing data.

Reading a verdict never authorizes approval; domain verifiers still validate the
complete versioned result, exact input hash, relevance, and source evidence.
"""

from dataclasses import dataclass
from typing import Literal

VerdictState = Literal["missing", "invalid", "supported", "unsupported", "uncertain"]


@dataclass(frozen=True)
class TopicVerdict:
    state: VerdictState
    version: str | None = None
    input_hash: str | None = None

    @property
    def uncertain(self) -> bool:
        return self.state == "uncertain"


def read_topic_verdict(value: object) -> TopicVerdict:
    if not value:
        return TopicVerdict("missing")
    if not isinstance(value, dict) or not isinstance(value.get("check"), dict):
        return TopicVerdict("invalid")
    check = value["check"]
    relevance = check.get("relevance")
    verdict = check.get("verdict")
    state: VerdictState
    if verdict == "uncertain" or (
        isinstance(relevance, dict) and relevance.get("verdict") == "uncertain"
    ):
        state = "uncertain"
    elif verdict == "supported":
        state = "supported"
    elif verdict == "unsupported":
        state = "unsupported"
    else:
        state = "invalid"
    return TopicVerdict(
        state,
        value.get("version") if isinstance(value.get("version"), str) else None,
        check.get("input_hash") if isinstance(check.get("input_hash"), str) else None,
    )
