from pathlib import Path
from unittest.mock import Mock

import pytest
from devfeed_core import runtime_files


@pytest.mark.parametrize("existing", [False, True])
def test_marker_is_private_and_replaced_completely(tmp_path, existing):
    path = tmp_path / "heartbeat"
    if existing:
        path.write_text("old heartbeat")
        path.chmod(0o666)
    runtime_files.write_runtime_marker(path, "new heartbeat")
    assert path.read_text() == "new heartbeat"
    assert path.stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("link", ["symbolic", "hard"])
def test_marker_does_not_modify_a_preexisting_link_target(tmp_path, link):
    target = tmp_path / "protected"
    target.write_text("keep this content")
    path = tmp_path / "heartbeat"
    if link == "symbolic":
        path.symlink_to(target)
    else:
        path.hardlink_to(target)
    runtime_files.write_runtime_marker(path, "1234.5")
    assert target.read_text() == "keep this content"
    assert not path.is_symlink()
    assert path.read_text() == "1234.5"
    assert path.stat().st_ino != target.stat().st_ino
    assert set(tmp_path.iterdir()) == {target, path}


def test_reader_sees_old_complete_marker_until_replacement(tmp_path, monkeypatch):
    path = tmp_path / "heartbeat"
    path.write_text("old heartbeat")
    replace = Path.replace

    def inspect_then_replace(staged, destination):
        assert destination.read_text() == "old heartbeat"
        assert staged.read_text() == "new heartbeat"
        return replace(staged, destination)

    monkeypatch.setattr(Path, "replace", inspect_then_replace)
    runtime_files.write_runtime_marker(path, "new heartbeat")
    assert path.read_text() == "new heartbeat"


def test_failed_replace_preserves_marker_and_cleans_temporary_file(tmp_path, monkeypatch):
    path = tmp_path / "heartbeat"
    path.write_text("old heartbeat")
    monkeypatch.setattr(Path, "replace", Mock(side_effect=PermissionError("denied")))
    with pytest.raises(PermissionError, match="denied"):
        runtime_files.write_runtime_marker(path, "new heartbeat")
    assert path.read_text() == "old heartbeat"
    assert list(tmp_path.iterdir()) == [path]


def test_missing_parent_does_not_create_a_marker(tmp_path):
    with pytest.raises(FileNotFoundError):
        runtime_files.write_runtime_marker(tmp_path / "missing" / "heartbeat", "1234.5")
    assert not list(tmp_path.iterdir())


def test_failed_write_preserves_marker_and_cleans_temporary_file(tmp_path):
    path = tmp_path / "heartbeat"
    path.write_text("old heartbeat")
    with pytest.raises(UnicodeEncodeError):
        runtime_files.write_runtime_marker(path, "\ud800")
    assert path.read_text() == "old heartbeat"
    assert list(tmp_path.iterdir()) == [path]
