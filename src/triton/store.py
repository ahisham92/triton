"""Projects are saved as one JSON file each; each section keeps its workbook and results in a folder."""

from __future__ import annotations

import json
import os
import pickle
import shutil
import uuid
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
        self, project_id: str, section_id: str, filename: str, result: ImportResult
    ) -> dict[str, Any]:
        d = self._dir(project_id, section_id)
        # Only this app writes these pickles, from workbooks the user uploaded.
        with (d / "workbook.pkl.tmp").open("wb") as f:
            pickle.dump(result, f, protocol=pickle.HIGHEST_PROTOCOL)
        (d / "workbook.pkl.tmp").replace(d / "workbook.pkl")
        summary = {"file": filename, "uploaded_at": _now(), "version": uuid.uuid4().hex, **result.summary()}
        self._write_json(d / "workbook.json", summary)
        # The results stay, flagged as out of date (the workbook is part of their fingerprint).
        return summary

    def workbook_summary(self, project_id: str, section_id: str) -> dict[str, Any] | None:
        path = self._dir(project_id, section_id) / "workbook.json"
        return json.loads(path.read_text("utf-8")) if path.exists() else None

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
