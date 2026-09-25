"""Canonical kinds for topics shown in the reader and developer profiles."""

from typing import Literal, get_args

TopicKind = Literal[
    "benchmark",
    "competition",
    "concept",
    "dataset",
    "database",
    "discipline",
    "event",
    "format",
    "framework",
    "game",
    "game_engine",
    "hardware",
    "language",
    "library",
    "license",
    "model",
    "operating_system",
    "organization",
    "package",
    "platform",
    "practice",
    "product",
    "program",
    "protocol",
    "publication",
    "runtime",
    "service",
    "software",
    "standard",
    "technology",
    "tool",
    "unclassified",
]

TOPIC_KINDS: tuple[str, ...] = get_args(TopicKind)
