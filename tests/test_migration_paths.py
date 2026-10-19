"""Migration helpers load when Alembic runs outside the repository."""

import subprocess
import sys
from pathlib import Path

from devfeed_core.version import SCHEMA_REVISION

ROOT = Path(__file__).resolve().parents[1]


def test_migration_history_loads_from_an_unrelated_working_directory(tmp_path):
    result = subprocess.run(
        [sys.executable, "-I", "-m", "alembic", "-c", str(ROOT / "alembic.ini"), "history"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "0027 -> 0028" in result.stdout
    assert "0028 -> 0029" in result.stdout
    assert f"-> {SCHEMA_REVISION} (head)" in result.stdout
