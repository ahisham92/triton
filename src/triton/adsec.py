"""AdSec 8.3 section files (.ads) of designed concrete sections, ready to analyse: pile and combi wall
infill parts (circles), beams and 1 m slab strips (rectangles).

An .ads file is a run of binary records, each ending with ``####@@@@``: a record type, its
name, then little-endian 32-bit integers and floats, 64-bit doubles for forces, and
zero-terminated strings; items inside a record are separated by ``####``. Triton copies the
records that do not depend on the section (program header, EC2 national parameters, units,
bar sizes and rules, rebar materials, cover) from an office file (``adsec_template``) and
writes the rest:

* titles and a one-line file history;
* the section: a circle of the pile diameter in the pile's concrete grade, with one bar
  group per ring. The outer ring is a perimeter group (bars on an arc from the first bar all
  the way round), inner rings are circle groups; every ring starts with a bar on the +y axis,
  so a half row sits behind every second outer bar, as Triton designs it;
* the loads: the station's seven QP sets then its seven ULS sets, N in N (compression +,
  the AdSec convention Triton designs with), My = M2 and Mz = M3 in N·m;
* one SLS analysis case per QP load (long-term) and one ULS case per ULS load (short-term).

Rectangles (from the office's front beam and slab strip files) are ``STD%R%depth.%width.`` with
four covers (top first) and every bar line a user line group (``GRP_LINE``, kind ``U``) between
its end bars, y across the section and z up, in m; My is the vertical bending (sagging +, as the
office beam file) and Mz the horizontal.

The layout of every generated record matches the office files byte for byte when given the
same section and loads (see ``tests/test_adsec.py``).
"""

from __future__ import annotations

import io
import math
import re
import struct
import zipfile
from datetime import datetime
from typing import Any

from . import clock
from .adsec_template import STATIC
from .alignment import named_parts

END = b"####@@@@"
SEP = b"####"

# Bytes inside the analysis case records that do not change between cases.
_SLS_MID = bytes.fromhex("000000000000803f0000803f00000000010000000000004000000000")
_ULS_MID = bytes.fromhex("000000000000803f0000803f000000000000000000000000")
_CASE_TAIL = bytes.fromhex("000000606666e63f000000606666e63f0000000000000000")


def _i(*values: int) -> bytes:
    return struct.pack(f"<{len(values)}i", *values)


def _f(*values: float) -> bytes:
    return struct.pack(f"<{len(values)}f", *values)


def _s(text: str) -> bytes:
    return text.encode("cp1252", "replace") + b"\0"


def _record(type_id: bytes, name: str, body: bytes) -> bytes:
    return type_id + _s(name) + body


def titles(job: str, title: str, subtitle: str, date: str, initials: str, heading: str) -> bytes:
    return _record(
        b"\x02\x00\x00\x00",
        "Titles",
        _i(1, 1) + b"".join(_s(t) for t in (job, title, subtitle, "", date, initials, heading)),
    )


def history(when: datetime) -> bytes:
    stamp = f"{when.hour}:{when.minute:02d}:{when.second:02d}"[:5]
    entry = (
        _s(when.strftime("%d-%b-%Y"))
        + _s(stamp)
        + _i(1)
        + _s("Triton")
        + _s("Triton")
        + _s("Written by Triton")
    )
    return _record(b"\x0a\x00\x00\x00", "File History", _i(1, 1) + entry)


def analysis(cases: int) -> bytes:
    return _record(b"\x8a\x1a\x06\x00", "Analysis", _i(2, 1, 1, 1, cases, *range(1, cases + 1)))


def _layer(bar_mm: float, count: int, grade: str, layout: str, kind: bytes, points: list[float]) -> bytes:
    return (
        b"\0"
        + _f(bar_mm / 1e3)
        + _i(0, count, 1, 0, -2)
        + _s(grade)
        + _s(layout)
        + kind
        + _f(*points)
        + _i(1, 1, 0)
        + _f(1.0)
    )


def perimeter_group(bar_mm: float, count: int, radius_mm: float, grade: str) -> bytes:
    """Bars on an arc: centre, first bar (+y axis), last bar, and a point on the arc."""
    r = radius_mm / 1e3
    step = 2 * math.pi / count
    points = [0.0, 0.0, r, 0.0, r * math.cos(step), -r * math.sin(step), -r, 0.0]
    return (
        b"\0"
        + _s("GRP_COL")
        + _i(0, -2)
        + _s(grade)
        + _i(1, 0, 1)
        + _layer(bar_mm, count, grade, "GRP_ARC", b"P", points)
    )


def circle_group(bar_mm: float, count: int, first: tuple[float, float], grade: str) -> bytes:
    """Bars round a circle about the origin through the first bar at ``first`` (y, z, m)."""
    points = [0.0, 0.0, first[0], first[1], 0.0, 0.0, 0.0, 0.0]
    return (
        b"\0"
        + _s("GRP_CIRCLE")
        + _i(0, 1, 1, 0, 1)
        + _layer(bar_mm, count, grade, "GRP_CIRCLE", b"U", points)
    )


def line_group(
    bar_mm: float, count: int, a: tuple[float, float], b: tuple[float, float], grade: str
) -> bytes:
    """``count`` bars evenly spaced from ``a`` to ``b`` (y, z in m), placed by the user."""
    return (
        b"\0"
        + _s("GRP_LINE")
        + _i(0, 1, 1, 0, 1)
        + _layer(bar_mm, count, grade, "GRP_LINE", b"U", [0.0, 0.0, a[0], a[1], b[0], b[1], 0.0, 0.0])
    )


def rect_section(
    name: str,
    depth_mm: float,
    width_mm: float,
    concrete: str,
    covers_mm: tuple[float, float, float, float],
    link_mm: float,
    groups: list[bytes],
) -> bytes:
    body = (
        _i(14, 1, 1, 10, 1)
        + _s(name)
        + _s(f"STD%R%{depth_mm:.0f}.%{width_mm:.0f}.")
        + _s("MT_CONCRETE")
        + _i(-8)
        + _s(concrete)
        + _i(0, 1)
        + _f(*[c / 1e3 for c in covers_mm])
        + _f(0.0, 0.0, 0.02, link_mm / 1e3, 0.0, 0.0)
        + _i(len(groups))
        + b"".join(groups)
    )
    return _record(b"\xea\x1a\x06\x00", "Sections", body)


def section(name: str, diameter_mm: float, concrete: str, cover_mm: float, groups: list[bytes]) -> bytes:
    body = (
        _i(14, 1, 1, 8, 2)
        + _s(name)
        + _s(f"STD%C%{diameter_mm:.0f}.")
        + _s("MT_CONCRETE")
        + _i(-8)
        + _s(concrete)
        + _i(0, 1)
        + _f(*[cover_mm / 1e3] * 4)
        + bytes(24)
        + _i(len(groups))
        + b"".join(groups)
    )
    return _record(b"\xea\x1a\x06\x00", "Sections", body)


def load_titles(names: list[str]) -> bytes:
    items = [_i(k) + _s(n) for k, n in enumerate(names, 1)]
    return _record(b"\x10\x1c\x06\x00", "Load Titles", _i(1, len(names)) + SEP.join(items))


def forces(rows: list[tuple[float, float, float]]) -> bytes:
    """(N, My, Mz) per load in kN and kNm, compression +."""
    items = [
        _i(k, k, 0, 1, 0) + struct.pack("<3d", n * 1e3, my * 1e3, mz * 1e3)
        for k, (n, my, mz) in enumerate(rows, 1)
    ]
    return _record(b"\x1b\x1c\x06\x00", "Section Forces", _i(2, len(rows)) + SEP.join(items))


def sls_cases(loads: list[int]) -> bytes:
    items = [
        _i(k, 10)
        + _s(f"SLS Case {k}")
        + _s("LD_SLS")
        + _SLS_MID
        + _s("LD_LONG")
        + _s(f"L{load}")
        + _CASE_TAIL
        for k, load in enumerate(loads, 1)
    ]
    return _record(b"\x1f\x1c\x06\x00", "SLS Analysis Cases", _i(10, len(loads)) + SEP.join(items))


def uls_cases(loads: list[int]) -> bytes:
    items = [
        _i(k, 10)
        + _s(f"ULS Case {k}")
        + _s("LD_ULS")
        + _ULS_MID
        + _s("LD_SHORT")
        + _s(f"L{load}")
        + _CASE_TAIL
        for k, load in enumerate(loads, 1)
    ]
    return _record(b"\x20\x1c\x06\x00", "ULS Analysis Cases", _i(2, len(loads)) + SEP.join(items))


def rebar_grade(grade: str) -> str:
    """AdSec's name of a BS 4449 grade: B500B -> 500B."""
    return grade[1:] if grade.upper().startswith("B") else grade


def ring_groups(rings: list[dict[str, Any]], grade: str) -> list[bytes]:
    groups = []
    for i, r in enumerate(rings):
        if i == 0:
            groups.append(perimeter_group(r["diameter"], r["count"], r["radius"], grade))
        else:
            groups.append(circle_group(r["diameter"], r["count"], (r["radius"] / 1e3, 0.0), grade))
    return groups


def _title(state: str, row: dict[str, Any]) -> str:
    text = f"{state} {row['case']} {row.get('combination', '')}".strip()
    if row.get("z") is not None:
        text += f" z {row['z']:g}"
    return text[:60]


def pile_file(
    *,
    job: str,
    title: str,
    diameter_mm: float,
    concrete: str,
    rebar: str,
    cover_mm: float,
    rings: list[dict[str, Any]],
    qp: list[dict[str, Any]],
    uls: list[dict[str, Any]],
    heading: str = "",
    when: datetime | None = None,
) -> bytes:
    """One .ads file: the section, its QP loads as SLS cases and its ULS loads as ULS cases."""
    grade = rebar_grade(rebar)
    sec = section("Concrete Pile", diameter_mm, concrete, cover_mm, ring_groups(rings, grade))
    return ads_file(
        job=job,
        title=title,
        subtitle=f"D = {diameter_mm:.0f} mm",
        heading=heading,
        section_record=sec,
        qp=qp,
        uls=uls,
        forces_of=lambda r: (r["N_kN"], r["M2_kNm"], r["M3_kNm"]),
        when=when,
    )


def ads_file(
    *,
    job: str,
    title: str,
    subtitle: str,
    heading: str,
    section_record: bytes,
    qp: list[dict[str, Any]],
    uls: list[dict[str, Any]],
    forces_of,
    when: datetime | None = None,
) -> bytes:
    """The records around a section: its QP loads as SLS cases and ULS loads as ULS cases.
    ``forces_of(row)`` gives the row's (N, My, Mz) in kN and kNm."""
    when = when or clock.now()
    rows = [("QP", r) for r in qp] + [("ULS", r) for r in uls]
    records = [
        STATIC["program"],
        titles(job, title, subtitle, when.strftime("%d-%b-%Y"), "Triton", heading),
        history(when),
        STATIC["national"],
        STATIC["units"],
        analysis(len(uls)),
        STATIC["basic_cover"],
        STATIC["bar_criteria"],
        STATIC["beam_spacing"],
        STATIC["bar_spacing"],
        STATIC["bar_sizes"],
        STATIC["bar_limits"],
        section_record,
        STATIC["rebar"],
        load_titles([_title(s, r) for s, r in rows]),
        forces([forces_of(r) for _, r in rows]),
        STATIC["cover"],
        sls_cases(list(range(1, len(qp) + 1))),
        uls_cases(list(range(len(qp) + 1, len(rows) + 1))),
    ]
    return END.join(records) + END


def read_records(data: bytes) -> dict[str, bytes]:
    """Records of an .ads file by name (first zero-terminated string after the 4-byte type)."""
    out = {}
    for rec in data.split(END):
        if len(rec) > 4:
            name = rec[4:].split(b"\0", 1)[0].decode("cp1252", "replace")
            out[name] = rec
    return out


def read_forces(data: bytes) -> list[tuple[float, float, float]]:
    """(N, My, Mz) in kN and kNm of every load of an .ads file."""
    rec = read_records(data)["Section Forces"]
    body = rec[4 + len(b"Section Forces\0") :]
    _, count = struct.unpack("<2i", body[:8])
    out = []
    for item in body[8:].split(SEP)[:count]:
        n, my, mz = struct.unpack("<3d", item[-24:])
        out.append((n / 1e3, my / 1e3, mz / 1e3))
    return out


def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9()._ -]+", "_", text).strip() or "section"


def section_files(
    job: str, section_name: str, results: dict[str, Any], elements: dict[str, dict[str, Any]], rebar: str
) -> dict[str, bytes]:
    """An .ads file per station (part) of every designed pile and combi wall infill, per beam, and per
    row of every slab's strip table.

    ``elements`` maps each element name to its diameter (mm), concrete grade and cover (mm) to
    the main bars' links, as designed.
    """
    results = named_parts(results)  # a corner berth's parts by their own names
    designs = [(p["element"], p) for p in results.get("piles", [])]
    designs += [
        (f"{w['element']} infill", w["infill"]) for w in results.get("combi_walls", []) if w.get("infill")
    ]
    files: dict[str, bytes] = {}
    for name, d in designs:
        info = elements.get(name)
        stations = d.get("governing_sets") or []
        if not info or not stations:
            continue
        for k, st in enumerate(stations, 1):
            rings = st.get("rings") or (d.get("arrangement") or {}).get("rings")
            if not rings:
                continue
            part = f"Part {k}" if len(stations) > 1 else "Part 1"
            title = f"{name} - {part}"
            heading = f"{section_name}: {st['top']:g} to {st['bottom']:g} m, {st['cage']}"
            files[_safe(f"{name} - {info['diameter']:.0f}mm - {part}") + ".ads"] = pile_file(
                job=job,
                title=title,
                diameter_mm=info["diameter"],
                concrete=info["concrete"],
                rebar=rebar,
                cover_mm=info["cover"],
                rings=rings,
                qp=st["qp"],
                uls=st["uls"],
                heading=heading,
            )
    for b in results.get("beams", []):
        files.update(beam_files(job, section_name, b, rebar))
    for d in results.get("slabs", []):
        files.update(slab_files(job, section_name, d, rebar))
    return files


def beam_files(job: str, section_name: str, beam: dict[str, Any], rebar: str) -> dict[str, bytes]:
    """One .ads file per designed beam: its cage and its 7 QP and 7 ULS governing sets."""
    cage = beam.get("cage") or {}
    lines = cage.get("lines")
    sets = beam.get("governing_sets") or []
    if not lines or not sets:
        return {}
    grade = rebar_grade(rebar)
    b, h = beam["width_mm"], beam["depth_mm"]
    groups = [
        line_group(
            ln["phi"],
            ln["count"],
            (ln["a"][0] / 1e3, ln["a"][1] / 1e3),
            (ln["b"][0] / 1e3, ln["b"][1] / 1e3),
            grade,
        )
        for ln in lines
    ]
    sec = rect_section(
        beam["element"],
        h,
        b,
        beam["concrete"],
        (beam["cover_mm"],) * 4,
        cage.get("link_diameter_mm") or 0.0,
        groups,
    )
    name = f"{beam['element']} {b:.0f}X{h:.0f}"
    st = sets[0]
    data = ads_file(
        job=job,
        title=name,
        subtitle=f"{b:.0f} x {h:.0f} mm",
        heading=f"{section_name}: {cage.get('label', '')}"[:80],
        section_record=sec,
        qp=st.get("qp") or [],
        uls=st.get("uls") or [],
        forces_of=lambda r: (r["N_kN"], r["M3_kNm"], r["M2_kNm"]),  # My = vertical bending, sagging +
    )
    return {_safe(name) + ".ads": data}


_ADD = re.compile(r"Ø(\d+) @ ([\d.]+)( in 2 layers)?( \+ Ø\d+ (?:under the mesh|behind the mesh bars))?$")


def slab_bars(
    mesh: dict[str, Any], additional: str | None, cross_mm: float, cover_mm: float, width_mm: float
) -> list[tuple[float, int, float, float, float]]:
    """Bars of one face of a slab strip ``width_mm`` wide, as lines (Ø, count, y from, y to, depth
    from the face to the bar centres), all in mm. The strip repeats the slab's bar pattern: the mesh
    at its spacing, additional bars in the gaps between (every gap or every second one), in a second
    layer, and behind the mesh bars (inside the mesh), at the layer pitch Triton designs with (Ø + 25 mm)."""
    phi_b, s_b, lay_b = mesh["phi"], mesh["spacing_mm"], mesh.get("layers") or 1
    m = _ADD.match(additional or "")
    n = max(1, round(width_mm / s_b))
    offset = s_b / 4 if m else s_b / 2
    y0 = -width_mm / 2 + offset
    mesh_y = (y0, y0 + (n - 1) * s_b)
    out = []
    t1 = cover_mm + cross_mm + phi_b / 2
    if not m:
        pitch = phi_b + 25
        return [(phi_b, n, *mesh_y, t1 + k * pitch) for k in range(lay_b)]
    phi_a, s_a = int(m.group(1)), float(m.group(2))
    pitch = max(phi_a, phi_b) + 25
    t_a = cover_mm + cross_mm + phi_a / 2
    for k in range(lay_b):
        out.append((phi_b, n, *mesh_y, t1 + k * pitch))
    every = 2 if s_a > s_b + 1e-6 else 1
    gaps = list(range(0, n, every))
    gy = (y0 + s_b / 2 + gaps[0] * s_b, y0 + s_b / 2 + gaps[-1] * s_b)
    out.append((phi_a, len(gaps), *gy, t_a))
    if m.group(3):
        out.append((phi_a, len(gaps), *gy, t_a + pitch))
    if m.group(4):
        out.append((phi_a, n, *mesh_y, t1 + lay_b * pitch if lay_b > 1 else t1 + pitch))
    return out


def strip_width(spacing_mm: float) -> float:
    """The AdSec strip: a whole number of mesh bars about 1 m wide, as the office's files (1050 mm for a
    150 mm mesh, 1000 mm for 200 mm). The forces per metre are multiplied by width / 1000."""
    return spacing_mm * max(1, round(1000 / spacing_mm))


def tension_face(row: dict[str, Any]) -> str:
    """The face whose bars set the strip width: the row's worst face (its tension face)."""
    return row.get("face") if row.get("face") in ("bottom", "top") else "bottom"


def slab_sets(faces: dict[str, list[dict[str, Any]]], kind: str) -> list[dict[str, Any]]:
    """The sets of one strip and direction over both faces: max N, min N, max M (sagging) and min M
    (hogging) over every combination, each naming its combination, plus each face's governing set (the
    one that sets its bars) when it is not already among them. M sagging +, N compression +."""
    pool = []
    for f in ("bottom", "top"):
        for r in faces.get(f, []):
            for x in (r.get("sets") or {}).get(kind, []):
                pool.append((f, x))
    if not pool:
        return []

    def pick(key) -> tuple[str, dict]:
        return max(pool, key=key)

    chosen = [
        ("max N", pick(lambda p: (p[1]["N_kN_per_m"], abs(p[1]["M_kNm_per_m"])))),
        ("min N", pick(lambda p: (-p[1]["N_kN_per_m"], abs(p[1]["M_kNm_per_m"])))),
        ("max M", pick(lambda p: p[1]["M_kNm_per_m"])),
        ("min M", pick(lambda p: -p[1]["M_kNm_per_m"])),
    ]
    chosen = [(c, p) for c, p in chosen if not (c == "max M" and p[1]["M_kNm_per_m"] <= 0)]
    chosen = [(c, p) for c, p in chosen if not (c == "min M" and p[1]["M_kNm_per_m"] >= 0)]
    for f, x in pool:
        if "governing" in x.get("case", ""):
            chosen.append((f"governing {'sagging' if f == 'bottom' else 'hogging'}", (f, x)))
    out: dict[tuple, dict[str, Any]] = {}
    for case, (f, x) in chosen:
        key = (x["combination"], x["N_kN_per_m"], x["M_kNm_per_m"])
        if key in out:
            if case not in out[key]["case"]:
                out[key]["case"] += f", {case}"
            continue
        out[key] = {**x, "case": case, "face": f}
    return list(out.values())


def slab_files(job: str, section_name: str, slab: dict[str, Any], rebar: str) -> dict[str, bytes]:
    """One .ads file per row of the slab's strip table (a direction, stations and strip), a whole number
    of the tension face's mesh bars about 1 m wide (``strip_width``): the bars of both faces and, for QP
    and ULS, the max N, min N, max M and min M sets plus the governing ones (``slab_sets``)."""
    sd = slab.get("strip_design") or {}
    rows = {r["key"]: r for r in sd.get("rows") or [] if r.get("sets") is not None}
    if not rows or not sd.get("table"):
        return {}
    grade = rebar_grade(rebar)
    h = slab["thickness_mm"]
    covers = {"top": slab["cover_top_mm"], "bottom": slab["cover_bottom_mm"]}
    basic = {k: (v.get("basic") or {}) for k, v in (slab.get("layers") or {}).items()}
    files: dict[str, bytes] = {}
    for row in sd["table"]:
        faces = {f: [rows[k] for k in row["keys"].get(f, []) if k in rows] for f in ("bottom", "top")}
        if not all(faces.values()):
            continue
        first = {f: faces[f][0] for f in faces}
        width = strip_width(first[tension_face(row)]["mesh"]["spacing_mm"])
        groups = []
        for f, sign in (("bottom", -1), ("top", 1)):
            direction = first[f]["layer"].split("_")[1]
            cross = (basic.get(f"{f}_x") or {}).get("phi", 0) if direction == "y" else 0
            own = first[f].get("spec")
            if own and first[f]["additional_bars"] and not _ADD.match(first[f]["additional_bars"]):
                bars = spec_bars(first[f]["mesh"], own, cross, covers[f], width)
            else:
                bars = slab_bars(first[f]["mesh"], first[f]["additional_bars"], cross, covers[f], width)
            for phi, count, ya, yb, t in bars:
                z = sign * (h / 2 - t) / 1e3
                groups.append(line_group(phi, count, (ya / 1e3, z), (yb / 1e3, z), grade))
        if row["strip"] == "all":  # over the whole deck, or a zone of it
            where = (
                row["label"]
                .replace("Whole deck, basic mesh", "whole deck")
                .replace("Zone at station ", "zone ")
            )
            name = f"SLAB {h:.0f} - {row['moment'].lower()} - {where.replace(',', '')}"
        else:
            strip = "CS" if row["strip"] == "column" else "FS"
            where = row["label"].replace("Station ", "")
            name = f"SLAB {h:.0f} - {row['moment'].lower()} - {where} - {strip}"
        sec = rect_section(
            f"Slab {h:.0f}mm",
            h,
            width,
            slab["concrete"],
            (covers["top"],) + (covers["bottom"],) * 3,
            0,
            groups,
        )
        k = width / 1000

        def loads(kind: str, faces=faces, k=k) -> list[dict[str, Any]]:
            return [
                {
                    "case": x["case"],
                    "combination": x["combination"],
                    "N_kN": x["N_kN_per_m"] * k,
                    "M_kNm": x["M_kNm_per_m"] * k,
                }
                for x in slab_sets(faces, kind)
            ]

        files[_safe(name) + ".ads"] = ads_file(
            job=job,
            title=f"SLAB {h:.0f}mm - {row['moment'].lower()}",
            subtitle=f"({where}) - {strip}",
            heading=f"{section_name}: strip {width:.0f} mm wide ({tension_face(row)} bars at "
            f"{first[tension_face(row)]['mesh']['spacing_mm']:g} mm), forces per metre x {k:g}",
            section_record=sec,
            qp=loads("qp"),
            uls=loads("uls"),
            forces_of=lambda r: (r["N_kN"], r["M_kNm"], 0.0),
        )
    return files


def spec_bars(
    mesh: dict[str, Any], spec: list, cross_mm: float, cover_mm: float, width_mm: float
) -> list[tuple[float, int, float, float, float]]:
    """As ``slab_bars`` for bar layers set by the user: [(Ø, spacing) or None] per layer, the first
    between the mesh bars, the next ones under it, each layer below the last with a clear gap of
    max(25 mm, Ø)."""
    phi_b, s_b, lay_b = mesh["phi"], mesh["spacing_mm"], mesh.get("layers") or 1
    n = max(1, round(width_mm / s_b))
    y0 = -width_mm / 2 + s_b / 4
    mesh_y = (y0, y0 + (n - 1) * s_b)
    layers = []
    for k in range(max(lay_b, len(spec))):
        items = [("mesh", phi_b, s_b)] if k < lay_b else []
        if k < len(spec) and spec[k]:
            items.append(("add", float(spec[k][0]), float(spec[k][1])))
        if items:
            layers.append((k, items))
    out = []
    at = cover_mm + cross_mm
    prev = None
    for _, items in layers:
        big = max(i[1] for i in items)
        at += big / 2 if prev is None else prev / 2 + max(25.0, prev, big) + big / 2
        prev = big
        for kind, phi, sp in items:
            if kind == "mesh":
                out.append((phi, n, *mesh_y, at))
            elif sp <= s_b / 2 + 1e-6:  # under every mesh bar and every gap
                out.append((phi, 2 * n, y0, y0 + (2 * n - 1) * s_b / 2, at))
            else:
                every = 2 if sp > s_b + 1e-6 else 1
                gaps = list(range(0, n, every))
                out.append((phi, len(gaps), y0 + s_b / 2 + gaps[0] * s_b, y0 + s_b / 2 + gaps[-1] * s_b, at))
    return out


def zip_files(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()
