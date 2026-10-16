"""Plain, single-line product copy with a bounded import conversion."""

import re
from typing import Annotated

from markdown_it import MarkdownIt
from pydantic import BeforeValidator, StringConstraints

from devfeed_core.feeds.parser import plain_text

TAGLINE_MAX_LENGTH = 160
_MARKDOWN = MarkdownIt("commonmark").enable(["table", "strikethrough"])
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _plain_tagline(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Tagline must be text")
    if _CONTROL.search(value):
        raise ValueError("Tagline contains an invalid control character")
    # Imported descriptions may contain Markdown or HTML. Parse their syntax without
    # fetching embedded media, retaining punctuation in names such as C++ and .NET.
    markup = _MARKDOWN.render(value[:100_000])
    return plain_text(markup, max(len(markup), 1))


def normalize_tagline(value: object) -> str:
    if isinstance(value, str) and len(" ".join(value.split())) > TAGLINE_MAX_LENGTH:
        raise ValueError(f"Tagline must be at most {TAGLINE_MAX_LENGTH} characters")
    return _plain_tagline(value)


def imported_tagline(value: object) -> str:
    """Keep short upstream copy intact and condense longer imported summaries."""
    tagline = _plain_tagline(value)
    if len(tagline) > TAGLINE_MAX_LENGTH:
        tagline = re.split(r"(?<=[.!?])\s+", tagline, maxsplit=1)[0]
    if len(tagline) > TAGLINE_MAX_LENGTH:
        prefix = tagline[: TAGLINE_MAX_LENGTH - 1]
        if not tagline[TAGLINE_MAX_LENGTH - 1].isspace() and " " in prefix:
            prefix = prefix.rsplit(" ", 1)[0]
        tagline = prefix.rstrip(" ,;:") + "…"
    return tagline


Tagline = Annotated[
    str,
    BeforeValidator(normalize_tagline),
    StringConstraints(min_length=3, max_length=TAGLINE_MAX_LENGTH),
]
