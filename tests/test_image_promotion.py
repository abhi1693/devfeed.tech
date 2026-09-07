"""Release tags cannot move; branch aliases can advance to verified digests."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "publish_images", Path(__file__).resolve().parents[1] / "scripts/ci/publish_images.py"
)
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)
OLD = "sha256:" + "a" * 64
NEW = "sha256:" + "b" * 64
IMAGES = {name: f"ghcr.io/owner/repo/{name}@{NEW}" for name in ("backend", "admin-api", "admin")}


def test_branch_tags_are_mutable_and_collision_resistant():
    assert publisher.publication_tag("branch", "master", "0.0.1") == ("master", False)
    first, immutable = publisher.publication_tag("branch", "feature/foo", "0.0.1")
    second, _ = publisher.publication_tag("branch", "feature-foo", "0.0.1")
    assert first != second and not immutable
    assert len(first) <= 128


def test_release_tags_require_matching_versions():
    assert publisher.publication_tag("tag", "v0.0.1", "0.0.1") == ("v0.0.1", True)
    with pytest.raises(ValueError):
        publisher.publication_tag("tag", "v0.0.2", "0.0.1")


def test_release_conflict_checks_all_images_before_any_write(monkeypatch):
    monkeypatch.setattr(publisher, "inspect_digest", lambda ref: OLD if "/admin:" in ref else None)
    monkeypatch.setattr(publisher.subprocess, "run", lambda *a, **kw: pytest.fail("Registry write"))
    with pytest.raises(ValueError, match="already exists"):
        publisher.promote(IMAGES, "v0.0.1", True)


def test_retry_of_same_release_digest_is_idempotent(monkeypatch):
    monkeypatch.setattr(publisher, "inspect_digest", lambda ref: NEW)
    monkeypatch.setattr(publisher.subprocess, "run", lambda *a, **kw: pytest.fail("Registry write"))
    assert len(publisher.promote(IMAGES, "v0.0.1", True)) == 3


def test_mutable_branch_promotes_exact_verified_digests(monkeypatch):
    registry = {}
    monkeypatch.setattr(publisher, "inspect_digest", lambda ref: registry.get(ref, OLD))

    def create(command, **kwargs):
        assert command[:5] == ["docker", "buildx", "imagetools", "create", "--tag"]
        registry[command[5]] = command[6].rsplit("@", 1)[1]

    monkeypatch.setattr(publisher.subprocess, "run", create)
    publisher.promote(IMAGES, "master", False)
    assert len(registry) == 3 and set(registry.values()) == {NEW}


@pytest.mark.parametrize("error", ["unauthorized", "TLS handshake timeout", "no such host"])
def test_registry_errors_are_not_treated_as_absent_tags(monkeypatch, error):
    monkeypatch.setattr(
        publisher.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=1, stderr=error)
    )
    with pytest.raises(RuntimeError):
        publisher.inspect_digest("ghcr.io/owner/repo:v0.0.1")


def test_missing_manifest_is_distinct_from_registry_failure(monkeypatch):
    reference = "ghcr.io/owner/repo:v0.0.1"
    monkeypatch.setattr(
        publisher.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=1, stderr=f"ERROR: {reference}: not found\n"),
    )
    assert publisher.inspect_digest(reference) is None


def test_digest_is_read_from_registry_manifest(monkeypatch):
    monkeypatch.setattr(
        publisher.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout=json.dumps({"digest": NEW})),
    )
    assert publisher.inspect_digest("ghcr.io/owner/repo:master") == NEW
