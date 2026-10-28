"""Exercise the native shell probe without importing a production Python fallback."""

import os
import subprocess

from tests.support.paths import REPO_ROOT


def healthy(path):
    return (
        subprocess.run(
            ["/bin/sh", str(REPO_ROOT / "packages/core/src/devfeed-worker-health")],
            env={**os.environ, "DEVFEED_WORKER_HEALTH_PATH": str(path)},
            capture_output=True,
            timeout=5,
        ).returncode
        == 0
    )
