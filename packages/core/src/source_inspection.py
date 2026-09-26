"""Complete partial source profiles from a fetched website page.

This module owns only the inspection contract: callers choose the guarded fetch
policy, and persistence stays with their application workflow.
"""

from collections.abc import Callable
from dataclasses import asdict
from urllib.parse import urlsplit, urlunsplit

from devfeed_core.feeds.fetcher import FeedError, FetchResult, fetch_page
from devfeed_core.source_profiles import PROFILE_FIELDS, merge_profile_candidates, website_profile

ProfileValues = dict[str, str | None]


def complete_website_profile(
    values: ProfileValues,
    website_url: str | None,
    *,
    fetch: Callable[[str], FetchResult] = fetch_page,
    fallback_to_root: bool = False,
) -> ProfileValues:
    """Fill missing profile fields from a website, preserving feed values.

    The caller supplies the safe transport so previews and background enrichment
    can keep their distinct response limits. Background callers may opt into one
    root-page retry when a feed URL was mistakenly declared as the website.
    """
    if not website_url or not any(not values.get(field) for field in PROFILE_FIELDS):
        return merge_profile_candidates(values)

    try:
        result = fetch(website_url)
    except FeedError as exc:
        parts = urlsplit(website_url)
        root = urlunsplit((parts.scheme, parts.netloc, "/", "", ""))
        if not fallback_to_root or exc.reason != "unsupported_content_type" or website_url == root:
            raise
        result = fetch(root)

    return merge_profile_candidates(values, asdict(website_profile(result)))
