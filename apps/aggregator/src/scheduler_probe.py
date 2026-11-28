"""Probe the owned heartbeat without loading settings, Redis or the scheduler."""

import time
from pathlib import Path

from devfeed_core.runtime_files import runtime_marker

HEARTBEAT_TTL = 120


def healthy(marker: Path, *, now: float | None = None) -> bool:
    try:
        age = (time.time() if now is None else now) - marker.stat().st_mtime
    except OSError:
        return False
    return 0 <= age < HEARTBEAT_TTL


def main() -> None:
    try:
        ready = healthy(runtime_marker("scheduler-heartbeat"))
    except OSError:
        ready = False
    raise SystemExit(0 if ready else 1)


if __name__ == "__main__":
    main()
