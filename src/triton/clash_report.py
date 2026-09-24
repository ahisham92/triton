"""The clash calculation of one pile head: plan and section of the bars, what clashes, each solution
designed again, the punching links, and a "what if" removal of bars, as Word, PDF or Excel (the
renderers of ``report``)."""

from __future__ import annotations

import io
import math
import re

from PIL import Image, ImageDraw

from . import clock
from .figures import INK, MUTED, _font
from .project import Project, Section
from .report import Report

RED = (200, 40, 40)
AMBER = (214, 140, 20)
GREEN = (40, 140, 70)
BLUE = (40, 90, 170)
GREY = (170, 176, 186)
PILE = (120, 70, 150)
PUNCH = (0, 140, 150)
CONCRETE = (238, 238, 232)


def _extent(sc: dict) -> float:
    """Half the width (mm) of the plan window round the pile."""
    legs = max(sc["pile"]["legs_m"] or [0.0]) * 1e3
    punch = max((math.hypot(g["u"], g["v"]) for g in sc["punch"]), default=0.0)
    return max(sc["pile"]["diameter_mm"] / 2 + legs, punch, sc["pile"]["diameter_mm"] / 2) + 250


def _bad(sc: dict, hits: list | None = None) -> tuple[set[int], set[int], set[int], set[int]]:
    """Clashing pile bars, horizontal bars, beam link legs and punching legs (indices in the scene)."""
    pb, hb, lg, pu = set(), set(), set(), set()
    for a, i, b, j, *_ in sc["conflicts"] if hits is None else hits:
        (pb if a == "pile" else pu).add(i)
        if b == "h":
            hb.add(j)
        elif b == "l":
            lg.add(j)
    return pb, hb, lg, pu


def plan_png(
    sc: dict,
    title: str,
    after: dict | None = None,
    removed: set[str] | None = None,
    pile_removed: set[str] | None = None,
) -> bytes:
    """Plan of the bars round a pile head: clashing bars red; with ``after`` (a solution) the bars as
    that solution leaves them, cut bars dashed grey and trimmers green; ``removed`` / ``pile_removed``
    (a "what if") dashed grey."""
    removed, pile_removed = removed or set(), pile_removed or set()
    change = (after or {}).get("change") or {}
    ext = _extent(sc)
    w = h = 1100
    m = 60
    k = (w - 2 * m) / (2 * ext)

    def P(u: float, v: float) -> tuple[float, float]:
        return m + (u + ext) * k, h - m - (v + ext) * k

    img = Image.new("RGB", (w, h + 60), "white")
    d = ImageDraw.Draw(img)
    f = _font(20)
    hits = (after or {}).get("left_pairs") if after else None
    pb, hb, lg, pu = _bad(sc, hits)
    cut = {sc["hbars"][j]["id"] for j in change.get("cut", [])}
    shift = {int(j): v for j, v in (change.get("shift_mm") or {}).items()}
    host = sc["host"]
    if host["kind"] == "beam" and host.get("centre_mm") is not None:
        half = host["width_mm"] / 2
        c = host["centre_mm"]
        if host["along"] == "X":
            d.rectangle([P(-ext, c + half), P(ext, c - half)], fill=CONCRETE)
        else:
            d.rectangle([P(c - half, ext), P(c + half, -ext)], fill=CONCRETE)
    else:
        d.rectangle([P(-ext, ext), P(ext, -ext)], fill=CONCRETE)
    r = sc["pile"]["diameter_mm"] / 2
    d.ellipse([P(-r, r), P(r, -r)], outline=PILE, width=3)
    mid = (host["top_m"] + host["soffit_m"]) / 2
    # Bottom bars first, the top bars over them (as seen from above); clashing bars last.
    for j in sorted(range(len(sc["hbars"])), key=lambda n: (n in hb, sc["hbars"][n]["z_m"])):
        b = sc["hbars"][j]
        at = b["at"] + shift.get(j, 0.0)
        if b["kind"] == "link leg" or abs(at) > ext:
            continue
        lo = max(b["lo"] if b["lo"] is not None else -ext, -ext)
        hi = min(b["hi"] if b["hi"] is not None else ext, ext)
        gone = b["id"] in removed or b["id"] in cut
        col = GREY if gone else RED if j in hb else (BLUE if b["z_m"] > mid else MUTED)
        wd = max(2, round(b["phi"] * k))
        a, z = (P(lo, at), P(hi, at)) if b["along"] == "X" else (P(at, lo), P(at, hi))
        if gone:
            _dashed(d, a, z, col, 2)
        else:
            d.line([a, z], fill=col, width=wd)
    for t in change.get("trimmers", []):
        c = (sc["pile"].get("x"), sc["pile"].get("y"))
        at = (t["at"] - (c[1] if t["along"] == "X" else c[0])) * 1e3
        lo = (t["lo"] - (c[0] if t["along"] == "X" else c[1])) * 1e3
        hi = (t["hi"] - (c[0] if t["along"] == "X" else c[1])) * 1e3
        lo, hi = max(lo, -ext), min(hi, ext)
        a, z = (P(lo, at), P(hi, at)) if t["along"] == "X" else (P(at, lo), P(at, hi))
        d.line([a, z], fill=GREEN, width=max(2, round(t["phi"] * k)))
    for j, g in enumerate(sc["legs"]):
        rr = max(3, g["phi"] * k / 2)
        x, y = P(g["u"], g["v"])
        d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=RED if j in lg else MUTED)
    turn = math.radians(change.get("rotate_deg") or 0.0)
    crank = change.get("crank_mm") or 0.0
    legs_m = sc["pile"]["legs_m"]
    for i, b in enumerate(sc["pile_bars"]):
        rad = math.hypot(b["u"], b["v"]) - crank
        ang = math.atan2(b["v"], b["u"]) + turn
        u, v = rad * math.cos(ang), rad * math.sin(ang)
        gone = b["id"] in pile_removed
        col = GREY if gone else RED if i in pb else PILE
        leg = legs_m[b["row"]] * 1e3 if b["row"] < len(legs_m) else 0
        if leg > 0 and not gone:
            d.line(
                [P(u, v), P(u + leg * math.cos(ang), v + leg * math.sin(ang))],
                fill=col,
                width=max(2, round(b["phi"] * k * 0.6)),
            )
        rr = max(3, b["phi"] * k / 2)
        x, y = P(u, v)
        if gone:
            d.ellipse([x - rr, y - rr, x + rr, y + rr], outline=GREY, width=2)
        else:
            d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=col)
    legs_after = (after or {}).get("legs_after")
    for n, g in enumerate(sc["punch"]):
        u, v = legs_after[n] if legs_after else (g["u"], g["v"])
        rr = max(3, g["phi"] * k / 2)
        x, y = P(u, v)
        d.rectangle([x - rr, y - rr, x + rr, y + rr], fill=RED if n in pu else PUNCH)
    d.text((m, 18), title, fill=INK, font=f)
    _scale_bar(d, m, h - 20, k, f)
    _legend(
        d,
        m,
        h + 20,
        f,
        [
            (PILE, "pile bars / L legs"),
            (MUTED, "bottom bars"),
            (BLUE, "top bars"),
            (PUNCH, "punching links"),
            (GREEN, "trimmers"),
            (RED, "clash"),
            (GREY, "cut / taken out"),
        ],
    )
    return _png(img)


def section_png(
    sc: dict,
    title: str,
    cut: str = "X",
    removed: set[str] | None = None,
    pile_removed: set[str] | None = None,
) -> bytes:
    """Vertical section through the pile's centre (``cut`` = the plan axis across the page): the pile
    bars up into the element and their L legs, the bars along the other axis cut (dots), the bars
    along this axis as lines."""
    removed, pile_removed = removed or set(), pile_removed or set()
    ext = _extent(sc)
    host = sc["host"]
    top, soffit = host["top_m"] * 1e3, host["soffit_m"] * 1e3
    below = 600.0
    z_lo, z_hi = soffit - below, top + 120
    w, m = 1300, 70
    k = (w - 2 * m) / (2 * ext)
    h = int((z_hi - z_lo) * k) + 2 * m

    def P(u: float, z: float) -> tuple[float, float]:
        return m + (u + ext) * k, h - m - (z - z_lo) * k

    img = Image.new("RGB", (w, h + 60), "white")
    d = ImageDraw.Draw(img)
    f = _font(20)
    pb, hb, _lg, pu = _bad(sc)
    d.rectangle([P(-ext, top), P(ext, soffit)], fill=CONCRETE, outline=INK, width=2)
    r = sc["pile"]["diameter_mm"] / 2
    d.rectangle([P(-r, soffit), P(r, z_lo)], fill=CONCRETE)
    d.line([P(-r, soffit), P(-r, z_lo)], fill=INK, width=2)
    d.line([P(r, soffit), P(r, z_lo)], fill=INK, width=2)
    other = "Y" if cut == "X" else "X"
    for j, b in enumerate(sc["hbars"]):
        z = b["z_m"] * 1e3
        col = GREY if b["id"] in removed else RED if j in hb else (BLUE if z > (top + soffit) / 2 else MUTED)
        if b["along"] == other:
            if abs(b["at"]) <= ext and b["kind"] != "link leg":
                rr = max(3, b["phi"] * k / 2)
                x, y = P(b["at"], z)
                d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=col)
        elif abs(b["at"]) < r + 80 and b["kind"] != "link leg":
            lo = max(b["lo"] if b["lo"] is not None else -ext, -ext)
            hi = min(b["hi"] if b["hi"] is not None else ext, ext)
            d.line([P(lo, z), P(hi, z)], fill=col, width=max(2, round(b["phi"] * k * 0.5)))
    legs_m = sc["pile"]["legs_m"]
    z0 = sc["pile"]["enters_m"] * 1e3
    for i, b in enumerate(sc["pile_bars"]):
        across = b["v"] if cut == "X" else b["u"]
        u = b["u"] if cut == "X" else b["v"]
        if abs(across) > 0.35 * r:
            continue
        col = GREY if b["id"] in pile_removed else RED if i in pb else PILE
        zt = b["top_m"] * 1e3
        wd = max(2, round(b["phi"] * k * 0.6))
        d.line([P(u, z_lo), P(u, zt)], fill=col, width=wd)
        leg = legs_m[b["row"]] * 1e3 if b["row"] < len(legs_m) else 0
        if leg > 0:
            s = 1 if u >= 0 else -1
            d.line([P(u, zt), P(u + s * leg, zt)], fill=col, width=wd)
    for n, g in enumerate(sc["punch"]):
        across = g["v"] if cut == "X" else g["u"]
        if abs(across) > 60:
            continue
        u = g["u"] if cut == "X" else g["v"]
        zs = [b["z_m"] * 1e3 for b in sc["hbars"]]
        lo, hi = (min(zs), max(zs)) if zs else (soffit + 60, top - 60)
        d.line([P(u, lo), P(u, hi)], fill=RED if n in pu else PUNCH, width=max(2, round(g["phi"] * k)))
    d.text((m, 18), title, fill=INK, font=f)
    for z, label in ((top, f"{top / 1e3:.2f}"), (soffit, f"{soffit / 1e3:.2f}"), (z0, f"{z0 / 1e3:.2f}")):
        d.text((m - 6, P(0, z)[1]), label, fill=INK, font=_font(16), anchor="rm")
    _scale_bar(d, m, h - 20, k, f)
    _legend(
        d,
        m,
        h + 20,
        f,
        [
            (PILE, "pile bars"),
            (MUTED, "bottom bars"),
            (BLUE, "top bars"),
            (PUNCH, "punching links"),
            (RED, "clash"),
            (GREY, "taken out"),
        ],
    )
    return _png(img)


def _dashed(d: ImageDraw.ImageDraw, a: tuple, b: tuple, col: tuple, wd: int) -> None:
    n = max(1, int(math.hypot(b[0] - a[0], b[1] - a[1]) / 14))
    for i in range(0, n, 2):
        t0, t1 = i / n, min((i + 1) / n, 1)
        d.line(
            [
                (a[0] + (b[0] - a[0]) * t0, a[1] + (b[1] - a[1]) * t0),
                (a[0] + (b[0] - a[0]) * t1, a[1] + (b[1] - a[1]) * t1),
            ],
            fill=col,
            width=wd,
        )


def _scale_bar(d: ImageDraw.ImageDraw, x: float, y: float, k: float, f) -> None:
    d.line([x, y, x + 500 * k, y], fill=INK, width=3)
    d.text((x + 500 * k + 8, y), "500 mm", fill=INK, font=f, anchor="lm")


def _legend(d: ImageDraw.ImageDraw, x: float, y: float, f, items: list[tuple[tuple, str]]) -> None:
    for col, text in items:
        d.rectangle([x, y - 7, x + 14, y + 7], fill=col)
        d.text((x + 20, y), text, fill=INK, font=f, anchor="lm")
        x += 40 + d.textlength(text, font=f)


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# --- The calculation -----------------------------------------------------------------------------------


def _checks_table(r: Report, checks: list[dict]) -> None:
    rows = []
    for c in checks:
        b, a = c.get("before") or {}, c.get("after") or {}
        rows.append(
            [
                c["what"],
                b.get("utilisation"),
                a.get("utilisation"),
                b.get("wk_mm"),
                a.get("wk_mm"),
                c["passes"],
            ]
        )
    r.table(
        ["Check", "Utilisation before", "Utilisation after", "wk before (mm)", "wk after (mm)", "Result"],
        rows,
    )
    for c in checks:
        if c.get("note"):
            r.note(c["note"])


def _ids(sc: dict, ids: list[str]) -> list[str]:
    by = {b["id"]: b for b in sc["hbars"]}
    out = []
    for i in ids:
        b = by.get(i)
        out.append(
            f"{b['group']}, {abs(b['at']):.0f} mm {'north' if b['along'] == 'X' and b['at'] >= 0 else 'south' if b['along'] == 'X' else 'east' if b['at'] >= 0 else 'west'} of the pile's centre"
            if b
            else i
        )
    return out


def build_calc(
    project: Project, section: Section, entry: dict, notes: list[str], whatif: dict | None = None
) -> Report:
    """The calculation of one head (``entry`` from ``Clashes.entry``), with a "what if" when given."""
    sc = entry["scene"]
    pile, host = sc["pile"]["element"], sc["host"]["element"]
    head_no = entry["index"] + 1
    r = Report(
        f"{project.info.name}: reinforcement clash check",
        f"{section.name}, {pile} head {head_no} in {host} (x {entry['x']:.2f} m, y {entry['y']:.2f} m)"
        f", {clock.now().strftime('%d %b %Y %H:%M')}",
    )
    rule = section.clashes
    r.h(1, "The connection")
    r.kv(
        [
            (
                "Pile",
                f"{pile} Ø{sc['pile']['diameter_mm']:g} mm, head {head_no} at x {entry['x']:.3f} m, y {entry['y']:.3f} m",
            ),
            (
                "Element over it",
                f"{host} ({sc['host']['kind']}), soffit {sc['host']['soffit_m']:.3f} m, top {sc['host']['top_m']:.3f} m",
            ),
            (
                "Clash rule",
                "EN 1992-1-1 8.2(2) clear spacing"
                if rule.rule == "ec2"
                else f"bars overlap or pass within {rule.fixing_tolerance:g} mm",
            ),
            (
                "Pile bar clashes",
                f"{entry['count']['clash']} overlapping, {entry['count']['tight']} too close ({entry['count']['pairs']} pairs)",
            ),
            (
                "Punching link clashes",
                f"{entry['punch_count']['clash']} overlapping, {entry['punch_count']['tight']} too close",
            ),
        ]
    )
    r.p(sc["connection"]["text"] + ".")
    r.table(
        ["Row", "Bars", "Up into the element (m)", "L leg (m)", "Shape"],
        [[c["row"], c["bars"], c["up_m"], c["leg_m"], c["shape"]] for c in sc["connection"]["rows"]],
    )
    removed = set((whatif or {}).get("bars") or [])
    pile_removed = set((whatif or {}).get("pile_bars") or [])
    r.image(
        plan_png(sc, f"Plan: {pile} head {head_no} in {host}", removed=removed, pile_removed=pile_removed),
        "Plan at the pile head, as designed"
        + (" (bars taken out in the 'what if' dashed grey)." if whatif else "."),
    )
    r.image(
        section_png(sc, f"Section across X through {pile}", "X", removed, pile_removed),
        "Section on the X axis through the pile's centre.",
    )
    r.image(
        section_png(sc, f"Section across Y through {pile}", "Y", removed, pile_removed),
        "Section on the Y axis through the pile's centre.",
    )
    if whatif:
        r.h(1, "What if: bars taken out")
        if whatif.get("note"):
            r.p(whatif["note"])
        r.bullets(
            [f"Taken out: {t}" for t in _ids(sc, whatif.get("bars") or [])]
            + [
                f"Pile bar {p} (row {int(p.split(':')[0]) + 1}, bar {int(p.split(':')[1]) + 1}) taken out at the head"
                for p in whatif.get("pile_bars") or []
            ]
        )
        if whatif.get("unknown"):
            r.note("Not found in the design (skipped): " + ", ".join(whatif["unknown"]))
        _checks_table(r, whatif.get("checks") or [])
        r.p(
            (
                "The connection still works with these bars taken out."
                if whatif.get("passes")
                else "The connection does NOT work with these bars taken out: see the checks marked 'fails'."
            )
            + f" Steel change {whatif.get('delta_kg', 0):+.1f} kg. The design itself is unchanged."
        )
    if entry.get("what"):
        r.h(1, "What clashes")
        r.table(
            ["Bars", "Pairs", "Overlapping", "Worst clear gap (mm)", "Levels (m)"],
            [
                [
                    w["bars"],
                    w["pairs"],
                    w["clash"],
                    w["worst_gap_mm"],
                    ", ".join(f"{z:.3f}" for z in w["levels_m"]),
                ]
                for w in entry["what"]
            ],
        )
    if entry.get("solutions"):
        r.h(1, "Solutions, each designed again")
        for s in entry["solutions"]:
            r.h(2, s["title"] + (" (recommended)" if s["recommended"] else ""))
            r.p(s["how"])
            r.kv(
                [
                    ("Result", "passes" if s["passes"] else "fails"),
                    ("Clashes left", f"{s['left']['clash']} overlapping, {s['left']['tight']} too close"),
                    (
                        "Steel change",
                        f"{s['delta_kg']:+.1f} kg"
                        + (
                            f" ({s['delta_kg_m3_per_head']:+.3f} kg/m³ of {s['element']})"
                            if s.get("delta_kg_m3_per_head") is not None
                            else ""
                        ),
                    ),
                ]
            )
            if s["checks"]:
                _checks_table(r, s["checks"])
            if s["recommended"]:
                r.image(
                    plan_png(sc, f"Plan after: {s['title']}", after=s), "Plan with the recommended solution."
                )
    p = entry.get("punching")
    if p:
        r.h(1, "Punching links")
        info = p.get("info") or {}
        r.kv(
            [
                ("Effective depth d", f"{info.get('d_mm')} mm"),
                ("Legs", f"Ø{info.get('phi')} per perimeter {info.get('counts')}"),
                ("Perimeters from the pile's face", f"{info.get('radii_mm')} mm from the centre"),
                ("Asw per perimeter needed", f"{info.get('asw_mm2_per_perimeter')} mm²"),
                (
                    "Utilisation",
                    f"{info.get('utilisation')} without links, {info.get('utilisation_with_links')} with",
                ),
            ]
        )
        _layout(r, p["layout"])
        sol = p.get("solution")
        if sol:
            r.h(2, sol["title"])
            r.p(sol["how"])
            r.kv(
                [
                    ("Result", "passes" if sol["passes"] else "fails"),
                    ("Clashes left", f"{sol['left']['clash']} overlapping, {sol['left']['tight']} too close"),
                ]
            )
            _layout(r, sol["layout"])
            r.image(
                plan_png(sc, "Punching links set out in the mesh openings", after={**sol, "change": {}}),
                "Punching legs as fixed on site, each in a clear opening of the mesh.",
            )
    r.h(1, "Assumptions")
    r.bullets(notes)
    return r


def _layout(r: Report, lay: dict) -> None:
    r.table(
        [
            "Perimeter",
            "Legs",
            "From the face (mm)",
            "Round it (mm)",
            "Limit",
            "From the one inside (mm)",
            "Limit",
            "Asw (mm²)",
            "Needed",
            "Result",
        ],
        [
            [
                x["perimeter"],
                x["legs"],
                "–".join(str(v) for v in x["from_face_mm"]),
                x["tangential_mm"],
                x["tangential_limit_mm"],
                x["radial_mm"],
                x["radial_limit_mm"],
                x["asw_mm2"],
                x["asw_needed_mm2"],
                x["passes"],
            ]
            for x in lay["rows"]
        ],
    )


def filename(project: Project, section: Section, entry: dict, whatif: dict | None) -> str:
    head = f"{entry['scene']['pile']['element']} {entry['index'] + 1} {entry['scene']['host']['element']}"
    return (
        re.sub(
            r"[^A-Za-z0-9._-]+",
            "_",
            f"{project.info.name} {section.name} clash {head}" + (" what-if" if whatif else ""),
        ).strip("_")
        or "clash"
    )
