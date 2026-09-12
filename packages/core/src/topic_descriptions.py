"""Convert publisher and model Markdown into plain topic description prose."""

from markdown_it import MarkdownIt

from devfeed_core.feeds.parser import plain_text

_MARKDOWN = MarkdownIt("commonmark").enable(["table", "strikethrough"])


def plain_topic_description(value: str | None) -> str | None:
    if value is None:
        return None
    # Parse syntax rather than deleting punctuation used in developer terminology.
    # Rendering happens only in memory; neither links nor images are fetched.
    markup = _MARKDOWN.render(value)
    return plain_text(markup, max(len(markup), 1)) or None
