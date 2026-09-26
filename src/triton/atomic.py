"""Safe writes when two requests touch the same files at once.

PythonAnywhere runs more than one web worker, so two things can run at the same time (two uploads,
a design and a page loading its tabs, the same section open in two windows). A file is written to a
temporary file of its own and then moved into place, so a reader never sees half a file and two
writers never write into the same temporary file. Where a request reads a file, changes it and
writes it back (a section's results), :func:`locked` makes the others wait their turn.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: one worker when run locally, nothing to wait for
    fcntl = None  # type: ignore[assignment]


def tmp_for(path: Path) -> Path:
    """A temporary file next to ``path`` that no other writer uses; it ends in ``.tmp`` so
    housekeeping clears one left by a write that was cut off."""
    return path.with_name(f"{path.name}.{os.getpid()}-{uuid.uuid4().hex[:8]}.tmp")


def write_text(path: Path, text: str) -> None:
    tmp = tmp_for(path)
    try:
        tmp.write_text(text, "utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def write_bytes(path: Path, data: bytes) -> None:
    tmp = tmp_for(path)
    try:
        tmp.write_bytes(data)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


@contextmanager
def locked(path: Path) -> Iterator[None]:
    """Hold ``path`` (a lock file) while the block runs; another request holding it waits."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        if fcntl is not None:
            fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(f, fcntl.LOCK_UN)
