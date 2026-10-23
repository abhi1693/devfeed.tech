"""Source-label identity cache and import commands; database conflicts stay integration tests."""

import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_core import source_tags as labels
from devfeed_core.models import Article
from devfeed_core.urls import fingerprint


@pytest.fixture(autouse=True)
def empty_process_cache(monkeypatch):
    monkeypatch.setattr(labels, "_identity_snapshot", None)


def tag(slug, name, aliases):
    return SimpleNamespace(id=uuid.uuid4(), slug=slug, name=name, aliases=aliases)


def test_exact_catalog_identities_win_over_aliases_and_reused_snapshots_are_immutable():
    first, second = (
        tag("a-runtime", "Runtime", ["Shared", "Python"]),
        tag("python", "Python", ["Shared"]),
    )
    session = Mock()
    session.scalar.side_effect = [7, 7, 7]
    session.execute.return_value.all.return_value = [first, second]
    identities, slugs = labels._source_tag_index(session)
    assert identities["python"] == second.id and identities["shared"] == first.id
    assert slugs["a-runtime"] == first.id and slugs["python"] == second.id
    with pytest.raises(TypeError):
        identities["python"] = first.id
    again = labels._source_tag_index(session)
    assert again == (identities, slugs)
    assert session.execute.call_count == 1
    assert session.flush.call_count == 2


@pytest.mark.parametrize("change", ["revision", "binding", "uncached"])
def test_catalog_snapshot_is_not_reused_across_revisions_bindings_or_unknown_revisions(change):
    session = Mock()
    first, replacement = tag("python", "Python", []), tag("python", "Python runtime", [])
    session.scalar.side_effect = (
        [3, 3, None]
        if change == "uncached"
        else [3, 3, 4, 4]
        if change == "revision"
        else [3, 3, 3, 3]
    )
    session.execute.return_value.all.side_effect = [[first], [replacement]]
    if change == "binding":
        session.get_bind.side_effect = [object(), object()]
    labels._source_tag_index(session)
    identities, _ = labels._source_tag_index(session)
    assert identities["python runtime"] == replacement.id and "python" in identities
    assert session.execute.call_count == 2


def test_catalog_changed_during_read_is_not_published_as_a_stable_revision():
    session = Mock()
    session.scalar.side_effect = [3, 4, 4, 4]
    first, replacement = tag("python", "Python", []), tag("python", "Python runtime", [])
    session.execute.return_value.all.side_effect = [[first], [replacement]]
    labels._source_tag_index(session)
    identities, _ = labels._source_tag_index(session)
    assert identities["python runtime"] == replacement.id
    assert session.execute.call_count == 2


def test_empty_or_nonsemantic_feed_labels_do_not_read_or_write_the_catalog():
    session = Mock()
    assert labels.resolve_source_tags(session, [None, {}, " ", "---"]) == ({}, 0)
    session.flush.assert_not_called()
    session.execute.assert_not_called()


def test_resolution_reuses_canonical_or_alias_names_and_creates_only_missing_slugs(monkeypatch):
    canonical, aliased, new_a, new_z = [uuid.uuid4() for _ in range(4)]
    index = Mock(return_value=({"python": canonical, "py": aliased}, {"python": canonical}))
    monkeypatch.setattr(labels, "_source_tag_index", index)
    session = Mock()
    session.scalars.return_value.all.return_value = [new_a, new_z]
    session.execute.return_value.tuples.return_value.all.return_value = [
        ("alpha", new_a),
        ("zulu", new_z),
    ]
    resolved, created = labels.resolve_source_tags(
        session, [" Zulu ", "PY", "Alpha", "Python", "alpha"]
    )
    assert created == 2 and resolved == {
        "zulu": new_z,
        "py": aliased,
        "alpha": new_a,
        "python": canonical,
    }
    parameters = session.scalars.call_args.args[0].compile().params
    assert {
        key: value
        for key, value in parameters.items()
        if key.startswith(("name_", "slug_", "aliases_"))
    } == {
        "name_m0": "Alpha",
        "slug_m0": "alpha",
        "aliases_m0": [],
        "name_m1": "Zulu",
        "slug_m1": "zulu",
        "aliases_m1": [],
    }


def test_resolution_uses_an_existing_slug_when_a_feed_label_normalizes_to_it(monkeypatch):
    identifier = uuid.uuid4()
    monkeypatch.setattr(labels, "_source_tag_index", lambda _: ({}, {"c-plus-plus": identifier}))
    session = Mock()
    assert labels.resolve_source_tags(session, ["C++"]) == ({"c++": identifier}, 0)
    session.scalars.assert_not_called()
    session.execute.assert_not_called()


@pytest.mark.parametrize("guard", ["empty", "missing", "manual"])
def test_source_labels_do_not_override_an_explicit_manual_classification(guard):
    identifier = uuid.uuid4()
    article = Article(
        id=identifier, classification_provenance={"origin": "manual"} if guard == "manual" else {}
    )
    session = Mock()
    session.scalar.return_value = None if guard == "missing" else article
    assert (
        labels.attach_source_tags(session, identifier, [] if guard == "empty" else [uuid.uuid4()])
        == 0
    )
    session.scalars.assert_not_called()
    session.expire.assert_not_called()
    if guard == "empty":
        session.scalar.assert_not_called()


def test_source_attachment_deduplicates_command_ids_and_expires_only_loaded_tag_projection():
    identifier = uuid.uuid4()
    first, second = uuid.UUID(int=1), uuid.UUID(int=2)
    article = Article(
        id=identifier,
        canonical_url="https://publisher.example/article",
        url_hash=fingerprint("https://publisher.example/article"),
        title="Source tag import",
        review_status="pending",
        publication_status="unpublished",
        classification_provenance={"origin": "ai"},
    )
    session = Mock()
    session.scalar.return_value = article
    session.scalars.return_value.all.return_value = [first, second]
    assert labels.attach_source_tags(session, identifier, [second, first, second]) == 2
    params = session.scalars.call_args.args[0].compile().params
    assert params["tag_id_m0"] == first and params["tag_id_m1"] == second
    assert params["origin_m0"] == params["origin_m1"] == "source"
    assert params["article_id_m0"] == params["article_id_m1"] == identifier
    session.expire.assert_called_once_with(article, ["tags"])
