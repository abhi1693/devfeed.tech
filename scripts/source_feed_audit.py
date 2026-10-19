"""Read-only publisher-feed audit; never changes approval, enablement or source identity.

Run: uv run python scripts/source_feed_audit.py --feed-url URL --website-url URL
Only configured URLs and feed links advertised by the publisher are considered.
"""

import argparse
import json

from devfeed_core.discovery_crawler import CrawlSession, Links, feed_evidence
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.urls import validate_public_url


def audit(feed_url: str, website_url: str) -> dict:
    feed_url = validate_public_url(feed_url)
    website_url = validate_public_url(website_url)
    crawler = CrawlSession()
    result: dict = {"feed_url": feed_url, "website_url": website_url, "working_feeds": []}
    try:
        response = crawler.get(feed_url)
        feed_evidence(response.body, response.final_url, website_url)
        result["working_feeds"].append(response.final_url)
        result["configured_feed"] = "working"
    except (FeedError, ValueError) as error:
        result["configured_feed"] = "failed"
        result["failure_reason"] = error.reason if isinstance(error, FeedError) else "invalid_feed"
        result["http_status"] = error.status if isinstance(error, FeedError) else None
    try:
        page = crawler.get(website_url)
        links = Links(page.final_url)
        links.feed(page.body.decode("utf-8", errors="replace"))
        for candidate in dict.fromkeys(links.feeds[:5]):
            try:
                candidate = validate_public_url(candidate)
                response = crawler.get(candidate)
                feed_evidence(response.body, response.final_url, page.final_url)
                if response.final_url not in result["working_feeds"]:
                    result["working_feeds"].append(response.final_url)
            except (FeedError, ValueError):
                continue
    except (FeedError, ValueError):
        result["website_check"] = "unavailable"
    result["next_action"] = (
        "review_verified_feed" if result["working_feeds"] else "editorial_decision_required"
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feed-url", required=True)
    parser.add_argument("--website-url", required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.feed_url, args.website_url), indent=2))
