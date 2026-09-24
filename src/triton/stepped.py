"""Reading an uploaded workbook a few sheets at a time.

A host ends a request that runs too long or uses too much memory (PythonAnywhere then answers
"Bad Gateway"), and a whole project's workbook can take minutes to read there. So the page asks
for it to be read in steps of a few seconds: each step reads and cleans the next sheets and keeps
them beside the upload, then forgets them. A step that is cut off loses only the sheet it was on,
and is simply asked for again. The last request puts the kept sheets together.
"""

from __future__ import annotations

import gzip
import json
import pickle
import time
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path

from .importer import SheetData, clean_sheet
from .reader import Row, read_sheets, sheet_names, sheet_weights
from .validation import CHECKER_SHEET, ImportResult, collect

STATE = "read.json"


class RawFiles(Mapping[str, list[Row]]):
    """Sheets' rows as read, kept in files (gzipped pickles) rather than in memory."""

    def __init__(self, paths: dict[str, Path]) -> None:
        self._paths = paths

    def path(self, name: str) -> Path:
        return self._paths[name]

    def subset(self, names: Iterable[str]) -> RawFiles:
        keep = set(names)
        return RawFiles({k: v for k, v in self._paths.items() if k in keep})

    def __getitem__(self, name: str) -> list[Row]:
        # Only this app writes these pickles, from workbooks the user uploaded.
        with gzip.open(self._paths[name], "rb") as f:
            return pickle.load(f)

    def __iter__(self) -> Iterator[str]:
        return iter(self._paths)

    def __len__(self) -> int:
        return len(self._paths)


def _state(work: Path, path: Path) -> dict:
    file = work / STATE
    if file.exists():
        return json.loads(file.read_text("utf-8"))
    names = sheet_names(path)
    state = {"names": names, "weights": sheet_weights(path, names), "read": []}
    _save(work, state)
    return state


def _save(work: Path, state: dict) -> None:
    tmp = work / (STATE + ".tmp")
    tmp.write_text(json.dumps(state), "utf-8")
    tmp.replace(work / STATE)


def _dump(path: Path, value: object, zipped: bool = False) -> None:
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with gzip.open(tmp, "wb", compresslevel=3) if zipped else tmp.open("wb") as f:
        pickle.dump(value, f, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(path)


def read_step(work: Path, path: Path, budget_s: float) -> dict:
    """Read and clean the next sheets of the workbook at ``path`` for about ``budget_s`` seconds
    (at least one sheet), keeping them in ``work``. Says how far the reading has got."""
    started = time.monotonic()
    state = _state(work, path)
    todo = [n for n in state["names"] if n not in state["read"]]
    last = ""
    for name, rows in read_sheets(path, todo):
        i = state["names"].index(name)
        if name != CHECKER_SHEET:
            _dump(work / "raw" / f"{i:04d}.pkl.gz", rows, zipped=True)
            _dump(work / "sheets" / f"{i:04d}.pkl", clean_sheet(name, rows))
        del rows
        state["read"].append(name)
        _save(work, state)
        last = name
        if time.monotonic() - started >= budget_s:
            break
    done = len(state["read"]) == len(state["names"])
    share = sum(state["weights"].get(n, 0.0) for n in state["read"])
    return {
        "done": done,
        "fraction": 1.0 if done else round(min(share, 0.999), 4),
        "sheets": len(state["read"]),
        "of": len(state["names"]),
        "step": f"Read {last}" if last else "Read",
    }


def is_read(work: Path) -> bool:
    file = work / STATE
    if not file.exists():
        return False
    state = json.loads(file.read_text("utf-8"))
    return len(state["read"]) == len(state["names"])


def assemble(work: Path) -> ImportResult:
    """The workbook from the sheets the steps kept, checked as a whole."""
    state = json.loads((work / STATE).read_text("utf-8"))
    sheets: list[SheetData] = []
    raw: dict[str, Path] = {}
    for i, name in enumerate(state["names"]):
        if name == CHECKER_SHEET:
            continue
        with (work / "sheets" / f"{i:04d}.pkl").open("rb") as f:
            sheets.append(pickle.load(f))
        raw[name] = work / "raw" / f"{i:04d}.pkl.gz"
    return collect(sheets, RawFiles(raw))
