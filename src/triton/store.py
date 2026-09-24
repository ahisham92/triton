"""Projects are saved as one JSON file each; each section keeps its workbook and results in a folder."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import pickle
import shutil
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import clock
from .project import Project, _now
from .validation import ImportResult


class ProjectNotFound(KeyError):
    pass


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
        self, project_id: str, section_id: str, filename: str, result: ImportResult, replace: bool = True
    ) -> dict[str, Any]:
        d = self._dir(project_id, section_id)
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
        # Only this app writes these pickles, from workbooks the user uploaded.
        with (d / "workbook.pkl.tmp").open("wb") as f:
            pickle.dump(result, f, protocol=pickle.HIGHEST_PROTOCOL)
        (d / "workbook.pkl.tmp").replace(d / "workbook.pkl")
        summary = {"file": filename, "uploaded_at": _now(), "version": uuid.uuid4().hex, **result.summary()}
        self._write_json(d / "workbook.json", summary)
        # The results stay, flagged as out of date (the workbook is part of their fingerprint).
        return summary

    def delete_workbook(self, project_id: str, section_id: str) -> None:
        """The section's workbook and the rows kept with it; its results stay (out of date)."""
        d = self._dir(project_id, section_id)
        for name in ("workbook.pkl", "workbook.json", "workbook_view.json"):
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
        path = self._dir(project_id, section_id) / "workbook.pkl"
        if not path.exists():
            return None
        with path.open("rb") as f:
            return pickle.load(f)

    def save_results(self, project_id: str, section_id: str, results: dict[str, Any]) -> None:
        self._write_json(self._dir(project_id, section_id) / "results.json", results)

    def load_results(self, project_id: str, section_id: str) -> dict[str, Any] | None:
        path = self._dir(project_id, section_id) / "results.json"
        return json.loads(path.read_text("utf-8")) if path.exists() else None

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, default=str), "utf-8")
        tmp.replace(path)
