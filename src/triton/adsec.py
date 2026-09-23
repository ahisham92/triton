"""AdSec 8.3 section files (.ads) of designed circular concrete sections, ready to analyse.

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

The layout of every generated record matches the office file byte for byte when given the
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

from .adsec_template import STATIC

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
    when = when or datetime.now()
    grade = rebar_grade(rebar)
    rows = [("QP", r) for r in qp] + [("ULS", r) for r in uls]
    records = [
        STATIC["program"],
        titles(job, title, f"D = {diameter_mm:.0f} mm", when.strftime("%d-%b-%Y"), "Triton", heading),
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
        section("Concrete Pile", diameter_mm, concrete, cover_mm, ring_groups(rings, grade)),
        STATIC["rebar"],
        load_titles([_title(s, r) for s, r in rows]),
        forces([(r["N_kN"], r["M2_kNm"], r["M3_kNm"]) for _, r in rows]),
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
    """An .ads file per station (part) of every designed pile and combi wall infill.

    ``elements`` maps each element name to its diameter (mm), concrete grade and cover (mm) to
    the main bars' links, as designed.
    """
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
    return files


def zip_files(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()
