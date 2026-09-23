"""Projects are saved as one JSON file each in the data folder."""

from __future__ import annotations

import os
from pathlib import Path

from .project import Project, _now


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
        return sorted(projects, key=lambda p: p.updated_at, reverse=True)

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
