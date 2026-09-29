"""Tab data kept once worked out, so any web worker can hand it out again at once.

The 3D view (geometry, site, deformed shape) and the Clashes tab each read the stored workbook or
work through every pile head, which takes seconds to minutes on a real project. What they give
depends only on the section's inputs, the workbook, the design results and this code, so it is kept
in the section's ``views`` folder under a key made of those. When any of them changes the key does
too, and the next request works it out again. The page asks for these right after a design, so the
tabs open straight away.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import pickle
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import atomic

FOLDER = "views"


def stat(path: Path) -> str:
    """A file's size and last change, or "-" when it is not there: changes whenever it is written."""
    try:
        s = path.stat()
    except OSError:
        return "-"
    return f"{s.st_mtime_ns}-{s.st_size}"


def key(*parts: Any) -> str:
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _file(d: Path, name: str, ext: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:80]
    return d / FOLDER / f"{safe}.{ext}"


def get(d: Path, name: str, k: str) -> Any | None:
    """What was kept under ``name`` when it was kept with key ``k``; else None."""
    try:
        with gzip.open(_file(d, name, "json.gz"), "rt", encoding="utf-8") as f:
            kept = json.load(f)
    except (OSError, ValueError, EOFError):
        return None
    return kept.get("data") if isinstance(kept, dict) and kept.get("key") == k else None


def put(d: Path, name: str, k: str, data: Any) -> None:
    path = _file(d, name, "json.gz")
    path.parent.mkdir(exist_ok=True)
    tmp = atomic.tmp_for(path)
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=3) as f:
        json.dump({"key": k, "data": data}, f, default=str)
    tmp.replace(path)


def kept(d: Path, name: str, k: str, work: Callable[[], Any]) -> Any:
    """The data kept under ``name`` for key ``k``, worked out (and kept) when there is none."""
    data = get(d, name, k)
    if data is None:
        data = work()
        put(d, name, k, data)
    return data


def get_object(d: Path, name: str, k: str) -> Any | None:
    """A Python object kept with :func:`put_object` under the same key; else None."""
    try:
        with gzip.open(_file(d, name, "pkl.gz"), "rb") as f:
            got, obj = pickle.load(f)  # only this app writes these files
    except Exception:  # noqa: BLE001 - a file from older code or cut off: work it out again
        return None
    return obj if got == k else None


def put_object(d: Path, name: str, k: str, obj: Any) -> None:
    path = _file(d, name, "pkl.gz")
    path.parent.mkdir(exist_ok=True)
    tmp = atomic.tmp_for(path)
    with gzip.open(tmp, "wb", compresslevel=3) as f:
        pickle.dump((k, obj), f, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(path)


def drop(d: Path, prefix: str, keep: str | None = None) -> None:
    """Remove the kept files whose name starts with ``prefix`` (all but ``keep``)."""
    folder = d / FOLDER
    if not folder.is_dir():
        return
    for f in folder.iterdir():
        stem = f.name.split(".", 1)[0]
        if stem.startswith(prefix) and stem != keep:
            f.unlink(missing_ok=True)
