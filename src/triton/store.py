"""Projects are saved as one JSON file each; each section keeps its workbook and results in a folder.

Space is kept lean: the workbook is stored compressed, results are deleted when the model is
unlocked, and ``housekeeping`` removes what nothing needs any more. It deletes only what its
allow-list names (abandoned uploads, old progress notes, half-written ``.tmp`` files); anything
else in the data folder, such as templates or reference files, is never touched.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import pickle
import re
import shutil
import time
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import clock
from .project import Project, _now
from .validation import ImportResult


class ProjectNotFound(KeyError):
    pass


UPLOAD_ID = re.compile(r"[0-9a-f]{32}")
UPLOAD_MAX_AGE = 6 * 3600  # an upload nobody has added to for this long is abandoned
PROGRESS_MAX_AGE = 24 * 3600
TMP_MAX_AGE = 3600
HOUSEKEEPING_EVERY = 600


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    if path.is_dir():
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return 0


def _last_change(path: Path) -> float:
    times = [path.stat().st_mtime] + [f.stat().st_mtime for f in path.rglob("*")] if path.is_dir() else []
    return max(times or [path.stat().st_mtime])


def housekeeping(data: str | Path | None = None, force: bool = False) -> dict[str, int]:
    """Delete what nothing needs any more, at most every few minutes (``force`` runs it now).

    Allow-list, and nothing else: ``uploads/<32 hex>`` folders untouched for ``UPLOAD_MAX_AGE``,
    ``progress/*.json`` older than a day, and ``*.tmp`` files older than an hour in the projects
    folder (left by a write that was cut off). Returns what it removed."""
    root = Path(data or os.environ.get("TRITON_DATA_DIR", "data"))
    mark = root / ".housekeeping"
    now = time.time()
    if not force and mark.exists() and now - mark.stat().st_mtime < HOUSEKEEPING_EVERY:
        return {"files": 0, "bytes": 0}
    if root.is_dir():
        mark.touch()
    gone = {"files": 0, "bytes": 0}

    def drop(path: Path) -> None:
        gone["bytes"] += _size(path)
        gone["files"] += 1
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)

    uploads = root / "uploads"
    if uploads.is_dir():
        for d in uploads.iterdir():
            if d.is_dir() and UPLOAD_ID.fullmatch(d.name) and now - _last_change(d) > UPLOAD_MAX_AGE:
                drop(d)
    progress = root / "progress"
    if progress.is_dir():
        for f in progress.glob("*.json"):
            if now - f.stat().st_mtime > PROGRESS_MAX_AGE:
                drop(f)
    projects = root / "projects"
    for pattern in ("*.tmp", "*/*/*.tmp", "*/*/raw/*.tmp"):
        for f in projects.glob(pattern):
            if f.is_file() and now - f.stat().st_mtime > TMP_MAX_AGE:
                drop(f)
    return gone


class ProjectStore:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or os.environ.get("TRITON_DATA_DIR", "data")) / "projects"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, project_id: str) -> Path:
        if not project_id.isalnum():
            raise ProjectNotFound(project_id)
        return self.root / f"{project_id}.json"

    def list(self) -> list[Project]:
        projects = [Project.model_validate_json(p.read_text("utf-8")) for p in self.root.glob("*.json")]
        return sorted(projects, key=lambda p: clock.order(p.updated_at), reverse=True)

    def get(self, project_id: str) -> Project:
        path = self._path(project_id)
        if not path.exists():
            raise ProjectNotFound(project_id)
        return Project.model_validate_json(path.read_text("utf-8"))

    def save(self, project: Project) -> Project:
        project.updated_at = _now()
        path = self._path(project.id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(project.model_dump_json(indent=2), "utf-8")
        tmp.replace(path)
        return project

    def duplicate(self, project_id: str, name: str) -> Project:
        """A new project with ``project_id``'s settings, sections, workbooks, results and trials."""
        project = self.get(project_id).model_copy(deep=True)
        project.id = uuid.uuid4().hex[:12]
        project.info.name = name
        project.created_at = _now()
        src, dst = self.root / project_id, self.root / project.id
        try:
            if src.is_dir():
                shutil.copytree(src, dst, ignore=shutil.ignore_patterns("*.tmp", "revisions"))
            return self.save(project)
        except BaseException:
            shutil.rmtree(dst, ignore_errors=True)
            raise

    def delete(self, project_id: str) -> None:
        path = self._path(project_id)
        if not path.exists():
            raise ProjectNotFound(project_id)
        path.unlink()
        shutil.rmtree(self.root / project_id, ignore_errors=True)

    # --- Files kept for each section: the checked workbook and design results.

    def _dir(self, project_id: str, section_id: str) -> Path:
        self._path(project_id)  # validates the id
        if not section_id.isalnum():
            raise ProjectNotFound(section_id)
        d = self.root / project_id / section_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def delete_section_files(self, project_id: str, section_id: str) -> None:
        shutil.rmtree(self._dir(project_id, section_id), ignore_errors=True)

    def save_workbook(
        self,
        project_id: str,
        section_id: str,
        filename: str,
        result: ImportResult,
        replace: bool = True,
        new_sheets: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        """Keep a section's workbook. Each sheet remembers the upload it came from (``sources``):
        with ``replace`` all of them come from ``filename``; otherwise only ``new_sheets`` do and the
        rest keep where they came from."""
        d = self._dir(project_id, section_id)
        earlier = self.workbook_summary(project_id, section_id) if not replace else None
        raw, result.raw = result.raw, None
        if replace:
            shutil.rmtree(d / "raw", ignore_errors=True)
        kept = getattr(raw, "path", None)  # rows already in files (read in steps): copy the files
        for name in raw or {}:
            if kept:
                target = self._raw_path(d, name)
                target.parent.mkdir(exist_ok=True)
                shutil.copyfile(kept(name), target)
            else:
                self._save_raw(d, name, raw[name])
        # Only this app writes these pickles, from workbooks the user uploaded. Compressed: about half.
        with gzip.open(d / "workbook.pkl.gz.tmp", "wb", compresslevel=1) as f:
            pickle.dump(result, f, protocol=pickle.HIGHEST_PROTOCOL)
        (d / "workbook.pkl.gz.tmp").replace(d / "workbook.pkl.gz")
        (d / "workbook.pkl").unlink(missing_ok=True)  # the uncompressed copy of an earlier upload
        now = _now()
        names = [sh.name for sh in result.sheets]
        fresh = set(names) if replace or earlier is None else set(new_sheets or [])
        before = (earlier or {}).get("sources") or {
            n: {
                "file": (earlier or {}).get("file") or filename,
                "at": (earlier or {}).get("uploaded_at") or now,
            }
            for n in names
        }
        upload = filename.rsplit(" + ", 1)[-1]
        sources = {
            n: {"file": upload, "at": now} if n in fresh or n not in before else before[n] for n in names
        }
        summary = {
            "file": filename,
            "uploaded_at": now,
            "version": uuid.uuid4().hex,
            **result.summary(),
            "sources": sources,
        }
        self._write_json(d / "workbook.json", summary)
        # Every result depends on the workbook: none of them is kept once it changes.
        (d / "results.json").unlink(missing_ok=True)
        return summary

    def delete_workbook(self, project_id: str, section_id: str) -> None:
        """The section's workbook, the rows kept with it and the results designed from it."""
        d = self._dir(project_id, section_id)
        for name in (
            "workbook.pkl",
            "workbook.pkl.gz",
            "workbook.json",
            "workbook_view.json",
            "results.json",
        ):
            (d / name).unlink(missing_ok=True)
        shutil.rmtree(d / "raw", ignore_errors=True)

    def drop_raw(self, project_id: str, section_id: str, sheets: Iterable[str]) -> None:
        d = self._dir(project_id, section_id)
        for sheet in sheets:
            self._raw_path(d, sheet).unlink(missing_ok=True)

    def load_view(self, project_id: str, section_id: str, key: str) -> dict[str, Any] | None:
        """The workbook as the section last read it, if nothing it depends on has changed since."""
        path = self._dir(project_id, section_id) / "workbook_view.json"
        if not path.exists():
            return None
        try:
            kept = json.loads(path.read_text("utf-8"))
        except ValueError:
            return None
        return kept["data"] if kept.get("key") == key else None

    def save_view(self, project_id: str, section_id: str, key: str, data: dict[str, Any]) -> None:
        self._write_json(self._dir(project_id, section_id) / "workbook_view.json", {"key": key, "data": data})

    def workbook_summary(self, project_id: str, section_id: str) -> dict[str, Any] | None:
        path = self._dir(project_id, section_id) / "workbook.json"
        return json.loads(path.read_text("utf-8")) if path.exists() else None

    @staticmethod
    def _raw_path(d: Path, sheet: str) -> Path:
        return d / "raw" / (hashlib.sha1(sheet.encode()).hexdigest()[:16] + ".pkl.gz")

    def _save_raw(self, d: Path, sheet: str, rows: list) -> None:
        path = self._raw_path(d, sheet)
        path.parent.mkdir(exist_ok=True)
        with gzip.open(path.with_suffix(".tmp"), "wb", compresslevel=3) as f:
            pickle.dump(rows, f, protocol=pickle.HIGHEST_PROTOCOL)
        path.with_suffix(".tmp").replace(path)

    def load_raw(self, project_id: str, section_id: str, sheet: str) -> list | None:
        """A sheet's rows as they were read, or None for workbooks uploaded before they were kept."""
        path = self._raw_path(self._dir(project_id, section_id), sheet)
        if not path.exists():
            return None
        with gzip.open(path, "rb") as f:
            return pickle.load(f)

    def load_workbook(self, project_id: str, section_id: str) -> ImportResult | None:
        d = self._dir(project_id, section_id)
        if (d / "workbook.pkl.gz").exists():
            with gzip.open(d / "workbook.pkl.gz", "rb") as f:
                return pickle.load(f)
        if not (d / "workbook.pkl").exists():
            return None
        with (d / "workbook.pkl").open("rb") as f:
            return pickle.load(f)

    def save_results(self, project_id: str, section_id: str, results: dict[str, Any]) -> None:
        self._write_json(self._dir(project_id, section_id) / "results.json", results)

    def delete_results(self, project_id: str, section_id: str) -> int:
        """Delete a section's design results; returns the bytes freed. Its inputs, workbook and
        sheet mapping stay."""
        path = self._dir(project_id, section_id) / "results.json"
        size = _size(path)
        path.unlink(missing_ok=True)
        return size

    def usage(self, project_id: str, section_ids: Iterable[str]) -> dict[str, dict[str, int]]:
        """Bytes kept per section: the workbook (and its cached view), its rows kept for editing,
        and the design results."""
        out = {}
        for sid in section_ids:
            d = self.root / project_id / sid
            workbook = sum(
                _size(d / n)
                for n in ("workbook.pkl", "workbook.pkl.gz", "workbook.json", "workbook_view.json")
            )
            rows, results = _size(d / "raw"), _size(d / "results.json")
            out[sid] = {"workbook": workbook, "rows": rows, "results": results, "total": _size(d)}
        return out

    def load_results(self, project_id: str, section_id: str) -> dict[str, Any] | None:
        path = self._dir(project_id, section_id) / "results.json"
        return json.loads(path.read_text("utf-8")) if path.exists() else None

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, default=str), "utf-8")
        tmp.replace(path)
