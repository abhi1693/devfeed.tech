"""Dependency-free probe of a parent's last successful RQ heartbeat."""

import json
import os
import time
from pathlib import Path


def healthy(path: str) -> bool:
    try:
        record = json.loads(Path(path).read_text())
        os.kill(int(record["pid"]), 0)
        age = time.monotonic() - float(record["at"])
        return 0 <= age <= float(record["ttl"])
    except (OSError, ValueError, KeyError, TypeError):
        return False


if __name__ == "__main__":
    raise SystemExit(0 if healthy(os.environ["DEVFEED_WORKER_HEALTH_PATH"]) else 1)
