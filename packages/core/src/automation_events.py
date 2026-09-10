"""Persist classification-relevant topic changes in the catalog transaction."""

import uuid

from sqlalchemy import event, inspect

from devfeed_core.cache_events import AppSession
from devfeed_core.config import get_settings
from devfeed_core.models import Topic, TopicReanalysis

FIELDS = ("name", "slug", "kind", "aliases", "keywords", "status")


@event.listens_for(AppSession, "before_flush")
def topic_changes(session, flush_context, instances):
    settings = get_settings()
    if not settings.ai_enabled or not settings.auto_reanalyze_topics:
        return
    for topic in list(session.new | session.dirty):
        if not isinstance(topic, Topic) or topic.status != "active":
            continue
        if not any(inspect(topic).attrs[field].history.has_changes() for field in FIELDS):
            continue
        if topic.id is None:
            topic.id = uuid.uuid4()
        snapshot = {
            field: getattr(topic, field) or ([] if field in {"aliases", "keywords"} else "")
            for field in FIELDS
        }
        # Removed names and aliases still identify articles affected by this edit.
        previous_terms = []
        for field in ("name", "slug", "aliases", "keywords"):
            for previous in inspect(topic).attrs[field].history.deleted:
                previous_terms.extend(previous if isinstance(previous, list) else [previous])
        snapshot["aliases"] = list(
            dict.fromkeys([*snapshot["aliases"], *filter(None, previous_terms)])
        )
        session.add(
            TopicReanalysis(
                topic_id=topic.id,
                topic=topic,
                topic_snapshot=snapshot,
            )
        )


@event.listens_for(AppSession, "before_flush")
def relationship_changes(session, flush_context, instances):
    # Persist changes even while AI/scheduling is disabled, so enabling it later
    # resumes current coverage. The generation order gives each pair one owner.
    from devfeed_core.models import TopicRelationshipScan, utcnow
    from devfeed_core.relationship_coverage import reset_scan
    from devfeed_core.topic_relationships import TOPIC_SNAPSHOT_FIELDS
    from devfeed_core.topics import lock_topics

    changed = [
        topic
        for topic in session.new | session.dirty
        if isinstance(topic, Topic)
        and any(
            inspect(topic).attrs[field].history.has_changes()
            for field in (*TOPIC_SNAPSHOT_FIELDS, "status")
        )
    ]
    if not changed:
        return
    lock_topics(session)
    pending = {
        scan.topic_id: scan for scan in session.new if isinstance(scan, TopicRelationshipScan)
    }
    for topic in changed:
        if topic.id is None:
            topic.id = uuid.uuid4()
        scan = pending.get(topic.id) or session.get(TopicRelationshipScan, topic.id)
        if topic.status == "active":
            reset_scan(session, topic, scan)
        elif scan is not None:
            scan.finished_at, scan.job_id = utcnow(), None


@event.listens_for(AppSession, "before_flush")
def tag_topic_changes(session, flush_context, instances):
    from devfeed_core.models import Tag
    from devfeed_core.tag_topic_discovery import (
        IDENTITY_FIELDS,
        catalog_changed,
        record_keys,
    )
    from devfeed_core.topics import lock_topics

    topics_changed = False
    for obj in session.new | session.dirty | session.deleted:
        state = inspect(obj)
        if isinstance(obj, Topic) and (
            obj in session.new | session.deleted
            or any(
                state.attrs[field].history.has_changes() for field in (*IDENTITY_FIELDS, "status")
            )
        ):
            obj.identity_keys = record_keys(obj)
            topics_changed = True
        elif isinstance(obj, Tag) and (
            obj in session.new
            or any(
                state.attrs[field].history.has_changes()
                for field in (*IDENTITY_FIELDS, "auto_link_topic")
            )
        ):
            # Direct ORM callers supplying a topic also express a manual choice.
            if obj in session.new and obj.auto_link_topic is None:
                obj.auto_link_topic = obj.topic_id is None
            obj.topic_match_revision = 0
            obj.topic_match_status = "pending" if obj.auto_link_topic else "manual"
            obj.topic_match_checked_at = None
    if topics_changed:
        lock_topics(session)
        catalog_changed(session)
