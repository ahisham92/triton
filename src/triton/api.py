"""Web API: projects, material catalogue and workbook checks."""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from . import adsec, durability
from .design.export import pile_cages
from .design.governing import workbook as governing_workbook
from .design.runner import factored_elements, run_section
from .design.spw import workbook as spw_workbook
from .elements import ElementType
from .geometry import section_geometry
from .materials import catalogue
from .project import (
    CombiWallInput,
    DesignSettings,
    PileInput,
    Project,
    ProjectInfo,
    Section,
    with_project_grades,
)
from .reader import UnsupportedWorkbook
from .report import RENDERERS, build_report
from .store import ProjectNotFound, ProjectStore
from .validation import ImportResult, import_workbook

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


@app.get("/api/durability-defaults")
def durability_defaults(cover_code: str = "en1992", corrosion_code: str = "en1993_5", life: int = 50) -> dict:
    """Covers and corrosion allowances for the chosen codes and design life."""
    if life < 1:
        raise HTTPException(422, "Design life must be at least 1 year.")
    return durability.defaults(cover_code, corrosion_code, life)


@app.get("/api/schema/project")
def project_schema() -> dict:
    return Project.model_json_schema()


# --- Projects ----------------------------------------------------------------------


class ProjectSummary(BaseModel):
    id: str
    name: str
    number: str
    sections: int
    elements: int
    updated_at: str


class NewProject(BaseModel):
    info: ProjectInfo = ProjectInfo()
    design: DesignSettings | None = None
    section_name: str = "Section 1"
    element_names: list[str] = []


class NewSection(BaseModel):
    name: str


class ElementNames(BaseModel):
    names: list[str]


def _get(project_id: str) -> Project:
    try:
        return store().get(project_id)
    except ProjectNotFound:
        raise HTTPException(404, "Project not found.") from None


def _section(project: Project, section_id: str) -> Section:
    try:
        return project.section(section_id)
    except KeyError:
        raise HTTPException(404, "Section not found.") from None


@app.get("/api/projects")
def list_projects() -> list[ProjectSummary]:
    return [
        ProjectSummary(
            id=p.id,
            name=p.info.name,
            number=p.info.number,
            sections=len(p.sections),
            elements=sum(len(s.elements) for s in p.sections),
            updated_at=p.updated_at,
        )
        for p in store().list()
    ]


@app.post("/api/projects", status_code=201)
def create_project(body: NewProject) -> Project:
    section = Section(name=body.section_name)
    section.add_elements(body.element_names)
    project = Project(info=body.info, design=body.design or DesignSettings(), sections=[section])
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
    saved = store().save(body)
    kept = {s.id for s in saved.sections}
    for s in existing.sections:
        if s.id not in kept:
            store().delete_section_files(project_id, s.id)
    return saved


@app.delete("/api/projects/{project_id}", status_code=204)
def delete_project(project_id: str) -> None:
    try:
        store().delete(project_id)
    except ProjectNotFound:
        raise HTTPException(404, "Project not found.") from None


# --- Sections ----------------------------------------------------------------------


@app.post("/api/projects/{project_id}/sections", status_code=201)
def add_section(project_id: str, body: NewSection) -> Project:
    project = _get(project_id)
    if any(s.name.strip().lower() == body.name.strip().lower() for s in project.sections):
        raise HTTPException(400, f"There is already a section called '{body.name}'.")
    try:
        section = Section(name=body.name)
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_url=False, include_context=False)) from None
    project.sections.append(section)
    return store().save(project)


@app.delete("/api/projects/{project_id}/sections/{section_id}")
def delete_section(project_id: str, section_id: str) -> Project:
    project = _get(project_id)
    _section(project, section_id)
    if len(project.sections) == 1:
        raise HTTPException(400, "A project needs at least one section.")
    project.sections = [s for s in project.sections if s.id != section_id]
    store().delete_section_files(project_id, section_id)
    return store().save(project)


@app.post("/api/projects/{project_id}/sections/{section_id}/elements")
def add_elements(project_id: str, section_id: str, body: ElementNames) -> dict:
    project = _get(project_id)
    added = _section(project, section_id).add_elements(body.names)
    store().save(project)
    return {"added": added, "project": project}


# --- Workbooks ------------------------------------------------------------------------


def _import_upload(file: UploadFile) -> ImportResult:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED:
        raise HTTPException(400, f"Upload a .xlsb, .xlsx or .xlsm file (got '{suffix or 'no extension'}').")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"upload{suffix}"
        with path.open("wb") as out:
            shutil.copyfileobj(file.file, out, length=1024 * 1024)
        try:
            return import_workbook(path)
        except UnsupportedWorkbook as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:  # corrupt or password-protected files
            raise HTTPException(400, f"Could not read the workbook: {e}") from e


@app.post("/api/workbooks/check")
def check_workbook(file: UploadFile) -> dict:
    return {"file": file.filename, **_import_upload(file).summary()}


SECTION = "/api/projects/{project_id}/sections/{section_id}"


@app.post(SECTION + "/workbook")
def upload_section_workbook(project_id: str, section_id: str, file: UploadFile) -> dict:
    _section(_get(project_id), section_id)
    result = _import_upload(file)
    return store().save_workbook(project_id, section_id, file.filename or "workbook", result)


@app.get(SECTION + "/workbook")
def section_workbook(project_id: str, section_id: str) -> dict:
    _section(_get(project_id), section_id)
    summary = store().workbook_summary(project_id, section_id)
    if summary is None:
        raise HTTPException(404, "No workbook uploaded for this section yet.")
    return summary


# --- Design ------------------------------------------------------------------------------


@app.post(SECTION + "/design")
def design_section(project_id: str, section_id: str) -> dict:
    """Design the piles and combi walls of a section."""
    project = _get(project_id)
    section = _section(project, section_id)
    workbook = store().load_workbook(project_id, section_id)
    if workbook is None:
        raise HTTPException(409, "Upload this section's workbook on the Workbook tab first.")
    results = run_section(project.design, section, workbook)
    store().save_results(project_id, section_id, results)
    return results


@app.get(SECTION + "/design")
def section_results(project_id: str, section_id: str) -> dict:
    _section(_get(project_id), section_id)
    results = store().load_results(project_id, section_id)
    if results is None:
        raise HTTPException(404, "This section has not been designed yet.")
    return results


@app.get(SECTION + "/design/cages.json")
def pile_cage_export(project_id: str, section_id: str) -> JSONResponse:
    """Bar runs of every designed pile and combi wall infill of a section, for a Revit / Dynamo script."""
    project = _get(project_id)
    section = _section(project, section_id)
    results = store().load_results(project_id, section_id)
    if results is None:
        raise HTTPException(404, "This section has not been designed yet.")
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{project.info.name} {section.name}").strip("_") or "project"
    return JSONResponse(
        pile_cages(project.info.name, results, section=section.name),
        headers={"Content-Disposition": f'attachment; filename="{name}-cages.json"'},
    )


@app.get(SECTION + "/design/governing.xlsx")
def governing_sets_export(project_id: str, section_id: str) -> Response:
    """Governing sets for AdSec: 7 QP + 7 ULS per concrete station, 10 ULS rows per steel element."""
    project = _get(project_id)
    section = _section(project, section_id)
    results = store().load_results(project_id, section_id)
    if results is None:
        raise HTTPException(404, "This section has not been designed yet.")
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{project.info.name} {section.name}").strip("_") or "project"
    return Response(
        governing_workbook(project.info.name, section.name, results),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}-governing-sets.xlsx"'},
    )


@app.get(SECTION + "/design/adsec.zip")
def adsec_export(project_id: str, section_id: str) -> Response:
    """AdSec 8.3 files (.ads), one per pile part and combi wall infill part, with its 7 QP and 7 ULS loads."""
    project = _get(project_id)
    section = _section(project, section_id)
    results = store().load_results(project_id, section_id)
    if results is None:
        raise HTTPException(404, "This section has not been designed yet.")
    d = project.design
    info = {}
    for name, e in section.elements.items():
        if isinstance(e, PileInput):
            p = with_project_grades(e, d.materials, d.durability)
            info[name] = {"diameter": p.diameter, "concrete": p.concrete, "cover": p.cover}
        elif isinstance(e, CombiWallInput):
            w = with_project_grades(e, d.materials, d.durability)
            info[f"{name} infill"] = {
                "diameter": w.tube_diameter - 2 * w.tube_thickness,
                "concrete": w.concrete,
                "cover": w.cover,
            }
    files = adsec.section_files(project.info.name, section.name, results, info, d.reinforcement.grade)
    if not files:
        raise HTTPException(404, "No designed piles or combi wall infill in this section.")
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{project.info.name} {section.name}").strip("_") or "project"
    return Response(
        adsec.zip_files(files),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}-adsec.zip"'},
    )


@app.get(SECTION + "/design/report.{fmt}")
def design_report(project_id: str, section_id: str, fmt: str, detail: str = "summary") -> Response:
    """The calculation report of a designed section: summary or detailed, as Word, PDF or Excel."""
    if fmt not in RENDERERS:
        raise HTTPException(404, "Reports are Word (.docx), PDF (.pdf) or Excel (.xlsx).")
    if detail not in ("summary", "detailed"):
        raise HTTPException(422, "detail is 'summary' or 'detailed'.")
    project = _get(project_id)
    section = _section(project, section_id)
    results = store().load_results(project_id, section_id)
    if results is None:
        raise HTTPException(404, "This section has not been designed yet.")
    rep = build_report(project, section, results, detail)
    name = (
        re.sub(r"[^A-Za-z0-9._-]+", "_", f"{project.info.name} {section.name} {detail}").strip("_")
        or "report"
    )
    render, media = RENDERERS[fmt]
    return Response(
        render(rep),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}.{fmt}"'},
    )


@app.get(SECTION + "/geometry")
def geometry(project_id: str, section_id: str) -> dict:
    """Element geometry for the 3D view, with the directions found in the workbook check."""
    _section(_get(project_id), section_id)
    wb = store().load_workbook(project_id, section_id)
    if wb is None:
        raise HTTPException(409, "Upload this section's workbook on the Workbook tab first.")
    summary = store().workbook_summary(project_id, section_id) or {}
    return {"elements": section_geometry(wb), "axes": summary.get("axes", [])}


@app.get(SECTION + "/spw.xlsx")
def spw_export(project_id: str, section_id: str) -> Response:
    """Sheet pile wall straining actions (Plaxis sign, multipliers applied) for the sheet pile program."""
    project = _get(project_id)
    section = _section(project, section_id)
    wb = store().load_workbook(project_id, section_id)
    if wb is None:
        raise HTTPException(409, "Upload this section's workbook on the Workbook tab first.")
    elements = factored_elements(section, wb)
    spw = [
        name
        for name, combos in elements.items()
        if any(s.parsed and s.parsed.spec.type is ElementType.SHEET_PILE_WALL for s in combos.values())
    ]
    if not spw:
        raise HTTPException(404, "The workbook has no sheet pile wall sheets.")
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{project.info.name} {section.name} {spw[0]}").strip("_")
    return Response(
        spw_workbook(project.info.name, section.name, spw[0], elements[spw[0]]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}-straining-actions.xlsx"'},
    )
