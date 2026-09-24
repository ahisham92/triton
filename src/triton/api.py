"""Web API: projects, material catalogue and workbook checks."""

from __future__ import annotations

import contextlib
import hashlib
import html
import json
import os
import re
import secrets
import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from . import (
    adsec,
    checker,
    clash_report,
    drawings,
    durability,
    dxf,
    fresh,
    furniture_report,
    method,
    package,
    revit,
    trials,
)
from . import furniture as furniture_mod
from .alignment import plan_geometry
from .clashes import Clashes, _clean, assumptions, find_clashes
from .costing import cost_project
from .design.export import pile_cages
from .design.governing import workbook as governing_workbook
from .design.runner import along_axis, factored_elements, run_section, section_alignment
from .design.spw import workbook as spw_workbook
from .elements import ElementType
from .geometry import section_geometry
from .importer import is_header
from .joints import joints_drawing, section_joints
from .materials import catalogue
from .project import (
    CombiWallInput,
    DesignSettings,
    ElementCheck,
    PileInput,
    Project,
    ProjectInfo,
    Section,
    SheetPileInput,
    WhatIf,
    _now,
    with_project_grades,
)
from .reader import UnsupportedWorkbook
from .report import RENDERERS, build_report
from .review import choices
from .stepped import assemble, is_read, read_step
from .store import UPLOAD_ID, ProjectNotFound, ProjectStore, housekeeping
from .suggest import suggest
from .validation import (
    MERGE_MODES,
    ImportResult,
    apply_mapping,
    apply_section,
    combination_check,
    drop_sheets,
    edit_sheet,
    import_workbook,
    merge_workbooks,
)

STATIC = Path(__file__).parent / "static"
ALLOWED = {".xlsb", ".xlsx", ".xlsm"}

app = FastAPI(title="Triton")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def store() -> ProjectStore:
    # Created per request so TRITON_DATA_DIR can change between tests.
    return ProjectStore()


@app.get("/")
def index() -> HTMLResponse:
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    # A new version of a file gets a new address, so no browser keeps running an old one.
    for name in ("app.js", "style.css"):
        page = page.replace(f"static/{name}", f"static/{name}?v={int((STATIC / name).stat().st_mtime)}")
    # The modules app.js imports get new addresses too, through an import map.
    modules = {
        f"./static/{f.name}": f"./static/{f.name}?v={int(f.stat().st_mtime)}"
        for f in sorted(STATIC.glob("*.js"))
        if f.name != "app.js"
    }
    imports = f'<script type="importmap">{json.dumps({"imports": modules})}</script>\n'
    page = page.replace('<script type="module"', imports + '<script type="module"', 1)
    home = os.environ.get("TRITON_HOME_URL")
    if home:
        # Hosted inside another site (e.g. Project Control): a way back to it in the header.
        label = os.environ.get("TRITON_HOME_LABEL") or "Home"
        link = f'<a class="home" href="{html.escape(home)}">&larr; {html.escape(label)}</a>'
        page = page.replace("</div></header>", link + "</div></header>", 1)
    return HTMLResponse(page)


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
    copy_from: str | None = Field(None, description="A section whose settings the new one starts with.")


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
    housekeeping()
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


@app.get("/api/projects/{project_id}/project.trt")
def download_project(project_id: str) -> StreamingResponse:
    """The whole project as one file (.trt) to send to someone: settings, sections, workbooks, results
    and trials."""
    project = _get(project_id)
    f, size = package.spool(store(), project)

    def chunks():
        with f:
            while block := f.read(1024 * 1024):
                yield block

    return StreamingResponse(
        chunks(),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{package.file_name(project)}"',
            "Content-Length": str(size),
        },
    )


class OpenProject(BaseModel):
    # ask: say if a project already has this name; keep: open it beside it (named "… (2)");
    # replace: open it and delete the project ``replace_id``.
    if_exists: Literal["ask", "keep", "replace"] = "ask"
    replace_id: str | None = None


@app.post("/api/projects/open/{upload_id}")
def open_project(upload_id: str, body: OpenProject) -> dict:
    """Open an uploaded .trt as a project on the server. When a project has its name and nothing was
    decided, the answer lists them (``exists``) and the upload stays for the second call."""
    d = _upload_dir(upload_id)
    path = _upload_file(d)
    if path.suffix != package.SUFFIX:
        raise HTTPException(400, "Open a Triton project file (.trt).")
    try:
        head = package.peek(path)
    except package.BadPackage as e:
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(400, str(e)) from e
    same = [p for p in store().list() if p.info.name == head["name"]]
    if same and body.if_exists == "ask":
        return {
            "name": head["name"],
            "exists": [{"id": p.id, "name": p.info.name, "updated_at": p.updated_at} for p in same],
        }
    old = None
    if body.if_exists == "replace":
        old = next((p for p in same if p.id == body.replace_id), None)
        if old is None:
            raise HTTPException(409, "The project to replace is not here any more. Open the file again.")
    name = head["name"] if body.if_exists == "replace" else package.unique_name(store(), head["name"])
    try:
        with _Progress(upload_id) as tell:
            project, notes = package.open_package(store(), path, name, tell)
    except package.BadPackage as e:
        raise HTTPException(400, str(e)) from e
    finally:
        shutil.rmtree(d, ignore_errors=True)
    if old is not None:
        store().delete(old.id)
    return {"id": project.id, "name": project.info.name, "notes": notes, "replaced": old is not None}


@app.get("/api/projects/{project_id}/method")
def project_method(project_id: str) -> dict:
    """The Method tab: how each kind of element in the project is designed, and the options in use."""
    return method.view(_get(project_id))


LOCKED = "The model is locked since it was designed. Press Unlock to edit first."


def _model(p: Project) -> dict:
    """What the lock protects: everything the design depends on (not prices, costing or drawing names)."""
    d = p.model_dump(
        mode="json", exclude={"locked", "created_at", "updated_at", "prices", "drawings", "furniture"}
    )
    d.pop("info")  # names, numbers and who designed it: open to edit at any time
    for s in d["sections"]:
        s.pop("costing", None)
        s.pop("checks", None)  # the checker's status is not a design input
        s.pop("displacements", None)  # typed in as received, checked as they are
        s.pop("user_cages", None)  # set on the Design tab, then checked
        s.pop("beam_cages", None)
        s.pop("slab_strips", None)
        s.pop("clashes", None)  # the Clashes tab never changes the design
        s.pop("furniture", None)  # designed on its own tab, from the element design
        for el in s.get("elements", {}).values():  # a sheet pile wall's "ignore N or Q", ticked on its card
            if el.get("kind") == "sheet_pile_wall":
                el.pop("ignore", None)
    return d


def _unlocked(project: Project) -> Project:
    if project.locked:
        raise HTTPException(409, LOCKED)
    return project


@app.put("/api/projects/{project_id}")
def update_project(project_id: str, body: Project) -> Project:
    existing = _get(project_id)
    if body.id != project_id:
        raise HTTPException(400, "Project id in the body does not match the URL.")
    if existing.locked and body.locked and _model(body) != _model(existing):
        raise HTTPException(409, LOCKED)
    body.created_at = existing.created_at
    _stamp_multipliers(existing, body)
    # The Clashes tab saves its own settings and what-ifs; a page holding older ones never undoes them.
    clash = {s.id: (s.clashes, s.checks) for s in existing.sections}
    for s in body.sections:
        if s.id in clash:
            s.clashes, s.checks = clash[s.id]
    saved = store().save(body)
    kept = {s.id for s in saved.sections}
    for s in existing.sections:
        if s.id not in kept:
            store().delete_section_files(project_id, s.id)
    if not saved.locked:
        _drop_stale(project_id, saved)
    return saved


def _stamp_multipliers(existing: Project, body: Project) -> None:
    """Each load multiplier remembers when it last changed, so tabs uploaded after it show up."""
    before = {s.id: s for s in existing.sections}
    now = _now()
    for section in body.sections:
        old = before.get(section.id)
        kept = {(r.factor, tuple(r.sheets), r.note): r.applied_at for r in old.load_factors} if old else {}
        for rule in section.load_factors:
            key = (rule.factor, tuple(rule.sheets), rule.note)
            rule.applied_at = kept[key] if key in kept else now


def _drop_stale(project_id: str, project: Project) -> None:
    """Unlocked to edit, results that no longer match their inputs are deleted rather than kept out of
    date: only what a change affects goes (all of a section's for a shared input), so the others can
    still be used and the changed elements designed on their own."""
    for section in project.sections:
        results = store().load_results(project_id, section.id)
        if not results:
            continue
        now = fresh.fingerprint(project, section, store().workbook_summary(project_id, section.id))
        changed, stale = fresh.status(results, now, fresh.names(project, section))
        designed = {e["element"] for e in fresh._designed(results)}
        gone = designed & set(stale)
        if changed is None or not gone:
            continue
        if gone == designed:
            store().delete_results(project_id, section.id)
            continue
        for kind in fresh.KINDS:
            results[kind] = [e for e in results.get(kind) or [] if e["element"] not in gone]
        for name in gone:
            (results.get("element_inputs") or {}).pop(name, None)
        store().save_results(project_id, section.id, results)


@app.get("/api/projects/{project_id}/storage")
def project_storage(project_id: str) -> dict:
    """Space each section takes: workbook, rows kept for editing, design results."""
    project = _get(project_id)
    used = store().usage(project_id, [s.id for s in project.sections])
    sections = [{"id": s.id, "name": s.name, **used[s.id]} for s in project.sections]
    return {
        "sections": sections,
        "total": sum(x["total"] for x in sections),
        "results": sum(x["results"] for x in sections),
    }


class DuplicateProject(BaseModel):
    name: str | None = None


@app.post("/api/projects/{project_id}/duplicate")
def duplicate_project(project_id: str, body: DuplicateProject) -> Project:
    """A copy of the whole project (sections, workbooks, results, trials), named ``name`` or "… copy"."""
    project = _get(project_id)
    name = (body.name or "").strip() or f"{project.info.name} copy"
    return store().duplicate(project.id, package.unique_name(store(), name))


@app.delete("/api/projects/{project_id}", status_code=204)
def delete_project(project_id: str) -> None:
    try:
        store().delete(project_id)
    except ProjectNotFound:
        raise HTTPException(404, "Project not found.") from None


# --- Sections ----------------------------------------------------------------------


# What belongs to a section's workbook rather than its settings: not copied to a new section.
WORKBOOK_OWN = {
    "id",
    "name",
    "sheet_map",
    "combination_map",
    "review",
    "excluded_peaks",
    "user_cages",
    "beam_cages",
    "slab_strips",
}


@app.post("/api/projects/{project_id}/sections", status_code=201)
def add_section(project_id: str, body: NewSection) -> Project:
    project = _unlocked(_get(project_id))
    if any(s.name.strip().lower() == body.name.strip().lower() for s in project.sections):
        raise HTTPException(400, f"There is already a section called '{body.name}'.")
    settings = {}
    if body.copy_from:
        settings = _section(project, body.copy_from).model_dump(mode="json", exclude=WORKBOOK_OWN)
    try:
        section = Section.model_validate({**settings, "name": body.name})
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_url=False, include_context=False)) from None
    project.sections.append(section)
    return store().save(project)


@app.delete("/api/projects/{project_id}/sections/{section_id}")
def delete_section(project_id: str, section_id: str) -> Project:
    project = _unlocked(_get(project_id))
    _section(project, section_id)
    if len(project.sections) == 1:
        raise HTTPException(400, "A project needs at least one section.")
    project.sections = [s for s in project.sections if s.id != section_id]
    store().delete_section_files(project_id, section_id)
    return store().save(project)


@app.post("/api/projects/{project_id}/sections/{section_id}/elements")
def add_elements(project_id: str, section_id: str, body: ElementNames) -> dict:
    project = _unlocked(_get(project_id))
    added = _section(project, section_id).add_elements(body.names)
    store().save(project)
    return {"added": added, "project": project}


# --- Workbooks ------------------------------------------------------------------------


def _suffix(filename: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED:
        raise HTTPException(400, f"Upload a .xlsb, .xlsx or .xlsm file (got '{suffix or 'no extension'}').")
    return suffix


def _import_path(path: Path, progress: Callable[[float, str], None] | None = None) -> ImportResult:
    try:
        return import_workbook(path, progress)
    except UnsupportedWorkbook as e:
        raise HTTPException(400, str(e)) from e
    except Stopped:
        raise
    except Exception as e:  # corrupt or password-protected files
        raise HTTPException(400, f"Could not read the workbook: {e}") from e


def _import_upload(file: UploadFile) -> ImportResult:
    suffix = _suffix(file.filename or "")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"upload{suffix}"
        with path.open("wb") as out:
            shutil.copyfileobj(file.file, out, length=1024 * 1024)
        return _import_path(path)


# --- Progress of long steps -------------------------------------------------------------------
# Reading a whole project's workbook or designing a section takes a while. The step writes how far
# it has got to a small file; the page asks for it (from another worker) to show a percentage and
# the time left.

PROGRESS_KEY = re.compile(r"[0-9a-zA-Z_-]{1,80}")


def _progress_path(key: str) -> Path:
    if not PROGRESS_KEY.fullmatch(key):
        raise HTTPException(404, "No such step.")
    root = Path(os.environ.get("TRITON_DATA_DIR", "data")) / "progress"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{key}.json"


class Stopped(Exception):
    """The user pressed Stop; raised at the next sheet or element."""


@app.exception_handler(Stopped)
def _stopped(_request: Request, _exc: Stopped) -> JSONResponse:
    return JSONResponse({"detail": "Stopped."}, status_code=409)


class _Progress:
    def __init__(self, key: str) -> None:
        self.path, self.started, self.written = _progress_path(key), time.time(), 0.0
        self.stop = self.path.with_suffix(".stop")
        self.stop.unlink(missing_ok=True)  # a Stop pressed on an earlier run
        self(0.0, "Starting")

    def __call__(self, fraction: float, step: str) -> None:
        if self.stop.exists():
            raise Stopped
        now = time.time()
        if now - self.written < 0.5 and 0 < fraction < 1:
            return
        self.written = now
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"fraction": round(fraction, 4), "step": step, "started": self.started}), "utf-8"
        )
        tmp.replace(self.path)

    def __enter__(self) -> _Progress:
        return self

    def __exit__(self, *_: object) -> None:
        self.path.unlink(missing_ok=True)
        self.stop.unlink(missing_ok=True)


@app.post("/api/progress/{key}/stop", status_code=202)
def stop(key: str) -> dict:
    """Ask a running step to stop; it stops at its next sheet or element."""
    path = _progress_path(key)
    if not path.exists():
        raise HTTPException(404, "Not running.")
    path.with_suffix(".stop").touch()
    return {"stopping": True}


@app.get("/api/progress/{key}")
def progress(key: str) -> dict:
    path = _progress_path(key)
    try:
        state = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as e:
        raise HTTPException(404, "Not running.") from e
    elapsed = time.time() - state["started"]
    fraction = state["fraction"]
    remaining = elapsed * (1 - fraction) / fraction if fraction >= 0.03 and elapsed >= 2 else None
    return {
        "fraction": fraction,
        "step": state["step"],
        "elapsed_s": round(elapsed),
        "remaining_s": None if remaining is None else round(remaining),
    }


# --- Uploads in pieces --------------------------------------------------------------------
# Hosts cap the size of one request (PythonAnywhere at about 100 MB), and a whole project's
# workbook can be larger. The browser sends it in pieces to /api/uploads/{id}, then asks for it
# to be checked or kept by that id. A finished or abandoned upload is deleted.


def _uploads() -> Path:
    root = Path(os.environ.get("TRITON_DATA_DIR", "data")) / "uploads"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _upload_dir(upload_id: str) -> Path:
    d = _uploads() / upload_id
    if not UPLOAD_ID.fullmatch(upload_id) or not d.is_dir():
        raise HTTPException(404, "That upload is not here (it may have expired). Upload the file again.")
    return d


class NewUpload(BaseModel):
    filename: str
    size: int = 0


@app.post("/api/uploads", status_code=201)
def start_upload(body: NewUpload) -> dict:
    is_project = Path(body.filename or "").suffix.lower() == package.SUFFIX
    suffix = package.SUFFIX if is_project else _suffix(body.filename)
    housekeeping(force=True)  # abandoned uploads and the like
    upload_id = secrets.token_hex(16)
    d = _uploads() / upload_id
    d.mkdir()
    (d / "name").write_text(body.filename, "utf-8")
    (d / f"data{suffix}").touch()
    return {"id": upload_id, "received": 0}


@app.delete("/api/uploads/{upload_id}", status_code=204)
def drop_upload(upload_id: str) -> Response:
    shutil.rmtree(_upload_dir(upload_id), ignore_errors=True)
    return Response(status_code=204)


def _upload_file(d: Path) -> Path:
    return next(d.glob("data.*"))


@app.put("/api/uploads/{upload_id}")
async def upload_piece(upload_id: str, request: Request, offset: int = 0) -> dict:
    """Append one piece at ``offset``. Sending a piece again (after a dropped connection) replaces it."""
    d = _upload_dir(upload_id)
    path = _upload_file(d)
    size = path.stat().st_size
    if offset > size or offset < 0:
        raise HTTPException(409, f"Expected a piece at byte {size}, got one at {offset}.")
    with path.open("r+b") as out:
        out.truncate(offset)
        out.seek(offset)
        async for chunk in request.stream():
            out.write(chunk)
        received = out.tell()
    return {"id": upload_id, "received": received}


# How long one reading step runs before it answers (a host ends requests that run too long).
READ_STEP_S = float(os.environ.get("TRITON_READ_STEP_S", "4"))


@app.post("/api/uploads/{upload_id}/read")
def read_upload(upload_id: str) -> dict:
    """Read the next few sheets of a joined upload; the page asks again until ``done``."""
    d = _upload_dir(upload_id)
    try:
        return read_step(d, _upload_file(d), READ_STEP_S)
    except UnsupportedWorkbook as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # corrupt or password-protected files
        raise HTTPException(400, f"Could not read the workbook: {e}") from e


def _take_upload(upload_id: str, keep: Callable[[str, ImportResult], dict] | None = None) -> dict:
    """Read the joined upload (progress under its id), or put together the sheets its reading steps
    kept, hand it to ``keep``, then delete it."""
    d = _upload_dir(upload_id)
    try:
        with _Progress(upload_id) as tell:
            filename = (d / "name").read_text("utf-8")
            if is_read(d):
                tell(0.9, "Checking the sheets")
                result = assemble(d)
            else:
                result = _import_path(_upload_file(d), tell)
            if keep is None:
                return {"file": filename, **result.summary()}
            tell(0.95, "Saving")
            return keep(filename, result)
    finally:
        shutil.rmtree(d, ignore_errors=True)


@app.post("/api/workbooks/check")
def check_workbook(file: UploadFile) -> dict:
    return {"file": file.filename, **_import_upload(file).summary()}


@app.post("/api/workbooks/check/{upload_id}")
def check_uploaded_workbook(upload_id: str) -> dict:
    return _take_upload(upload_id)


SECTION = "/api/projects/{project_id}/sections/{section_id}"


class CheckUpdate(BaseModel):
    status: Literal["designed", "comments", "checked", "approved"]
    by: str = ""
    comment: str = ""


@app.put(SECTION + "/checks/{element}")
def set_check(project_id: str, section_id: str, element: str, body: CheckUpdate) -> Project:
    """The checker's status for one element (open while the model is locked: it is not a design input)."""
    project = _get(project_id)
    section = _section(project, section_id)
    if element not in section.elements:
        raise HTTPException(404, f"No element named {element} in {section.name}.")
    results = store().load_results(project_id, section_id) or {}
    section.checks[element] = ElementCheck(
        status=body.status,
        by=body.by.strip(),
        comment=body.comment.strip(),
        at=_now(),
        design_run_at=results.get("run_at"),
    )
    return store().save(project)


def _keeper(project_id: str, section_id: str, mode: str) -> Callable[[str, ImportResult], dict]:
    """Saves an uploaded workbook into a section: replacing its workbook, or (``update``, ``add``)
    brought into the one it has; see ``merge_workbooks``."""
    section = _section(_unlocked(_get(project_id)), section_id)
    if mode not in MERGE_MODES:
        raise HTTPException(422, "mode is replace, update or add.")

    def keep(filename: str, result: ImportResult) -> dict:
        old = store().load_workbook(project_id, section_id) if mode != "replace" else None
        if old is None:
            store().save_workbook(project_id, section_id, filename, result)
            return section_workbook(project_id, section_id)
        mapping = {k: v.model_dump() for k, v in section.sheet_map.items()}
        merged, what = merge_workbooks(old, result, mode, mapping)
        before = (store().workbook_summary(project_id, section_id) or {}).get("file") or "workbook"
        new = [r.split(" → ")[-1] for r in what["replaced"]] + list(what["added"])
        store().save_workbook(
            project_id, section_id, f"{before} + {filename}", merged, replace=False, new_sheets=new
        )
        renamed = _carry_renamed_sheets(project_id, section_id, what["replaced"])
        merged_what = {**what, "renamed": renamed} if renamed else what
        return {**section_workbook(project_id, section_id), "merged": merged_what}

    return keep


def _carry_renamed_sheets(project_id: str, section_id: str, replaced: list[str]) -> list[str]:
    """A tab replaced by one of another name ("old → new") keeps its load multiplier and its
    mapping under the new name, so it is multiplied once, as before."""
    pairs = [r.split(" → ") for r in replaced if " → " in r]
    if not pairs:
        return []
    project = _get(project_id)
    section = _section(project, section_id)
    out = []
    for old, new in pairs:
        for rule in section.load_factors:
            if old in rule.sheets:
                rule.sheets = [new if n == old else n for n in rule.sheets]
                out.append(f"{old} → {new}: ×{rule.factor:g} kept")
        if old in section.sheet_map and new not in section.sheet_map:
            section.sheet_map[new] = section.sheet_map.pop(old)
    store().save(project)
    return out


class SheetNames(BaseModel):
    sheets: list[str] = Field(min_length=1)


@app.delete(SECTION + "/workbook", status_code=204)
def delete_workbook(project_id: str, section_id: str) -> Response:
    """Delete the section's whole workbook and its results, back to a blank section."""
    _section(_unlocked(_get(project_id)), section_id)
    store().delete_workbook(project_id, section_id)
    return Response(status_code=204)


@app.post(SECTION + "/workbook/delete")
def delete_workbook_sheets(project_id: str, section_id: str, body: SheetNames) -> dict:
    """Delete some tabs of the section's workbook; the rest is checked again."""
    _section(_unlocked(_get(project_id)), section_id)
    wb = store().load_workbook(project_id, section_id)
    if wb is None:
        raise HTTPException(404, "No workbook uploaded for this section yet.")
    names = set(body.sheets)
    unknown = names - {s.name for s in wb.sheets}
    if unknown:
        raise HTTPException(404, f"No sheet named {', '.join(sorted(unknown))} in this section's workbook.")
    if names >= {s.name for s in wb.sheets}:
        store().delete_workbook(project_id, section_id)
        return {"deleted": sorted(names), "workbook": None}
    summary = store().workbook_summary(project_id, section_id) or {}
    store().save_workbook(
        project_id, section_id, summary.get("file") or "workbook", drop_sheets(wb, names), replace=False
    )
    store().drop_raw(project_id, section_id, names)
    return {"deleted": sorted(names), "workbook": section_workbook(project_id, section_id)}


@app.post(SECTION + "/workbook")
def upload_section_workbook(
    project_id: str, section_id: str, file: UploadFile, mode: str = "replace"
) -> dict:
    keep = _keeper(project_id, section_id, mode)
    return keep(file.filename or "workbook", _import_upload(file))


@app.post(SECTION + "/workbook/{upload_id}")
def keep_uploaded_workbook(project_id: str, section_id: str, upload_id: str, mode: str = "replace") -> dict:
    return _take_upload(upload_id, _keeper(project_id, section_id, mode))


def _sheet_map(section: Section) -> dict[str, dict]:
    return {k: v.model_dump() for k, v in section.sheet_map.items()}


def _sizes(section: Section) -> dict[str, str]:
    """Which elements are alike, for the point-count check: piles by diameter, king piles by tube."""
    out = {}
    for name, e in section.elements.items():
        if isinstance(e, PileInput):
            out[name] = f"Ø{e.diameter:g} piles"
        elif isinstance(e, CombiWallInput):
            out[name] = f"Ø{e.tube_diameter:g} king piles"
    return out


def _view(raw: ImportResult, section: Section) -> ImportResult:
    """The workbook as the section reads it."""
    return apply_section(
        raw,
        _sheet_map(section),
        section.combinations,
        section.combination_map,
        section.review,
        _sizes(section),
    )


def _workbook(project_id: str, section: Section) -> ImportResult | None:
    """The section's stored workbook as the section reads it: its sheet mapping applied and each
    combination read as one of the section's load combinations."""
    wb = store().load_workbook(project_id, section.id)
    if wb is None:
        return None
    return _view(wb, section)


# The checked workbook depends on the stored workbook, the section's reading of it and this code;
# it is kept once worked out, so opening the Workbook tab again only reads a file.
_CODE = hashlib.sha1(
    b"".join(p.read_bytes() for p in sorted(Path(__file__).parent.rglob("*.py")))
).hexdigest()[:12]


def _view_key(summary: dict, section: Section) -> str:
    what = {
        "code": _CODE,
        "version": summary.get("version"),
        "sheet_map": _sheet_map(section),
        "combinations": section.combinations,
        "combination_map": section.combination_map,
        "review": section.review,
        "sizes": _sizes(section),
        "elements": list(section.elements),
    }
    return hashlib.sha1(json.dumps(what, sort_keys=True, default=str).encode()).hexdigest()


@app.get(SECTION + "/workbook")
def section_workbook(
    project_id: str, section_id: str, progress: str | None = None, fresh: bool = False
) -> dict:
    """The section's workbook as it reads it; ``fresh`` checks it again even if nothing changed."""
    section = _section(_get(project_id), section_id)
    summary = store().workbook_summary(project_id, section_id)
    if summary is None:
        raise HTTPException(404, "No workbook uploaded for this section yet.")
    key = _view_key(summary, section)
    kept = None if fresh else store().load_view(project_id, section_id, key)
    if kept is not None:
        return kept
    with _Progress(progress) if progress else contextlib.nullcontext(lambda *_: None) as tell:
        tell(0.02, "Loading the workbook")
        raw = store().load_workbook(project_id, section_id)
        if raw is None:
            return summary
        tell(0.35, "Checking the sheets for this section")
        wb = _view(raw, section)
        tell(0.8, "Checking the load combinations")
        summary = {**summary, **wb.summary(), "sheet_map": list(section.sheet_map)}
        check = combination_check(
            apply_mapping(raw, _sheet_map(section)), section.combinations, section.combination_map
        )
        summary["combination_check"] = check
        summary["suggestions"] = suggest(summary["sheets"], list(section.elements), section.combinations)
        tell(0.95, "Keeping the result")
        store().save_view(project_id, section_id, key, summary)
    return summary


@app.get(SECTION + "/workbook/brief")
def section_workbook_brief(project_id: str, section_id: str) -> dict:
    """What the section's workbook is, without reading it: the file, when it was uploaded, its tabs
    and its warnings (as the section reads them when that is already worked out, else as found on
    upload)."""
    section = _section(_get(project_id), section_id)
    summary = store().workbook_summary(project_id, section_id)
    if summary is None:
        raise HTTPException(404, "No workbook uploaded for this section yet.")
    kept = store().load_view(project_id, section_id, _view_key(summary, section))
    counts = (kept or summary).get("counts") or {}
    return {
        "file": summary.get("file"),
        "uploaded_at": summary.get("uploaded_at"),
        "sheets": [{"name": s["name"]} for s in summary.get("sheets", [])],
        "elements": summary.get("elements", []),
        "counts": counts,
        "checked": kept is not None,
    }


# --- Sheets as read, to see and edit where a warning is ----------------------------------------

SHEET_PAGE = 200


def _raw_sheet(project_id: str, section: Section, name: str) -> tuple[ImportResult, list]:
    wb = store().load_workbook(project_id, section.id)
    if wb is None:
        raise HTTPException(404, "No workbook uploaded for this section yet.")
    if not any(s.name == name for s in wb.sheets):
        raise HTTPException(404, f"No sheet named '{name}' in this section's workbook.")
    return wb, store().load_raw(project_id, section.id, name)


def _cell(v):
    if v is None or isinstance(v, (int, float, str, bool)):
        return v
    return str(v)


@app.get(SECTION + "/workbook/sheet")
def workbook_sheet(
    project_id: str,
    section_id: str,
    name: str,
    start: int | None = None,
    show: Literal["all", "flagged"] = "all",
    code: str | None = None,
    sort: int | None = None,
    desc: bool = False,
) -> dict:
    """A page of a sheet's rows as read, with the rows each warning points at; ``start`` is the
    0-based row to begin at (default: a little above the first flagged row).

    A view (``show`` flagged rows, only those of a warning ``code``, ``sort`` by a 0-based column)
    lists the data rows under the header in that order; ``start`` is then a place in that list.
    It only changes what is shown: every row keeps its Excel number."""
    section = _section(_get(project_id), section_id)
    wb, raw = _raw_sheet(project_id, section, name)
    sheet = next(s for s in wb.sheets if s.name == name)
    issues = [i for i in sheet.issues + wb.issues if i.sheet == name]
    flags: dict[int, list[dict]] = {}
    for i in issues:
        for r in i.rows:
            flags.setdefault(r, []).append(
                {
                    "id": i.id,
                    "code": i.code,
                    "severity": i.severity.value,
                    "message": " ".join(filter(None, [i.message, (getattr(i, "notes", None) or {}).get(r)])),
                    "decision": section.review.get(i.id),
                }
            )
    if raw is None:  # uploaded before the rows were kept: show the rows as cleaned
        f = sheet.frame
        cols = [c for c in f.columns if c != "excel_row"]
        by_row = (
            {int(r["excel_row"]): [_cell(r[c]) for c in cols] for _, r in f.iterrows()} if not f.empty else {}
        )
        last = max(by_row, default=0)
        rows = [[*cols]] + [by_row.get(n, []) for n in range(2, last + 1)]
        editable = False
    else:
        rows, editable = raw, True
    header = next((n for n, r in enumerate(rows[:200]) if is_header(r)), None) if editable else 0
    view = show == "flagged" or code is not None or sort is not None
    if view:
        numbers = [n + 1 for n in range((header or -1) + 1, len(rows)) if not is_header(rows[n])]
        if show == "flagged" or code:
            numbers = [n for n in numbers if any(code in (None, f["code"]) for f in flags.get(n, []))]
        if sort is not None:
            # Numbers in order, then text, then empty cells (still in Excel order), either way round.
            nums, texts, empties = [], [], []
            for n in numbers:
                v = rows[n - 1][sort] if sort < len(rows[n - 1]) else None
                if v is None or (isinstance(v, str) and not v.strip()):
                    empties.append(n)
                elif isinstance(v, (int, float)) and not isinstance(v, bool):
                    nums.append((v, n))
                else:
                    try:
                        nums.append((float(str(v).strip()), n))
                    except ValueError:
                        texts.append((str(v).strip().lower(), n))
            nums.sort(key=lambda x: x[0], reverse=desc)  # stable: equal values stay in Excel order
            texts.sort(key=lambda x: x[0], reverse=desc)
            numbers = [n for _, n in nums] + [n for _, n in texts] + empties
        start = max(0, min(start or 0, max(len(numbers) - 1, 0)))
    else:
        first = min(flags, default=1) - 1
        if start is None:
            start = max(0, first - 5)
        start = max(0, min(start, max(len(rows) - 1, 0)))
        numbers = list(range(1, len(rows) + 1))
    shown = numbers[start : start + SHEET_PAGE]
    page = [[_cell(v) for v in rows[n - 1]] for n in shown]
    head_cells = [_cell(v) for v in rows[header]] if header is not None and rows else []
    width = max([len(r) for r in page] + [len(head_cells)], default=0)
    return {
        "name": name,
        "start": start,
        "numbers": shown,
        "view": view,
        "total": len(numbers),
        "width": width,
        "rows": page,
        "header_row": header + 1 if header is not None else None,
        "header": head_cells,
        "flags": {str(k): v for k, v in flags.items()},
        "flagged_rows": sorted(flags),
        "issues": [
            {**i.to_dict(), "choices": choices(i.code), "decision": section.review.get(i.id)} for i in issues
        ],
        "editable": editable,
    }


@app.get(SECTION + "/workbook/checker.xlsx")
def workbook_checker(project_id: str, section_id: str) -> Response:
    """The Checker: the sheets with warnings, flagged rows highlighted with the reason as a note."""
    project = _get(project_id)
    section = _section(project, section_id)
    summary = section_workbook(project_id, section_id)
    data = checker.build(
        summary["issues"], lambda name: store().load_raw(project_id, section_id, name), section.review
    )
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{project.info.name} {section.name} checker").strip("_")
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'},
    )


class CellEdit(BaseModel):
    row: int  # Excel row, 1-based
    col: int  # column, 0-based
    value: str | float | None = None


class SheetEdits(BaseModel):
    edits: list[CellEdit]


def _value(v):
    if isinstance(v, str):
        t = v.strip()
        if not t:
            return None
        try:
            return float(t)
        except ValueError:
            return t
    return v


@app.put(SECTION + "/workbook/sheet")
def edit_workbook_sheet(project_id: str, section_id: str, name: str, body: SheetEdits) -> dict:
    """Change cells of a sheet as read; the sheet is cleaned and the workbook checked again, as if
    the corrected workbook had been uploaded."""
    section = _section(_unlocked(_get(project_id)), section_id)
    wb, raw = _raw_sheet(project_id, section, name)
    if raw is None:
        raise HTTPException(
            409, "This workbook was uploaded before its rows were kept: upload it again to edit it."
        )
    rows = [list(r) for r in raw]
    for e in body.edits:
        if e.row < 1 or e.col < 0 or e.row > len(rows) + 1000 or e.col > 200:
            raise HTTPException(422, f"Row {e.row}, column {e.col + 1} is outside the sheet.")
        while len(rows) < e.row:
            rows.append([])
        r = rows[e.row - 1]
        while len(r) <= e.col:
            r.append(None)
        r[e.col] = _value(e.value)
    edited = edit_sheet(wb, name, rows)
    summary = store().workbook_summary(project_id, section_id) or {}
    filename = summary.get("file") or "workbook"
    if not filename.endswith("(edited)"):
        filename += " (edited)"
    store().save_workbook(project_id, section_id, filename, edited, replace=False)
    return section_workbook(project_id, section_id)


# --- Design ------------------------------------------------------------------------------


class DesignRequest(BaseModel):
    elements: list[str] | None = Field(None, description="The elements to design; empty: all of them.")
    budget_s: float | None = Field(
        None, gt=0, description="Start no element after this long; the rest come back in 'left'."
    )


def _merge(
    old: dict | None, new: dict, handled: list[str], section: Section, names: list[str] | None = None
) -> dict:
    """The section's results with the elements just designed replacing their earlier results; the
    other elements keep theirs."""
    old = old or {}
    done, kept = set(handled), set(section.elements if names is None else names)
    out = {**old, **{k: v for k, v in new.items() if k not in ("designed", "left")}}
    for kind in fresh.KINDS:
        earlier = [e for e in old.get(kind) or [] if e["element"] not in done]
        # Elements no longer in the section drop out (a sheet pile wall need not be one).
        earlier = [e for e in earlier if e["element"] in kept or kind == "sheet_pile_walls"]
        out[kind] = earlier + new.get(kind, [])
    multiplier = "Load multiplier sheets not in the workbook"
    out["skipped"] = [
        m for m in old.get("skipped") or [] if m.split(":")[0] not in done and not m.startswith(multiplier)
    ] + new.get("skipped", [])
    return out


@app.post(SECTION + "/design")
def design_section(project_id: str, section_id: str, body: DesignRequest | None = None) -> dict:
    """Design a section's elements (all, or the ones asked for); the others keep their results. The
    model is locked from here until it is unlocked to edit."""
    body = body or DesignRequest()
    project = _get(project_id)
    section = _section(project, section_id)
    workbook = _workbook(project_id, section)
    if workbook is None:
        raise HTTPException(409, "Upload this section's workbook on the Workbook tab first.")
    only = None if body.elements is None else [n for n in body.elements if n]
    deadline = time.monotonic() + body.budget_s if body.budget_s else None
    with _Progress(f"design-{project_id}-{section_id}") as tell:
        new = run_section(
            project.design,
            section,
            workbook,
            tell,
            only=only,
            deadline=deadline,
            approach=project.approach,
            furniture_at=furniture_mod.positions_for(project, section),
        )
        every = fresh.names(project, section)
        summary = store().workbook_summary(project_id, section_id)
        now = fresh.fingerprint(project, section, summary)
        old = store().load_results(project_id, section_id)
        handled = new["designed"]
        if only is None and not new["left"]:
            old = None  # everything designed again: nothing earlier stays
        results = _merge(old, new, handled, section, every)
        spws = {e["element"] for e in results["sheet_pile_walls"]}
        earlier = (fresh._by_element(old, every) or {}) if old else {}
        results["element_inputs"] = {
            **{k: v for k, v in earlier.items() if k in every or k in spws},
            **fresh.element_inputs(now, every, handled),
        }
        results["inputs"] = now
        tell(0.97, "Saving")
        store().save_results(project_id, section_id, results)
    if not project.locked:
        project.locked = True
        store().save(project)
    changed, stale = fresh.status(results, now, every)
    return {
        **results,
        "changed": changed,
        "stale": stale,
        "designed": handled,
        "left": new["left"],
        "locked": True,
    }


def _results(project_id: str, section_id: str) -> tuple[Project, Section, dict]:
    """A designed section's results, with what changed in its inputs since the design."""
    project = _get(project_id)
    section = _section(project, section_id)
    results = store().load_results(project_id, section_id)
    if results is None:
        raise HTTPException(404, "This section has not been designed yet.")
    summary = store().workbook_summary(project_id, section_id)
    return project, section, fresh.with_status(project, section, results, summary)


@app.get(SECTION + "/design")
def section_results(project_id: str, section_id: str) -> dict:
    return _results(project_id, section_id)[2]


def _picked(section: Section, results: dict, elements: str | None) -> tuple[Section, dict, str]:
    """Only the elements picked for an export (comma list; empty = all), with a file name suffix."""
    names = [n.strip() for n in (elements or "").split(",") if n.strip()]
    if not names:
        return section, results, ""
    unknown = [n for n in names if n not in section.elements and n != fresh.APPROACH]
    if unknown:
        raise HTTPException(404, f"No element named {', '.join(unknown)} in {section.name}.")
    keep = set(names)
    out = {
        k: [x for x in v if not (isinstance(x, dict) and "element" in x) or x["element"] in keep]
        if isinstance(v, list)
        else v
        for k, v in results.items()
    }
    if isinstance(results.get("element_inputs"), dict):
        out["element_inputs"] = {n: v for n, v in results["element_inputs"].items() if n in keep}
    picked = section.model_copy(update={"elements": {n: e for n, e in section.elements.items() if n in keep}})
    return picked, out, " " + " ".join(names) if len(names) <= 3 else f" {len(names)} elements"


def _file_name(*parts: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", " ".join(parts)).strip("_") or "project"


@app.get(SECTION + "/design/cages.json")
def pile_cage_export(project_id: str, section_id: str, elements: str | None = None) -> JSONResponse:
    """Bar runs of every designed pile and combi wall infill of a section, for a Revit / Dynamo script."""
    project = _get(project_id)
    section = _section(project, section_id)
    results = store().load_results(project_id, section_id)
    if results is None:
        raise HTTPException(404, "This section has not been designed yet.")
    _, results, suffix = _picked(section, results, elements)
    name = _file_name(project.info.name, section.name + suffix)
    return JSONResponse(
        pile_cages(project.info.name, results, section=section.name),
        headers={"Content-Disposition": f'attachment; filename="{name}-cages.json"'},
    )


def _drawings(
    project_id: str, section_id: str, element: list[str] | None, elements: str | None = None
) -> tuple[str, dict]:
    project = _get(project_id)
    section = _section(project, section_id)
    results = store().load_results(project_id, section_id)
    if results is None:
        raise HTTPException(404, "This section has not been designed yet.")
    # ?element=A&element=B (the drawings thread's form) and ?elements=A,B (every export) both pick.
    picked = ",".join([e for e in element or [] if e] + ([elements] if elements else []))
    _, results, suffix = _picked(section, results, picked)
    data = drawings.drawings(project.info.name, results, project.drawings, section.name, None)
    furn = _furniture_saved(project_id, section_id) if not suffix else None
    if furn and furn.get("use", True):
        data["views"] += furniture_report.views(furn)
        data["layers"] = {**furniture_report.LAYERS, **data["layers"]}
    if not data["views"]:
        raise HTTPException(
            404,
            "Nothing to draw: no designed pile, combi wall, beam or slab"
            + (" among the picked elements." if suffix else "."),
        )
    name = drawings.safe_name(" ".join(x for x in (project.info.name, section.name + suffix) if x)).replace(
        " ", "_"
    )
    return name, data


@app.get(SECTION + "/design/drawings.crm")
@app.get(SECTION + "/design/drawings.json")  # the name before .crm
def drawings_for_revit(
    project_id: str,
    section_id: str,
    element: Annotated[list[str] | None, Query()] = None,
    elements: str | None = None,
) -> JSONResponse:
    """Reinforcement drawings as 2D lines (triton.drawings/1, JSON in a .crm file), for the Revit add-in."""
    name, data = _drawings(project_id, section_id, element, elements)
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="{name}-drawings.crm"'})


@app.get(SECTION + "/design/drawings.dxf")
def drawings_for_autocad(
    project_id: str,
    section_id: str,
    element: Annotated[list[str] | None, Query()] = None,
    elements: str | None = None,
) -> Response:
    """The same drawings as an AutoCAD DXF, one layer per bar size."""
    name, data = _drawings(project_id, section_id, element, elements)
    return Response(
        dxf.to_dxf(data),
        media_type="application/dxf",
        headers={"Content-Disposition": f'attachment; filename="{name}.dxf"'},
    )


@app.get("/api/revit/triton-drawings.dyn")
def revit_dynamo_graph(engine: str = "CPython3") -> Response:
    """The Dynamo graph that draws a drawings file in Revit (download once)."""
    if engine not in revit.ENGINES:
        raise HTTPException(400, f"engine must be one of {', '.join(revit.ENGINES)}")
    suffix = "" if engine == "CPython3" else "-ironpython"
    return Response(
        revit.dynamo_graph(engine),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="Triton-drawings{suffix}.dyn"'},
    )


@app.get("/api/revit/triton-draw-bars.txt")
def revit_devkit_code() -> Response:
    """C# statements to paste into a DevKit code runner in Revit: picks a .crm file and draws it."""
    return Response(
        revit.devkit_code(),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="TritonDrawBars.txt"'},
    )


@app.get("/api/revit/triton-addin.zip")
def revit_addin() -> Response:
    """The Revit add-in (C# source, build once in Visual Studio): a Triton button that draws the file."""
    return Response(
        revit.addin_zip(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="TritonDrawings-addin.zip"'},
    )


@app.get("/api/revit/triton_revit.py")
def revit_script() -> Response:
    """The same script as a plain Python file, for pyRevit or RevitPythonShell."""
    return Response(
        revit.script(),
        media_type="text/x-python",
        headers={"Content-Disposition": 'attachment; filename="triton_revit.py"'},
    )


@app.get(SECTION + "/design/governing.xlsx")
def governing_sets_export(project_id: str, section_id: str, elements: str | None = None) -> Response:
    """Governing sets for AdSec: 7 QP + 7 ULS per concrete station, 10 ULS rows per steel element."""
    project = _get(project_id)
    section = _section(project, section_id)
    results = store().load_results(project_id, section_id)
    if results is None:
        raise HTTPException(404, "This section has not been designed yet.")
    _, results, suffix = _picked(section, results, elements)
    name = _file_name(project.info.name, section.name + suffix)
    return Response(
        governing_workbook(project.info.name, section.name, results),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}-governing-sets.xlsx"'},
    )


@app.get(SECTION + "/design/adsec.zip")
def adsec_export(project_id: str, section_id: str, elements: str | None = None) -> Response:
    """AdSec 8.3 files (.ads): pile and combi infill parts, beams and slab strips, with QP and ULS loads."""
    project = _get(project_id)
    section = _section(project, section_id)
    results = store().load_results(project_id, section_id)
    if results is None:
        raise HTTPException(404, "This section has not been designed yet.")
    section, results, suffix = _picked(section, results, elements)
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
        raise HTTPException(
            404,
            "Nothing designed "
            + ("among the picked elements" if suffix else "in this section")
            + " has AdSec files.",
        )
    name = _file_name(project.info.name, section.name + suffix)
    return Response(
        adsec.zip_files(files),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}-adsec.zip"'},
    )


@app.get("/api/projects/{project_id}/costing")
def project_costing(project_id: str) -> dict:
    """Quantities and cost of every designed section along its berth, for the Costing tab."""
    project = _get(project_id)
    results = {}
    for s in project.sections:
        res = store().load_results(project_id, s.id)
        if res is not None:
            res = fresh.with_status(project, s, res, store().workbook_summary(project_id, s.id))
        results[s.id] = res
    counts = {
        s.id: furniture_mod.counts_for_costing(_furniture_saved(project_id, s.id)) for s in project.sections
    }
    return cost_project(project, results, counts)


class TrialRequest(BaseModel):
    element: str
    sizes: list[dict[str, float | None]] = Field(
        default_factory=list,
        description="Sizes in mm: thickness (slab), diameter (pile), width and depth (beam).",
    )
    budget_s: float | None = Field(
        None, gt=0, description="Start no trial after this long; the rest come back in 'left'."
    )


class TrialPick(BaseModel):
    element: str
    size: dict[str, float | None]


def _trial_element(section: Section, name: str):
    element = section.elements.get(name)
    if element is None or trials.kind_of(element) is None:
        raise HTTPException(404, f"{name} is not a slab, beam or pile of this section.")
    return element


def _trial_sizes(element, sizes: list[dict]) -> list[dict[str, float]]:
    try:
        out = [trials.clean_size(element, s) for s in sizes]
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    unique = {trials.size_key(s): s for s in out}
    if not unique:
        raise HTTPException(422, "List at least one size to try.")
    if len(unique) > 12:
        raise HTTPException(422, "Try at most 12 sizes at a time.")
    return list(unique.values())


@app.get(SECTION + "/trials")
def section_trials(project_id: str, section_id: str) -> dict:
    """The Comparisons tab: each slab, beam and pile with the sizes to try and the trials run so far."""
    project = _get(project_id)
    section = _section(project, section_id)
    d = store()._dir(project_id, section_id)
    summary = store().workbook_summary(project_id, section_id)
    return trials.view(project, section, summary, store().load_results(project_id, section_id), d)


@app.post(SECTION + "/trials")
def run_trials(project_id: str, section_id: str, body: TrialRequest) -> dict:
    """Design an element at each size asked for (the ones not designed yet from the same inputs). The
    section and its results do not change."""
    project = _get(project_id)
    section = _section(project, section_id)
    element = _trial_element(section, body.element)
    sizes = _trial_sizes(element, body.sizes)
    workbook = _workbook(project_id, section)
    if workbook is None:
        raise HTTPException(409, "Upload this section's workbook on the Workbook tab first.")
    deadline = time.monotonic() + body.budget_s if body.budget_s else None
    d = store()._dir(project_id, section_id)
    summary = store().workbook_summary(project_id, section_id)
    with _Progress(f"trials-{project_id}-{section_id}") as tell:
        done = trials.run(project, section, workbook, summary, d, body.element, sizes, deadline, tell)
    view = trials.view(project, section, summary, store().load_results(project_id, section_id), d)
    return {**view, "done": done["done"], "left": done["left"]}


@app.post(SECTION + "/trials/use")
def use_trial(project_id: str, section_id: str, body: TrialPick) -> dict:
    """Give the element the trial's size and take the trial's design as its result. The bars set by
    hand for its old size go, as the trial picked its own."""
    project = _get(project_id)
    section = _section(project, section_id)
    element = _trial_element(section, body.element)
    size = _trial_sizes(element, [body.size])[0]
    summary = store().workbook_summary(project_id, section_id)
    d = store()._dir(project_id, section_id)
    trial = trials.trial_section(section, body.element, size)
    key = trials.run_key(project, trial, body.element, summary)
    design = trials.load_design(d, key)
    if design is None:
        raise HTTPException(409, "Run this trial again first: its inputs changed since it ran.")
    project.sections = [trial if s.id == section_id else s for s in project.sections]
    kind = trials.kind_of(element)
    old = store().load_results(project_id, section_id)
    results = _merge(old, {kind: [design]}, [body.element], trial, fresh.names(project, trial))
    now = fresh.fingerprint(project, trial, summary)
    earlier = (fresh._by_element(old, fresh.names(project, trial)) or {}) if old else {}
    results["element_inputs"] = {**earlier, **fresh.element_inputs(now, trial.elements, [body.element])}
    results["inputs"] = now
    if not results.get("run_at"):
        results["run_at"] = design.get("run_at") or _now()
    store().save_results(project_id, section_id, results)
    project.locked = True
    saved = store().save(project)
    # Elements designed with this one's size: piles carry the beams and the slab (supports, punching),
    # the beams and the slab frame into each other.
    others = {"piles": ("beams", "slabs"), "beams": ("slabs",), "slabs": ("beams",)}[kind]
    affected = [
        n
        for n, e in trial.elements.items()
        if n != body.element
        and trials.kind_of(e) in others
        and any(r["element"] == n for r in results.get(trials.kind_of(e)) or [])
    ]
    return {"project": saved, "affected": affected}


class ScenarioRequest(BaseModel):
    variants: list[dict] = Field(
        default_factory=list, description="Changes to try on every element, e.g. {crack_width_limit: 0.3}."
    )
    budget_s: float | None = Field(
        None, gt=0, description="Start no element after this long; what is left comes back in 'left'."
    )


def _scenarios(project_id: str, section_id: str, which: str) -> dict:
    project = _get(project_id)
    section = _section(project, section_id)
    d = store()._dir(project_id, section_id)
    summary = store().workbook_summary(project_id, section_id)
    results = store().load_results(project_id, section_id)
    out = trials.scenarios_view(project, section, summary, results, d, which)
    if which == "ve":
        out["ideas"] = trials.ideas(section, results, project.approach)
    return out


def _run_scenarios(project_id: str, section_id: str, body: ScenarioRequest, which: str) -> dict:
    project = _get(project_id)
    section = _section(project, section_id)
    try:
        variants = [trials.clean_variant(v, section) for v in body.variants]
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    if len({trials.variant_key(v) for v in variants if trials.change_of(v)}) > 12:
        raise HTTPException(422, "Try at most 12 changes at a time.")
    workbook = _workbook(project_id, section)
    if workbook is None:
        raise HTTPException(409, "Upload this section's workbook on the Workbook tab first.")
    deadline = time.monotonic() + body.budget_s if body.budget_s else None
    d = store()._dir(project_id, section_id)
    summary = store().workbook_summary(project_id, section_id)
    with _Progress(f"trials-{project_id}-{section_id}") as tell:
        done = trials.run_scenarios(project, section, workbook, summary, d, variants, deadline, tell, which)
    return {**_scenarios(project_id, section_id, which), **done}


@app.get(SECTION + "/scenarios")
def section_scenarios(project_id: str, section_id: str) -> dict:
    """The whole section designed with a change on every element, against the section as set."""
    return _scenarios(project_id, section_id, "scenarios")


@app.post(SECTION + "/scenarios")
def run_scenarios(project_id: str, section_id: str, body: ScenarioRequest) -> dict:
    """Design every element for the section as set and for each variant, in steps. The section and its
    results do not change."""
    return _run_scenarios(project_id, section_id, body, "scenarios")


@app.get(SECTION + "/value-engineering")
def value_engineering(project_id: str, section_id: str) -> dict:
    """The value engineering ideas Triton can cost for this section, and the mixes run so far."""
    return _scenarios(project_id, section_id, "ve")


@app.post(SECTION + "/value-engineering")
def run_value_engineering(project_id: str, section_id: str, body: ScenarioRequest) -> dict:
    """Design the whole section for each idea or mix of ideas, in steps, and cost it against the
    section as set. The section and its results do not change."""
    return _run_scenarios(project_id, section_id, body, "ve")


@app.get(SECTION + "/design/report.{fmt}")
def design_report(
    project_id: str, section_id: str, fmt: str, detail: str = "summary", elements: str | None = None
) -> Response:
    """The calculation report of a designed section: summary or detailed, as Word, PDF or Excel."""
    if fmt not in RENDERERS:
        raise HTTPException(404, "Reports are Word (.docx), PDF (.pdf) or Excel (.xlsx).")
    if detail not in ("summary", "detailed"):
        raise HTTPException(422, "detail is 'summary' or 'detailed'.")
    project, section, results = _results(project_id, section_id)
    section, results, suffix = _picked(section, results, elements)
    rep = build_report(project, section, results, detail)
    name = _file_name(project.info.name, section.name + suffix, detail)
    render, media = RENDERERS[fmt]
    return Response(
        render(rep),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}.{fmt}"'},
    )


# --- Clashes -------------------------------------------------------------------------------------------

_CLASHES: dict[tuple, Clashes] = {}


def _clashes(project_id: str, section_id: str) -> tuple[Project, Section, Clashes]:
    """The section's pile heads and bars round them, kept while the design, the design settings and the
    clash rule stay the same (what-ifs and choices do not count)."""
    project, section, results = _results(project_id, section_id)
    rule = section.clashes.model_dump(mode="json", exclude={"whatifs", "choices"})
    key = (
        project_id,
        section_id,
        results.get("run_at"),
        hashlib.sha1(
            json.dumps([rule, project.design.model_dump(mode="json")], sort_keys=True, default=str).encode()
        ).hexdigest(),
    )
    c = _CLASHES.get(key)
    if c is None:
        for k in [k for k in _CLASHES if k[:2] == (project_id, section_id)]:
            del _CLASHES[k]
        c = _CLASHES[key] = Clashes(project, section, results)
    c.ctx.rule = section.clashes
    return project, section, c


@app.get(SECTION + "/clashes")
def section_clashes(project_id: str, section_id: str) -> dict:
    """The Clashes tab: every pile head's clashes by pile and element, solutions and the what-ifs kept."""
    project, section, c = _clashes(project_id, section_id)
    return find_clashes(project, section, c.ctx.results, c=c)


@app.get(SECTION + "/clashes/head")
def clash_head(project_id: str, section_id: str, group: str, index: int = 0) -> dict:
    """One pile head: its drawing (plan, section, 3D), what clashes, the solutions and the punching links."""
    _, _, c = _clashes(project_id, section_id)
    head = c.head(group, index)
    if head is None:
        raise HTTPException(404, "No such pile head.")
    return _clean(c.entry(head))


class ClashSettingsIn(BaseModel):
    rule: Literal["touch", "ec2"] | None = None
    fixing_tolerance: float | None = Field(None, ge=0, le=50)
    plate_level: Literal["mid", "top"] | None = None
    top_levels: dict[str, float] | None = None
    mesh_start: dict[str, float] | None = None
    choices: dict[str, str] | None = None


@app.put(SECTION + "/clashes/settings")
def save_clash_settings(project_id: str, section_id: str, body: ClashSettingsIn) -> dict:
    """The clash rule and the solutions chosen. Saved even while the project is locked: they never
    change the design."""
    project = _get(project_id)
    section = _section(project, section_id)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(section.clashes, k, v)
    store().save(project)
    return section.clashes.model_dump(mode="json")


@app.post(SECTION + "/clashes/whatif")
def check_whatif(project_id: str, section_id: str, body: WhatIf) -> dict:
    """Bars taken out at a connection, checked again; nothing is saved and the design is unchanged."""
    _, _, c = _clashes(project_id, section_id)
    return c.whatif(body)


@app.post(SECTION + "/clashes/whatifs")
def keep_whatif(project_id: str, section_id: str, body: WhatIf) -> dict:
    """Keep a what-if with the section (replacing one of the same id)."""
    project = _get(project_id)
    section = _section(project, section_id)
    section.clashes.whatifs = [w for w in section.clashes.whatifs if w.id != body.id] + [body]
    store().save(project)
    _, _, c = _clashes(project_id, section_id)
    return c.whatif(body)


@app.delete(SECTION + "/clashes/whatifs/{whatif_id}", status_code=204)
def drop_whatif(project_id: str, section_id: str, whatif_id: str) -> Response:
    project = _get(project_id)
    section = _section(project, section_id)
    section.clashes.whatifs = [w for w in section.clashes.whatifs if w.id != whatif_id]
    store().save(project)
    return Response(status_code=204)


@app.get(SECTION + "/clashes/calc.{fmt}")
def clash_calc(
    project_id: str, section_id: str, fmt: str, group: str = "", index: int = 0, whatif: str = ""
) -> Response:
    """The calculation of one pile head's clashes and solutions, or of a kept what-if, as Word, PDF
    or Excel."""
    if fmt not in RENDERERS:
        raise HTTPException(404, "Calculations are Word (.docx), PDF (.pdf) or Excel (.xlsx).")
    project, section, c = _clashes(project_id, section_id)
    w = None
    if whatif:
        kept = next((x for x in section.clashes.whatifs if x.id == whatif), None)
        if kept is None:
            raise HTTPException(404, "That what-if is not kept any more.")
        group, index = kept.group, kept.head
        w = c.whatif(kept)
    head = c.head(group, index)
    if head is None:
        raise HTTPException(404, "No such pile head.")
    entry = _clean(c.entry(head))
    notes = c.notes + assumptions(c.ctx.rule, c.ctx.settings)
    rep = clash_report.build_calc(project, section, entry, notes, w)
    render, media = RENDERERS[fmt]
    name = clash_report.filename(project, section, entry, w)
    return Response(
        render(rep), media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}.{fmt}"'}
    )


@app.get(SECTION + "/geometry")
def geometry(project_id: str, section_id: str) -> dict:
    """Element geometry for the 3D view, with the directions found in the workbook check."""
    section = _section(_get(project_id), section_id)
    wb = _workbook(project_id, section)
    if wb is None:
        raise HTTPException(409, "Upload this section's workbook on the Workbook tab first.")
    sheets = factored_elements(section, wb)
    parts, alignment = section_alignment(section, sheets)
    axes = {a["element"]: a.get("local") for a in getattr(wb, "axes", None) or []}
    elements = plan_geometry(section_geometry(wb), sheets, parts, axes)
    return {"elements": elements, "axes": wb.summary()["axes"], "alignment": alignment}


@app.get(SECTION + "/joints")
def expansion_joints(project_id: str, section_id: str) -> dict:
    """Where the expansion joints go along the section's berth (Design settings' rules)."""
    project = _get(project_id)
    section = _section(project, section_id)
    wb = _workbook(project_id, section)
    raw, parts = {}, []
    if wb is not None:
        sheets = factored_elements(section, wb)
        parts, _ = section_alignment(section, sheets)
        raw = wb.elements()
    return section_joints(
        project.design,
        section,
        raw,
        parts,
        along_axis(section),
        furniture_mod.positions_for(project, section),
    )


@app.get(SECTION + "/joints.dxf")
def expansion_joints_dxf(project_id: str, section_id: str) -> Response:
    """The expansion joint layout as an AutoCAD DXF: the berth laid out straight, 1:1 in mm."""
    project = _get(project_id)
    section = _section(project, section_id)
    layout = expansion_joints(project_id, section_id)
    if not layout.get("segments"):
        raise HTTPException(409, layout.get("text") or "No joint layout yet.")
    title = f"{project.info.name} - {section.name} - expansion joints"
    name = f"{project.info.name} {section.name} expansion joints".replace('"', "")
    return Response(
        dxf.to_dxf(joints_drawing(layout, project.drawings, title)),
        media_type="application/dxf",
        headers={"Content-Disposition": f'attachment; filename="{name}.dxf"'},
    )


def _king_piles(section: Section, wb) -> list[tuple[float, float, float]]:
    """Plan position and radius (m) of every combi wall king pile in the section's workbook."""
    out = []
    for g in section_geometry(wb):
        el = section.elements.get(g["element"])
        if isinstance(el, CombiWallInput):
            out += [(x, y, el.tube_diameter / 2000) for x, y, *_ in g.get("lines") or []]
    return out


@app.get(SECTION + "/spw.xlsx")
def spw_export(project_id: str, section_id: str) -> Response:
    """Sheet pile wall straining actions (Plaxis sign, multipliers applied) for the sheet pile program."""
    project = _get(project_id)
    section = _section(project, section_id)
    wb = _workbook(project_id, section)
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
        spw_workbook(
            project.info.name,
            section.name,
            spw[0],
            elements[spw[0]],
            wall if isinstance(wall := section.elements.get(spw[0]), SheetPileInput) else None,
            _king_piles(section, wb),
        ),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}-straining-actions.xlsx"'},
    )


# --- Quay furniture ------------------------------------------------------------------------------------


def _furniture_saved(project_id: str, section_id: str) -> dict | None:
    """The section's furniture as last worked out (for Costing and the drawings)."""
    path = store()._dir(project_id, section_id) / "furniture.json"
    try:
        return json.loads(path.read_text("utf-8")) if path.exists() else None
    except (OSError, ValueError):
        return None


def _berth_geometry(project_id: str, section: Section) -> list[dict]:
    """Element positions from the workbook, kept beside it until the next upload (reading the
    workbook takes a few seconds)."""
    summary = store().workbook_summary(project_id, section.id) or {}
    key = [summary.get("version"), summary.get("uploaded_at")]
    path = store()._dir(project_id, section.id) / "furniture_geometry.json"
    try:
        kept = json.loads(path.read_text("utf-8"))
        if kept.get("key") == key:
            return kept["geometry"]
    except (OSError, ValueError, AttributeError):
        pass
    wb = _workbook(project_id, section)
    if wb is None:
        raise HTTPException(
            409,
            "Upload this section's workbook on the Workbook tab first: "
            "the furniture is laid out on its beams and piles.",
        )
    geometry = section_geometry(wb)
    store()._write_json(path, {"key": key, "geometry": geometry})
    return geometry


def _joints_for_furniture(project_id: str, project: Project, section: Section) -> dict | None:
    """The section's expansion joint layout, kept until its inputs change (it reads the workbook)."""
    summary = store().workbook_summary(project_id, section.id) or {}
    key = hashlib.sha1(
        json.dumps(
            [
                summary.get("version"),
                summary.get("uploaded_at"),
                project.design.joints.model_dump(mode="json"),
                section.joints.model_dump(mode="json"),
                section.costing.model_dump(mode="json"),
                section.alignment.model_dump(mode="json"),
                project.furniture.model_dump(mode="json"),
                section.furniture.model_dump(mode="json"),
            ],
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()
    path = store()._dir(project_id, section.id) / "furniture_joints.json"
    try:
        kept = json.loads(path.read_text("utf-8"))
        if kept.get("key") == key:
            return kept["layout"]
    except (OSError, ValueError, AttributeError):
        pass
    try:
        layout = expansion_joints(project_id, section.id)
    except HTTPException:
        return None
    store()._write_json(path, {"key": key, "layout": layout})
    return layout


def _furniture(project_id: str, section_id: str) -> tuple[Project, Section, dict]:
    project = _get(project_id)
    section = _section(project, section_id)
    geometry = _berth_geometry(project_id, section)
    joints = _joints_for_furniture(project_id, project, section)
    try:
        res = furniture_mod.design(project, section, geometry, joints)
    except ValueError as e:
        raise HTTPException(409, str(e)) from None
    store()._write_json(store()._dir(project_id, section_id) / "furniture.json", res)
    return project, section, res


@app.get(SECTION + "/furniture")
def section_furniture(project_id: str, section_id: str) -> dict:
    """The Furniture tab: the project's furniture arranged along this section's berth, and designed."""
    return _furniture(project_id, section_id)[2]


@app.get(SECTION + "/furniture/calc.{fmt}")
def furniture_calc(project_id: str, section_id: str, fmt: str) -> Response:
    """The furniture calculation as Word, PDF or Excel."""
    if fmt not in RENDERERS:
        raise HTTPException(404, "Calculations are Word (.docx), PDF (.pdf) or Excel (.xlsx).")
    project, section, res = _furniture(project_id, section_id)
    render, media = RENDERERS[fmt]
    name = drawings.safe_name(f"{project.info.name} {section.name} quay furniture").replace(" ", "_")
    return Response(
        render(furniture_report.build_calc(project, section, res)),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}.{fmt}"'},
    )


@app.get(SECTION + "/furniture/plan.{fmt}")
def furniture_plan(project_id: str, section_id: str, fmt: str) -> Response:
    """The berth's furniture plan and bolt patterns as an AutoCAD DXF, a drawings file (.crm) or a PNG."""
    project, section, res = _furniture(project_id, section_id)
    name = drawings.safe_name(f"{project.info.name} {section.name} furniture").replace(" ", "_")
    if fmt == "png":
        return Response(furniture_report.plan_png(res), media_type="image/png")
    data = {
        "format": drawings.FORMAT,
        "project": project.info.name,
        "section": section.name,
        "units": "mm",
        "view_prefix": project.drawings.revit_view_prefix,
        "layers": furniture_report.LAYERS,
        "views": furniture_report.views(res),
    }
    if fmt == "dxf":
        return Response(
            dxf.to_dxf(data),
            media_type="application/dxf",
            headers={"Content-Disposition": f'attachment; filename="{name}.dxf"'},
        )
    if fmt == "crm":
        return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="{name}.crm"'})
    raise HTTPException(404, "The plan is .dxf, .crm or .png.")
