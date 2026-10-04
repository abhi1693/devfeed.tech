"""Release preparation preserves immutable bytes and verifies signed provenance."""

import importlib.util
import json
import subprocess
import textwrap
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, ZipInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "release_assets", ROOT / "scripts/sync_extension_release_assets.py"
)
assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assets)


def archive(payload=b"built from tag", year=2026):
    stream = BytesIO()
    with ZipFile(stream, "w") as zipped:
        zipped.writestr(ZipInfo("index.html", (year, 1, 1, 0, 0, 0)), payload)
    return stream.getvalue()


@pytest.fixture
def release(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(assets, "_version", lambda: "1.2.3")
    for key, value in {
        "GITHUB_REPOSITORY": "owner/app",
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "2",
    }.items():
        monkeypatch.setenv(key, value)
    for package in assets.packages():
        package.parent.mkdir(parents=True, exist_ok=True)
        package.write_bytes(archive())
    state = {"isDraft": True, "remote": {}, "commands": [], "exists": True}

    def run(*command):
        state["commands"].append(command)
        if command[:3] == ("gh", "release", "view"):
            if not state["exists"]:
                raise subprocess.CalledProcessError(1, command)
            return json.dumps(
                {"isDraft": state["isDraft"], "assets": [{"name": n} for n in state["remote"]]}
            )
        if command[:3] == ("gh", "release", "create"):
            assert not state["exists"]
            state["exists"] = True
        elif command[:3] == ("gh", "release", "upload"):
            for filename in command[4:]:
                path = Path(filename)
                assert path.name not in state["remote"]
                state["remote"][path.name] = path.read_bytes()
        elif command[:3] == ("gh", "release", "download"):
            destination = Path(command[command.index("--dir") + 1])
            name = command[command.index("--pattern") + 1]
            (destination / name).write_bytes(state["remote"][name])
        elif command[:3] == ("gh", "attestation", "verify"):
            assert "--bundle" in command
            assert "--deny-self-hosted-runners" in command
            assert command[command.index("--source-ref") + 1] == "refs/tags/v1.2.3"
            assert command[command.index("--source-digest") + 1] == "a" * 40
            assert command[command.index("--signer-workflow") + 1] == (
                "owner/app/.github/workflows/extension-release.yml"
            )
        elif command == ("git", "rev-parse", "HEAD"):
            return "a" * 40
        else:
            pytest.fail(f"Unexpected release command: {command}")
        return ""

    monkeypatch.setattr(assets, "run", run)
    return state


def test_existing_packages_use_exact_published_bytes_after_comparing_payloads(release):
    for package in assets.packages():
        release["remote"][package.name] = archive(year=2025)
    assets.sync_packages("v1.2.3", draft_only=True)
    for package in assets.packages():
        assert package.read_bytes() == release["remote"][package.name]
    assert not any(command[2] == "upload" for command in release["commands"])


def test_missing_packages_are_uploaded_without_replacing_assets(release):
    assets.sync_packages("v1.2.3", draft_only=True)
    assert release["remote"] == {path.name: path.read_bytes() for path in assets.packages()}
    assert not any("--clobber" in command for command in release["commands"])


def test_changed_package_payload_cannot_be_attached_to_existing_release(release):
    release["remote"][assets.packages()[0].name] = archive(b"different source")
    with pytest.raises(SystemExit, match="differs from the package built"):
        assets.sync_packages("v1.2.3", draft_only=True)
    assert not any(command[2] == "upload" for command in release["commands"])


def test_signed_preparation_refuses_published_releases(release):
    release["isDraft"] = False
    with pytest.raises(SystemExit, match="draft releases only"):
        assets.sync_packages("v1.2.3", draft_only=True)
    assert len(release["commands"]) == 1


def test_legacy_published_release_can_verify_existing_packages(release):
    release["isDraft"] = False
    release["remote"] = {path.name: path.read_bytes() for path in assets.packages()}
    assets.sync_packages("v1.2.3")
    assert not any(command[2] == "upload" for command in release["commands"])


def test_duplicate_archive_paths_are_rejected(tmp_path):
    path = tmp_path / "duplicate.zip"
    with ZipFile(path, "w") as zipped:
        zipped.writestr("index.html", "first")
        with pytest.warns(UserWarning, match="Duplicate name"):
            zipped.writestr("index.html", "second")
    with pytest.raises(SystemExit, match="duplicate paths"):
        assets.archive_contents(path)


def test_tag_preparation_creates_only_a_draft_and_requires_the_existing_tag(release):
    release["exists"] = False
    assets.prepare_draft("v1.2.3")
    creation = next(command for command in release["commands"] if command[2] == "create")
    assert "--draft" in creation
    assert "--verify-tag" in creation


def test_tag_preparation_does_not_reopen_a_published_release(release):
    release["isDraft"] = False
    with pytest.raises(SystemExit, match="draft releases only"):
        assets.prepare_draft("v1.2.3")
    assert len(release["commands"]) == 1


def test_bundle_is_verified_for_both_packages_then_attached_without_overwriting(release, tmp_path):
    bundle = tmp_path / "bundle.json"
    bundle.write_text('{"signed": "test fixture; verification is mocked"}')
    assets.attach_attestation("v1.2.3", bundle)
    verification = [c for c in release["commands"] if c[:3] == ("gh", "attestation", "verify")]
    assert {c[3] for c in verification} == {str(p) for p in assets.packages()}
    assert release["remote"] == {
        "devfeed-extensions-1.2.3-123-2.sigstore.json": bundle.read_bytes()
    }


@pytest.mark.parametrize("failed_package", [0, 1])
def test_invalid_bundle_cannot_be_uploaded(release, tmp_path, monkeypatch, failed_package):
    bundle = tmp_path / "bundle.json"
    bundle.write_text("invalid signature")
    original = assets.run

    def reject(*command):
        if command[:3] == ("gh", "attestation", "verify") and command[3] == str(
            assets.packages()[failed_package]
        ):
            raise subprocess.CalledProcessError(1, command)
        return original(*command)

    monkeypatch.setattr(assets, "run", reject)
    with pytest.raises(subprocess.CalledProcessError):
        assets.attach_attestation("v1.2.3", bundle)
    assert release["remote"] == {}


def test_published_release_cannot_receive_an_attestation(release, tmp_path):
    release["isDraft"] = False
    with pytest.raises(SystemExit, match="draft releases only"):
        assets.attach_attestation("v1.2.3", tmp_path / "bundle.json")
    assert len(release["commands"]) == 1


@pytest.mark.parametrize(
    ("ref", "skip_sync", "success"),
    [
        ("refs/tags/v1.2.3", "false", True),
        ("refs/heads/master", "false", False),
        ("refs/tags/v1.2.4", "false", False),
        ("refs/tags/v1.2.3", "true", False),
    ],
)
def test_preparation_guard_rejects_wrong_source_and_store_retry_inputs(
    tmp_path, ref, skip_sync, success
):
    workflow = (ROOT / ".github/workflows/extension-release.yml").read_text()
    step = workflow.split("      - name: Prepare a draft release for signed packages\n", 1)[1]
    script = textwrap.dedent(step.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0])
    python = tmp_path / "python3"
    python.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    python.chmod(0o755)
    result = subprocess.run(
        ["bash", "-c", script],
        env={
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "GITHUB_REF": ref,
            "RELEASE_TAG": "v1.2.3",
            "SKIP_ASSET_SYNC": skip_sync,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is success
    assert result.stdout.splitlines() == (
        ["scripts/sync_extension_release_assets.py", "--prepare-draft"] if success else []
    )
