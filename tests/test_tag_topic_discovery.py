"""Automatic tag links against real transactions and the scheduler entry point."""

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from devfeed_aggregator import scheduler
from devfeed_core import services
from devfeed_core.automation_scheduler import schedule_automation
from devfeed_core.config import get_settings
from devfeed_core.models import Tag, Topic
from devfeed_core.schemas import TagPatch, TagWrite
from devfeed_core.source_tags import resolve_source_tags
from devfeed_core.tag_topic_discovery import schedule_tag_topic_discovery
from devfeed_core.topic_deletion import delete_topic
from sqlalchemy import select

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def automatic(unit_test_settings, monkeypatch):
    monkeypatch.setenv("DEVFEED_AUTO_LINK_TAGS", "true")
    get_settings.cache_clear()


def topic(database, name="Kubernetes", slug="kubernetes", aliases=None, status="active"):
    with database.begin() as session:
        row = Topic(name=name, slug=slug, kind="technology", aliases=aliases or [], status=status)
        session.add(row)
        session.flush()
        return row.id


def tag(database, name="k8s", slug="k8s", **fields):
    with database.begin() as session:
        return services.create_tag(session, TagWrite(name=name, slug=slug, **fields)).id


def read(database, identifier):
    with database() as session:
        return session.get(Tag, identifier)


def test_scheduler_discovers_feed_imports_without_ai_and_does_not_repeat(database):
    target = topic(database, aliases=["k8s"])
    with database.begin() as session:
        ids, created = resolve_source_tags(session, ["K8S", "Unrelated"])
    assert created == 2
    counts = scheduler.tick()
    assert counts["tags_scanned"] == 2
    assert counts["tags_linked"] == 1
    assert read(database, ids["k8s"]).topic_id == target
    assert read(database, ids["unrelated"]).topic_match_status == "unmatched"
    assert schedule_automation(database)["tags_scanned"] == 0
    assert not get_settings().ai_enabled


def test_existing_tags_are_revisited_when_a_topic_arrives_or_is_approved(database):
    identifier = tag(database)
    assert schedule_automation(database)["tags_scanned"] == 1
    target = topic(database, aliases=["k8s"], status="proposed")
    schedule_automation(database)
    assert read(database, identifier).topic_id is None
    with database.begin() as session:
        session.get(Topic, target).status = "active"
    assert schedule_automation(database)["tags_linked"] == 1
    assert read(database, identifier).topic_id == target


def test_shared_aliases_and_conflicting_tag_aliases_never_choose_arbitrarily(database):
    first = topic(database, name="Alpha", slug="alpha", aliases=["common"])
    topic(database, name="Beta", slug="beta", aliases=["common"])
    shared = tag(database, name="Common", slug="common")
    conflict = tag(database, name="Alpha", slug="alpha", aliases=["Beta"])
    exact = tag(database, name="Alpha tag", slug="alpha-tag", aliases=["Alpha"])
    assert schedule_automation(database)["tags_ambiguous"] == 2
    for identifier in (shared, conflict):
        row = read(database, identifier)
        assert row.topic_id is None and row.topic_match_status == "ambiguous"
    assert read(database, exact).topic_id == first


def test_later_collision_clears_automatic_link_then_removal_resolves_it(database):
    first = topic(database, aliases=["k8s"])
    identifier = tag(database)
    schedule_automation(database)
    second = topic(database, name="Other", slug="other", aliases=["k8s"])
    assert schedule_automation(database)["tags_unlinked"] == 1
    assert read(database, identifier).topic_match_status == "ambiguous"
    with database.begin() as session:
        delete_topic(session, second, {"subject": "test"})
    assert schedule_automation(database)["tags_linked"] == 1
    assert read(database, identifier).topic_id == first


def test_manual_link_clear_and_opt_out_survive_catalog_changes(database):
    target = topic(database, aliases=["k8s"])
    manual = tag(database, name="Manual", slug="manual", topic_id=target)
    disabled = tag(database, name="Kubernetes", slug="kubernetes", auto_link_topic=False)
    cleared = tag(database)
    schedule_automation(database)
    with database.begin() as session:
        services.update_tag(session, cleared, TagPatch(topic_id=None))
        session.get(Topic, target).aliases = ["k8s", "another"]
    schedule_automation(database)
    assert read(database, manual).topic_id == target
    assert not read(database, manual).auto_link_topic
    for identifier in (disabled, cleared):
        row = read(database, identifier)
        assert row.topic_id is None and row.topic_match_status == "manual"
    with database.begin() as session:
        services.update_tag(session, cleared, TagPatch(auto_link_topic=True))
    assert schedule_automation(database)["tags_linked"] == 1
    assert read(database, cleared).topic_id == target


def test_tag_rename_and_alias_edit_recheck_without_topic_changes(database):
    target = topic(database, aliases=["k8s"])
    identifier = tag(database)
    schedule_automation(database)
    with database.begin() as session:
        services.update_tag(session, identifier, TagPatch(name="Unrelated", slug="unrelated"))
    assert schedule_automation(database)["tags_unlinked"] == 1
    with database.begin() as session:
        services.update_tag(session, identifier, TagPatch(aliases=["Kubernetes"]))
    assert schedule_automation(database)["tags_linked"] == 1
    assert read(database, identifier).topic_id == target


def test_retired_topic_and_removed_alias_clear_only_automatic_links(database):
    target = topic(database, aliases=["k8s"])
    identifier = tag(database)
    manual = tag(database, name="Manual", slug="manual", topic_id=target)
    schedule_automation(database)
    with database.begin() as session:
        session.get(Topic, target).aliases = []
    assert schedule_automation(database)["tags_unlinked"] == 1
    with database.begin() as session:
        session.get(Topic, target).aliases = ["k8s"]
    schedule_automation(database)
    with database.begin() as session:
        session.get(Topic, target).status = "rejected"
    assert schedule_automation(database)["tags_unlinked"] == 1
    assert read(database, identifier).topic_id is None
    assert read(database, manual).topic_id == target


def test_irrelevant_topic_edits_do_not_reopen_discovery(database):
    target = topic(database, aliases=["k8s"])
    tag(database)
    schedule_automation(database)
    with database.begin() as session:
        row = session.get(Topic, target)
        row.description = "New description"
        row.keywords = ["clusters"]
    assert schedule_automation(database)["tags_scanned"] == 0


def test_batches_resume_and_concurrent_schedulers_do_not_repeat_work(database, monkeypatch):
    monkeypatch.setenv("DEVFEED_AUTOMATION_BATCH_SIZE", "2")
    get_settings.cache_clear()
    topic(database)
    with database.begin() as session:
        for number in range(7):
            session.add(
                Tag(
                    id=uuid.UUID(int=number + 10),
                    name=f"Tag {number}",
                    slug=f"tag-{number}",
                    aliases=["Kubernetes"],
                )
            )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: schedule_tag_topic_discovery(database), range(2)))
    assert [item["tags_scanned"] for item in results] == [2, 2]
    assert schedule_tag_topic_discovery(database)["tags_scanned"] == 2
    assert schedule_tag_topic_discovery(database)["tags_scanned"] == 1
    # A new UUID behind prior batches must also be discovered.
    with database.begin() as session:
        session.add(Tag(id=uuid.UUID(int=1), name="New tag", slug="new", aliases=["Kubernetes"]))
    assert schedule_tag_topic_discovery(database)["tags_linked"] == 1
    assert schedule_tag_topic_discovery(database)["tags_scanned"] == 0


def test_busy_tag_is_skipped_then_revisited_and_manual_edit_wins(database):
    topic(database, aliases=["k8s"])
    identifier = tag(database)
    with database.begin() as editor:
        editor.scalar(select(Tag).where(Tag.id == identifier).with_for_update())
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert (
                pool.submit(schedule_tag_topic_discovery, database).result(timeout=5)[
                    "tags_scanned"
                ]
                == 0
            )
    assert schedule_tag_topic_discovery(database)["tags_linked"] == 1
    with database.begin() as editor:
        services.update_tag(editor, identifier, TagPatch(topic_id=None))
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert (
                pool.submit(schedule_tag_topic_discovery, database).result(timeout=5)[
                    "tags_scanned"
                ]
                == 0
            )
    assert schedule_tag_topic_discovery(database)["tags_scanned"] == 0
    assert read(database, identifier).topic_id is None


def test_disabled_discovery_resumes_current_catalog(database, monkeypatch):
    monkeypatch.setenv("DEVFEED_AUTO_LINK_TAGS", "false")
    get_settings.cache_clear()
    topic(database, aliases=["k8s"])
    identifier = tag(database)
    assert schedule_automation(database)["tags_scanned"] == 0
    monkeypatch.setenv("DEVFEED_AUTO_LINK_TAGS", "true")
    get_settings.cache_clear()
    assert schedule_automation(database)["tags_linked"] == 1
    assert read(database, identifier).topic_match_status == "matched"


def test_interrupted_batch_rolls_back_links_and_retries(database, monkeypatch):
    from devfeed_core import tag_topic_discovery

    topic(database, aliases=["k8s"])
    identifier = tag(database)
    with monkeypatch.context() as patch:

        def interrupted():
            raise RuntimeError("Scheduler interrupted before commit")

        patch.setattr(tag_topic_discovery, "utcnow", interrupted)
        with pytest.raises(RuntimeError, match="interrupted"):
            schedule_tag_topic_discovery(database)
    row = read(database, identifier)
    assert row.topic_id is None and row.topic_match_status == "pending"
    assert schedule_tag_topic_discovery(database)["tags_linked"] == 1


def test_explicit_topic_replacement_becomes_a_manual_mapping(database):
    old = topic(database, aliases=["k8s"])
    replacement = topic(database, name="Replacement", slug="replacement")
    identifier = tag(database)
    schedule_automation(database)
    with database.begin() as session:
        delete_topic(session, old, {"subject": "test"}, replacement_id=replacement)
    assert schedule_automation(database)["tags_scanned"] == 0
    row = read(database, identifier)
    assert row.topic_id == replacement and not row.auto_link_topic
    assert row.topic_match_status == "manual"


def test_api_exposes_discovery_and_can_restore_it_after_manual_clear(admin_client, database):
    topic(database, aliases=["k8s"])
    response = admin_client.post("/v1/admin/tags", json={"name": "k8s", "slug": "k8s"})
    assert response.status_code == 201
    identifier = response.json()["id"]
    assert response.json()["auto_link_topic"] is True
    schedule_automation(database)
    url = f"/v1/admin/tags/{identifier}"
    assert admin_client.get(url).json()["topic_match_status"] == "matched"
    assert admin_client.patch(url, json={"topic_id": None}).json()["auto_link_topic"] is False
    assert admin_client.patch(url, json={"auto_link_topic": None}).status_code == 422
    assert admin_client.patch(url, json={"auto_link_topic": True}).status_code == 200
    assert schedule_automation(database)["tags_linked"] == 1
