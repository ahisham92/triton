"""Results films: a short animated summary of a SAP2000 analysis for the client.

The SAP2000 file itself is read in the browser (static/sapread.js), since a results export runs to
hundreds of megabytes; the site keeps only its compact summary and the film's settings, one folder
per film under the project: ``projects/<project>/films/<film>/{film.json, summary.json.gz}``.
Never a design input.
"""

from __future__ import annotations

import gzip
import json
import math
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import atomic, sap, sap_demo
from .project import _now
from .store import ProjectNotFound, ProjectStore

router = APIRouter()
MAX_SUMMARY = 400 * 1024 * 1024  # a summary this large unpacked is not one this page made


class Rock(BaseModel):
    name: str = "Breakwater"
    kind: Literal["breakwater", "revetment"] = Field(
        "breakwater", description="A revetment slopes one way only, down to the seabed on the sea side"
    )
    pts: list[list[float]] = Field(default_factory=list, description="[lat, lon] along its crest")
    xy: list[list[float]] = Field(
        default_factory=list, description="[x, y] in the model's axes, where it has no place on the map"
    )
    crest: float = Field(5.0, description="Crest level, m")
    width: float = Field(12.0, description="Crest width, m")
    fromTrito: bool = Field(False, description="Taken from a TritoMoor project's site")


class Land(BaseModel):
    pts: list[list[float]] = Field(default_factory=list)
    xy: list[list[float]] = Field(default_factory=list)
    level: float | None = Field(None, description="Ground level, m; empty: the top of the model")
    open: bool = Field(False, description="A strip along a coastline: its back edge has no slope")
    fromTrito: bool = False


class PileGroup(BaseModel):
    name: str = "Piles"
    match: str = Field(
        "",
        description="Which piles: frame names or section names, comma separated, * for any characters "
        "(e.g. 'EX-*, 3-*'), or rows ('Row D'); empty: every pile not in another group",
    )
    compression: float | None = Field(None, ge=0, description="Vertical capacity in compression")
    tension: float | None = Field(
        None, ge=0, description="Capacity in tension; empty: tension_factor x compression"
    )
    tension_factor: float = Field(0.7, ge=0)
    lateral: float | None = Field(None, ge=0, description="Lateral capacity")
    cases: str = Field(
        "",
        description="Combinations the capacities are checked under, comma separated, * for any "
        "characters (e.g. 'C1-*' for D+L service); empty: every combination",
    )
    lateral_cases: str = Field("", description="Combinations for the lateral check; empty: the same as cases")
    colour: str = ""


class FilmSettings(BaseModel):
    name: str = "Results film"
    title: str = ""
    client: str = ""
    lat: float | None = Field(
        None, ge=-90, le=90, description="Where the model's origin (0, 0) is on the map"
    )
    lon: float | None = Field(None, ge=-180, le=180)
    bearing: float = Field(0.0, description="Direction of the model's +X axis, degrees clockwise from north")
    water_level: float = 0.0
    seabed: float | None = Field(
        None, description="Seabed level, m; empty: two thirds of the way down the model"
    )
    breakwaters: list[Rock] = Field(default_factory=list)
    land: list[Land] = Field(default_factory=list)
    mode: Literal["detailed", "brief"] = "detailed"
    size: Literal["1280x720", "1920x1080"] = "1280x720"
    exaggerate: float | None = Field(
        None, gt=0, description="Movements drawn this many times larger; empty: chosen"
    )
    steel_fy: float = Field(355.0, gt=0, description="Steel yield strength for stress ratios, MPa")
    hide: list[str] = Field(
        default_factory=list,
        description="Shots left out: loads, reactions, movements, quake, piles, forces, design",
    )
    piles: list[PileGroup] = Field(
        default_factory=list, description="Pile groups (existing, new...) and capacities"
    )
    capacity_unit: Literal["kN", "t"] = "t"
    tritomoor: dict | None = Field(
        None, description="Where the site came from a TritoMoor file: x0 (m), sea ('-Y' or '+Y'), name"
    )
    pile_rows: str = Field(
        "",
        description="Names of the pile rows from the low side, comma separated (e.g. 'C, B, A, D'); "
        "empty: A, B, C...",
    )


class Film(BaseModel):
    id: str
    file: str = ""
    created: str = ""
    settings: FilmSettings = Field(default_factory=FilmSettings)
    counts: dict = Field(default_factory=dict)
    sample: bool = False


def _root(project_id: str) -> Path:
    s = ProjectStore()
    try:
        path = s._path(project_id)
    except ProjectNotFound as e:
        raise HTTPException(404, "No such project.") from e
    if not path.exists():
        raise HTTPException(404, "No such project.")
    return s.root / project_id / "films"


def _dir(project_id: str, film_id: str) -> Path:
    if not film_id.isalnum():
        raise HTTPException(404, "No such film.")
    d = _root(project_id) / film_id
    if not (d / "film.json").exists():
        raise HTTPException(404, "No such film.")
    return d


def _read(d: Path) -> Film:
    return Film.model_validate_json((d / "film.json").read_text("utf-8"))


def _write(d: Path, film: Film) -> Film:
    atomic.write_text(d / "film.json", film.model_dump_json(indent=1))
    return film


def _site(project_id: str) -> tuple[float | None, float | None, str]:
    p = ProjectStore().get(project_id)
    for s in p.info.sites:
        if s.lat is not None and s.lon is not None:
            return s.lat, s.lon, p.info.client
    return None, None, p.info.client


def _new(project_id: str, summary_gz: bytes, file: str, settings: FilmSettings, sample: bool = False) -> Film:
    data = json.loads(gzip.decompress(summary_gz))
    film = Film(
        id=uuid.uuid4().hex[:12],
        file=file,
        created=_now(),
        settings=settings,
        counts=sap.counts(data),
        sample=sample,
    )
    d = _root(project_id) / film.id
    d.mkdir(parents=True, exist_ok=True)
    tmp = atomic.tmp_for(d / "summary.json.gz")
    tmp.write_bytes(summary_gz)
    tmp.replace(d / "summary.json.gz")
    return _write(d, film)


@router.get("/api/projects/{project_id}/films")
def list_films(project_id: str) -> list[Film]:
    root = _root(project_id)
    if not root.is_dir():
        return []
    films = [_read(d) for d in root.iterdir() if d.is_dir() and (d / "film.json").exists()]
    return sorted(films, key=lambda f: f.created, reverse=True)


@router.post("/api/projects/{project_id}/films", status_code=201)
async def add_film(project_id: str, request: Request, file: str = "", name: str = "") -> Film:
    """Keep a film's summary, sent gzipped as the page read it from the SAP2000 file."""
    _root(project_id)
    body = await request.body()
    try:
        raw = gzip.decompress(body) if body[:2] == b"\x1f\x8b" else body
        if len(raw) > MAX_SUMMARY:
            raise ValueError("too large")
        data = json.loads(raw)
        sap.counts(data)
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise HTTPException(400, f"That is not a results summary this page made ({e}).") from e
    lat, lon, client = _site(project_id)
    stem = Path(file).stem or "SAP2000 results"
    settings = FilmSettings(
        name=name or stem, title=name or stem, client=client, lat=lat, lon=lon, seabed=_guess_seabed(data)
    )
    return _new(project_id, sap.gz_json(data), file, settings)


def _guess_seabed(data: dict) -> float:
    lo, hi = data["bbox"][0][2], data["bbox"][1][2]
    if lo is None or hi is None:
        return -10.0
    return round(hi - (hi - lo) * 0.55, 1) if hi - lo > 4 else round(lo - 5, 1)


def _to_latlon(xy: list[float], lat0: float, lon0: float, bearing: float) -> list[float]:
    b = math.radians(bearing)
    x, y = xy
    east = x * math.sin(b) - y * math.cos(b)
    north = x * math.cos(b) + y * math.sin(b)
    return [
        round(lat0 + north / 110540.0, 7),
        round(lon0 + east / (111320.0 * math.cos(math.radians(lat0))), 7),
    ]


@router.post("/api/projects/{project_id}/films/sample", status_code=201)
def sample_film(project_id: str) -> Film:
    """A film of the made-up jetty in sap_demo, to see what a film looks like."""
    _root(project_id)
    lat, lon, client = _site(project_id)
    with tempfile.TemporaryDirectory() as tmp:
        path = sap_demo.write(Path(tmp) / "sample.s2k")
        work = Path(tmp) / "work"
        work.mkdir()
        data = sap.read_all(path, work).summary()
    s = sap_demo.SURROUNDINGS
    place = (lambda q: _to_latlon(q, lat, lon, 0.0)) if lat is not None else None
    rocks = [
        Rock(
            name=r["name"],
            crest=r["crest"],
            width=r["width"],
            **({"pts": [place(q) for q in r["pts"]]} if place else {"xy": r["pts"]}),
        )
        for r in s["breakwaters"]
    ]
    land = [
        Land(**({"pts": [place(q) for q in poly]} if place else {"xy": poly}), level=sap_demo.DECK + 0.5)
        for poly in s["land"]
    ]
    settings = FilmSettings(
        name="Sample jetty",
        title="Sample jetty: results of the analysis",
        client=client,
        lat=lat,
        lon=lon,
        seabed=sap_demo.SEABED,
        water_level=0.0,
        breakwaters=rocks,
        land=land,
    )
    return _new(project_id, sap.gz_json(data), "Sample jetty.s2k", settings, sample=True)


@router.get("/api/projects/{project_id}/films/{film_id}/summary")
def film_summary(project_id: str, film_id: str, request: Request) -> Response:
    d = _dir(project_id, film_id)
    body = (d / "summary.json.gz").read_bytes()
    if "gzip" in request.headers.get("accept-encoding", ""):
        return Response(
            body,
            media_type="application/json",
            headers={"Content-Encoding": "gzip", "Cache-Control": "no-cache"},
        )
    return Response(gzip.decompress(body), media_type="application/json")


@router.put("/api/projects/{project_id}/films/{film_id}")
def save_film(project_id: str, film_id: str, settings: FilmSettings) -> Film:
    d = _dir(project_id, film_id)
    film = _read(d)
    film.settings = settings
    return _write(d, film)


@router.delete("/api/projects/{project_id}/films/{film_id}", status_code=204)
def delete_film(project_id: str, film_id: str) -> Response:
    shutil.rmtree(_dir(project_id, film_id), ignore_errors=True)
    return Response(status_code=204)
