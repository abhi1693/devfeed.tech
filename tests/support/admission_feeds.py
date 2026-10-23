"""Dated RSS/Atom samples for source admission (separate from parser fixtures)."""

from datetime import UTC, datetime


def with_admission_entries(body: bytes) -> bytes:
    date = datetime.now(UTC).isoformat()
    if b"</channel>" in body or b"<channel/>" in body:
        entries = "".join(
            f"<item><title>Article {i}</title><link>https://example.com/admission/{i}</link>"
            f"<pubDate>{date}</pubDate></item>"
            for i in range(3)
        ).encode()
        return body.replace(b"<channel/>", b"<channel></channel>").replace(
            b"</channel>", entries + b"</channel>"
        )
    entries = "".join(
        f'<entry><title>Article {i}</title><link href="https://example.com/admission/{i}"/>'
        f"<updated>{date}</updated></entry>"
        for i in range(3)
    ).encode()
    return body.replace(b"</feed>", entries + b"</feed>")
