"""Web API: projects, material catalogue and workbook checks."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .materials import catalogue
from .project import DesignSettings, Project, ProjectInfo
from .reader import UnsupportedWorkbook
from .store import ProjectNotFound, ProjectStore
from .validation import import_workbook

STATIC = Path(__file__).parent / "static"
ALLOWED = {".xlsb", ".xlsx", ".xlsm"}

app = FastAPI(title="Triton")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def store() -> ProjectStore:
    # Created per request so TRITON_DATA_DIR can change between tests.
    return ProjectStore()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


# --- Reference data --------------------------------------------------------------


@app.get("/api/materials")
def materials() -> dict:
    return catalogue()


@app.get("/api/schema/project")
def project_schema() -> dict:
    return Project.model_json_schema()


# --- Projects ----------------------------------------------------------------------


class ProjectSummary(BaseModel):
    id: str
    name: str
    number: str
    section: str
    elements: int
    updated_at: str


class NewProject(BaseModel):
    info: ProjectInfo = ProjectInfo()
    design: DesignSettings | None = None
    element_names: list[str] = []


class ElementNames(BaseModel):
    names: list[str]


def _get(project_id: str) -> Project:
    try:
        return store().get(project_id)
    except ProjectNotFound:
        raise HTTPException(404, "Project not found.") from None


@app.get("/api/projects")
def list_projects() -> list[ProjectSummary]:
    return [
        ProjectSummary(
            id=p.id,
            name=p.info.name,
            number=p.info.number,
            section=p.info.section,
            elements=len(p.elements),
            updated_at=p.updated_at,
        )
        for p in store().list()
    ]


@app.post("/api/projects", status_code=201)
def create_project(body: NewProject) -> Project:
    project = Project(info=body.info, design=body.design or DesignSettings())
    project.add_elements(body.element_names)
    return store().save(project)


@app.get("/api/projects/{project_id}")
def get_project(project_id: str) -> Project:
    return _get(project_id)


@app.put("/api/projects/{project_id}")
def update_project(project_id: str, body: Project) -> Project:
    existing = _get(project_id)
    if body.id != project_id:
        raise HTTPException(400, "Project id in the body does not match the URL.")
    body.created_at = existing.created_at
    return store().save(body)


@app.delete("/api/projects/{project_id}", status_code=204)
def delete_project(project_id: str) -> None:
    try:
        store().delete(project_id)
    except ProjectNotFound:
        raise HTTPException(404, "Project not found.") from None


@app.post("/api/projects/{project_id}/elements")
def add_elements(project_id: str, body: ElementNames) -> dict:
    project = _get(project_id)
    added = project.add_elements(body.names)
    store().save(project)
    return {"added": added, "project": project}


# --- Workbooks ------------------------------------------------------------------------


@app.post("/api/workbooks/check")
def check_workbook(file: UploadFile) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED:
        raise HTTPException(400, f"Upload a .xlsb, .xlsx or .xlsm file (got '{suffix or 'no extension'}').")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"upload{suffix}"
        with path.open("wb") as out:
            shutil.copyfileobj(file.file, out, length=1024 * 1024)
        try:
            result = import_workbook(path)
        except UnsupportedWorkbook as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:  # corrupt or password-protected files
            raise HTTPException(400, f"Could not read the workbook: {e}") from e
    return {"file": file.filename, **result.summary()}
