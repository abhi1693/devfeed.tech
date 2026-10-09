"""Publisher logo scheduling reuses the guarded, checkpointed logo pipeline."""

from devfeed_core.models import Source
from devfeed_core.topic_logos import backfill_logos, request_logo


def request_source_logo(session, source_id, *, automatic=False, refresh=False):
    return request_logo(session, Source, source_id, automatic=automatic, refresh=refresh)


def backfill_source_logos(session, limit=100):
    return backfill_logos(session, Source, limit)
