"""Shared literal text search for admin lists and entity pickers."""

from devfeed_core.models import Topic
from sqlalchemy import case, func, literal, or_


def normalized_text(value):
    # Punctuation and whitespace separate words. Keep Unicode letters/numbers
    # and the meaningful symbols in technology names such as C++ and C#.
    return func.trim(
        func.regexp_replace(func.lower(func.coalesce(value, "")), "[^[:alnum:]#+]+", " ", "g")
    )


def text_search(query: str, *columns):
    """Normalize both sides before filtering/counting/paging, never the data.

    A punctuation-only query remains a literal search instead of becoming an
    empty substring that would match every row. strpos has no LIKE wildcards.
    """
    needle = normalized_text(literal(query))
    return or_(
        *[
            case(
                (needle == "", column.icontains(query, autoescape=True)),
                else_=func.strpos(normalized_text(column), needle) > 0,
            )
            for column in columns
        ]
    )


def topic_search(query: str):
    """Return every matching topic, including those sharing an abbreviation."""
    return text_search(query, Topic.name, Topic.slug, func.array_to_string(Topic.aliases, " "))
