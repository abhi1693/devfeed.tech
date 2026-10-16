"""Topic-specific entry points into the shared managed logo pipeline."""

from devfeed_core.logos import backfill_logos, request_logo
from devfeed_core.models import Topic


def request_topic_logo(session, topic_id, *, automatic=False):
    return request_logo(session, Topic, topic_id, automatic=automatic)


def backfill_topic_logos(session, limit=100):
    return backfill_logos(session, Topic, limit)
