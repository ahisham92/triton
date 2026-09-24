"""A whole project as one file (.trt) to send to someone, and opening one as a project on the server.

A .trt is a zip of plain data, nothing that runs: the project (settings, sections, elements, sheet
mapping, choices), and per section the workbook's sheets as their rows were read (or, for a workbook
uploaded before the rows were kept, its cleaned tables), the workbook's summary, the design results
and the Comparisons / Value engineering trials. Opening one checks the workbook again from those
rows (the same checks as an upload), so a file from someone else can only bring data. Cached views
and anything else in a section's folder stay behind.
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime as dt
import gzip
import json
import re
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import IO, Any

import pandas as pd

from .importer import SheetData, clean_sheet, parse_sheet_name
from .issues import Issue, Severity
from .project import Project, _now
from .store import ProjectStore
from .validation import _SHEET_CHECKS as SHEET_CHECKS
from .validation import CHECKER_SHEET, collect

FORMAT = "triton.project/1"
SUFFIX = ".trt"
# Section files carried besides the workbook: results and trials, all JSON.
_SECTION_JSON = re.compile(r"[A-Za-z0-9_-]+\.json")
_TRIAL = re.compile(r"trials/[0-9a-f]{6,64}\.json\.gz")
_SKIP = {"workbook.json", "workbook_view.json"}
# An opened file may not grow past this when unpacked (a zip bomb stops here).
MAX_UNPACKED = 4 * 1024**3


class BadPackage(ValueError):
    pass


def _encode(v: Any) -> Any:
    if isinstance(v, dt.datetime):
        return {"$dt": v.isoformat()}
    if isinstance(v, dt.date):
        return {"$d": v.isoformat()}
    if isinstance(v, dt.time):
        return {"$t": v.isoformat()}
    if isinstance(v, dt.timedelta):
        return {"$td": v.total_seconds()}
    return str(v)


def _decode(o: dict) -> Any:
    if len(o) == 1:
        ((k, v),) = o.items()
        if k == "$dt":
            return dt.datetime.fromisoformat(v)
        if k == "$d":
            return dt.date.fromisoformat(v)
        if k == "$t":
            return dt.time.fromisoformat(v)
        if k == "$td":
            return dt.timedelta(seconds=v)
    return o


def _frame_out(f: pd.DataFrame | None) -> dict[str, Any] | None:
    if f is None:
        return None
    return {
        "columns": [str(c) for c in f.columns],
        "dtypes": [str(t) for t in f.dtypes],
        "index": f.index.tolist(),
        "values": [f.iloc[:, i].tolist() for i in range(f.shape[1])],
    }


def _frame_in(d: dict[str, Any] | None) -> pd.DataFrame | None:
    if d is None:
        return None
    f = pd.DataFrame({i: pd.Series(v, dtype=object) for i, v in enumerate(d["values"])})
    if d["index"]:
        f.index = d["index"]
    f.columns = d["columns"]
    for i, t in enumerate(d["dtypes"]):
        if t != "object":
            with contextlib.suppress(TypeError, ValueError):
                f[f.columns[i]] = f.iloc[:, i].astype(t)
    return f


def _sheet_out(s: SheetData) -> dict[str, Any]:
    """A cleaned sheet as plain data (for workbooks uploaded before their rows were kept)."""
    return {
        "frame": _frame_out(s.frame),
        "duplicates": _frame_out(s.duplicates),
        "units": s.units,
        "issues": [{**dataclasses.asdict(i), "severity": i.severity.value} for i in s.issues],
        "counts": [s.raw_rows, s.blank_rows, s.duplicate_rows, s.empty],
    }


def _sheet_in(name: str, d: dict[str, Any]) -> SheetData:
    issues = []
    for i in d["issues"]:
        if i["code"] in SHEET_CHECKS:  # checked again with the whole workbook
            continue
        notes = {int(k): str(v) for k, v in i["notes"].items()} if i.get("notes") else None
        issues.append(Issue(**{**i, "severity": Severity(i["severity"]), "notes": notes}))
    raw_rows, blank_rows, duplicate_rows, empty = d["counts"]
    return SheetData(
        name,
        parse_sheet_name(name),
        _frame_in(d["frame"]),
        units={str(k): str(v) for k, v in d["units"].items()},
        issues=issues,
        raw_rows=int(raw_rows),
        blank_rows=int(blank_rows),
        duplicate_rows=int(duplicate_rows),
        empty=bool(empty),
        duplicates=_frame_in(d["duplicates"]),
    )


def file_name(project: Project) -> str:
    return (re.sub(r"[^A-Za-z0-9._ -]+", "_", project.info.name or "").strip(" ._") or "project") + SUFFIX


def write(store: ProjectStore, project: Project, out: IO[bytes]) -> dict[str, Any]:
    """Write ``project`` with every section's workbook rows, results and trials to ``out`` as a .trt."""
    manifest: dict[str, Any] = {
        "format": FORMAT,
        "saved_at": _now(),
        "name": project.info.name,
        "sections": {},
    }
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr("project.json", project.model_dump_json(indent=1))
        for section in project.sections:
            d = store.root / project.id / section.id
            if not d.is_dir():
                continue
            base = f"sections/{section.id}/"
            info: dict[str, Any] = {"name": section.name}
            summary = store.workbook_summary(project.id, section.id)
            wb = store.load_workbook(project.id, section.id) if summary else None
            if wb is not None:
                # One member per sheet, so neither end holds the whole workbook's rows at once.
                order = []
                for sheet in wb.sheets:
                    rows = store.load_raw(project.id, section.id, sheet.name)
                    # Rows as read; a workbook uploaded before they were kept carries its cleaned sheets.
                    body = {"rows": rows} if rows is not None else {"cleaned": _sheet_out(sheet)}
                    with z.open(f"{base}sheets/{len(order)}.json", "w", force_zip64=True) as f:
                        f.write(json.dumps(body, default=_encode).encode())
                    order.append(sheet.name)
                info["sheets"] = order
                z.writestr(base + "workbook.json", json.dumps(summary, default=str))
            for p in sorted(d.iterdir()):
                if p.is_file() and _SECTION_JSON.fullmatch(p.name) and p.name not in _SKIP:
                    z.write(p, base + p.name)
            if (d / "trials").is_dir():
                for p in sorted((d / "trials").glob("*.json.gz")):
                    if _TRIAL.fullmatch("trials/" + p.name):
                        z.write(p, base + "trials/" + p.name, compress_type=zipfile.ZIP_STORED)
            manifest["sections"][section.id] = info
        z.writestr("triton.json", json.dumps(manifest, indent=1))
    return manifest


def peek(path: Path) -> dict[str, Any]:
    """The project name and sections of a .trt, without opening it."""
    z = _zip(path)
    with z:
        project = _project(z)
    return {"name": project.info.name, "sections": [s.name for s in project.sections]}


def _zip(path: Path) -> zipfile.ZipFile:
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        raise BadPackage("This is not a Triton project file (.trt).") from e
    names = set(z.namelist())
    if "triton.json" not in names or "project.json" not in names:
        z.close()
        raise BadPackage("This is not a Triton project file (.trt).")
    if sum(i.file_size for i in z.infolist()) > MAX_UNPACKED:
        z.close()
        raise BadPackage("This project file unpacks to more than 4 GB, so it was not opened.")
    try:
        fmt = json.loads(z.read("triton.json")).get("format")
    except ValueError as e:
        z.close()
        raise BadPackage("This project file is damaged (triton.json).") from e
    if fmt != FORMAT:
        z.close()
        raise BadPackage(
            f"This project file is in a format this Triton does not know ({fmt}): update Triton."
        )
    return z


def _project(z: zipfile.ZipFile) -> Project:
    try:
        project = Project.model_validate_json(z.read("project.json"))
    except ValueError as e:
        raise BadPackage(f"This project file is damaged (project.json): {e}") from e
    ids = [s.id for s in project.sections]
    if len(set(ids)) != len(ids) or not all(i.isalnum() for i in ids):
        raise BadPackage("This project file is damaged (section ids).")
    return project


def open_package(
    store: ProjectStore, path: Path, name: str | None = None, progress: Any = None
) -> tuple[Project, list[str]]:
    """Make a new project on the server from the .trt at ``path`` (named ``name`` if given).

    Returns the project and notes on what could not come with it. Nothing is kept if the file fails.
    """
    tell = progress or (lambda *_: None)
    notes: list[str] = []
    with _zip(path) as z:
        project = _project(z)
        manifest = json.loads(z.read("triton.json"))
        project.id = uuid.uuid4().hex[:12]
        if name:
            project.info.name = name
        names = set(z.namelist())
        store.save(project)
        try:
            for n, section in enumerate(project.sections):
                base = f"sections/{section.id}/"
                tell(0.1 + 0.8 * n / len(project.sections), f"Opening {section.name}")
                d = store._dir(project.id, section.id)
                order = (manifest.get("sections", {}).get(section.id) or {}).get("sheets") or []
                if order:
                    summary = (
                        json.loads(z.read(base + "workbook.json")) if base + "workbook.json" in names else {}
                    )
                    sheets, kept_rows = [], {}
                    for i, sheet in enumerate(order):
                        member = f"{base}sheets/{i}.json"
                        if not isinstance(sheet, str) or member not in names:
                            raise BadPackage(f"The workbook of {section.name} is damaged.")
                        tell(0.1 + 0.8 * (n + i / len(order)) / len(project.sections), f"Checking {sheet}")
                        with z.open(member) as f:
                            body = json.load(f, object_hook=_decode)
                        try:
                            if "rows" in body:
                                if not all(isinstance(r, list) for r in body["rows"]):
                                    raise TypeError("rows")
                                if sheet == CHECKER_SHEET:
                                    continue
                                sheets.append(clean_sheet(sheet, body["rows"]))
                                kept_rows[sheet] = member
                            else:
                                sheets.append(_sheet_in(sheet, body["cleaned"]))
                        except (KeyError, TypeError, ValueError) as e:
                            raise BadPackage(f"The sheet {sheet} of {section.name} is damaged.") from e
                    store.save_workbook(
                        project.id, section.id, summary.get("file") or "workbook", collect(sheets)
                    )
                    # The rows as read, for the Workbook tab's sheet view and editing.
                    for sheet, member in kept_rows.items():
                        with z.open(member) as f:
                            store._save_raw(d, sheet, json.load(f, object_hook=_decode)["rows"])
                    if summary:
                        # The same workbook version, sources and upload times as where it was saved,
                        # so its results and trials stay up to date.
                        kept = store.workbook_summary(project.id, section.id) or {}
                        for k in ("file", "uploaded_at", "version", "sources"):
                            if k in summary:
                                kept[k] = summary[k]
                            else:  # an older summary without it: so is the fingerprint its results have
                                kept.pop(k, None)
                        store._write_json(d / "workbook.json", kept)
                for member in sorted(names):
                    if not member.startswith(base):
                        continue
                    rel = member[len(base) :]
                    if _SECTION_JSON.fullmatch(rel) and rel not in _SKIP:
                        data = json.loads(z.read(member))
                        store._write_json(d / rel, data)
                    elif _TRIAL.fullmatch(rel):
                        data = json.loads(gzip.decompress(z.read(member)))
                        (d / "trials").mkdir(exist_ok=True)
                        (d / rel).write_bytes(gzip.compress(json.dumps(data).encode(), 5))
        except BaseException:
            store.delete(project.id)
            raise
    tell(0.95, "Saving")
    return store.save(project), notes


def unique_name(store: ProjectStore, name: str) -> str:
    """``name``, or ``name (2)``, ``name (3)``… if a project already has it."""
    taken = {p.info.name for p in store.list()}
    if name not in taken:
        return name
    stem = re.sub(r" \(\d+\)$", "", name)
    n = 2
    while f"{stem} ({n})" in taken:
        n += 1
    return f"{stem} ({n})"


def spool(store: ProjectStore, project: Project) -> tuple[IO[bytes], int]:
    """The .trt in a temporary file (on disk past 32 MB), rewound, and its size."""
    f = tempfile.SpooledTemporaryFile(max_size=32 * 1024**2)
    write(store, project, f)
    size = f.tell()
    f.seek(0)
    return f, size
