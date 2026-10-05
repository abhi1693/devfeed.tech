"""Publish process health markers without following pre-existing links."""

import os
import stat
from pathlib import Path
from tempfile import NamedTemporaryFile, gettempdir
from typing import Literal


def runtime_marker(name: Literal["worker-name", "search-heartbeat"]) -> Path:
    directory = Path(gettempdir()) / f"devfeed-runtime-{os.geteuid()}"
    directory.mkdir(mode=0o700, exist_ok=True)
    details = directory.lstat()
    if (
        not stat.S_ISDIR(details.st_mode)
        or details.st_uid != os.geteuid()
        or stat.S_IMODE(details.st_mode) & 0o077
    ):
        raise PermissionError("Runtime markers require an owned private directory")
    return directory / name


def write_runtime_marker(path: Path, value: str) -> None:
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(value)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
