"""Diaphragm wall bars into the beam over the wall: clashes and anchorage.

A diaphragm wall's vertical bars run straight up into the capping (front) beam and stop under its top
bars, as the combi wall's bars do (drawing SC-401). There they pass the beam's bottom bars, which run
along the wall, its link legs and its transverse bars. This checks one panel's cage in the middle of
the wall, with the wall's top zone of bars (both faces, every layer) placed as the drawing places them:
from ``end_clear_mm`` of each end of the cage at equal steps, the first layer just inside the
horizontal bars.

Geometry
--------
* The wall's Plaxis plate is its mid-thickness line (``line_m`` of the design).
* The beam over it is the beam that runs the same way and whose width holds that line.
* The front (sea) face is on the side away from the land: the side away from the rear beam (or the
  slab's centre, if there is no rear beam).

Ways out (each checked again for clashes, the beam's design is not changed):

* ``set_out``: slide the cage along the wall (at most one bar spacing) so its bars fall between the
  beam's links and transverse bars. The panel joints move with it.
* ``between``: lay the links' bottom legs and each layer of transverse bars again between the wall's
  bars over the panel, none further apart than designed (the count per panel against the design's).
* ``shift``: move each beam bottom bar that sits on a wall face line sideways to the nearest clear
  place (bars per metre the same).
"""

from __future__ import annotations

import math

import numpy as np

from .clashes import (
    SHIFT_STEP,
    Ctx,
    Head,
    Hit,
    Host,
    _beam_axes,
    _beam_bars,
    _beam_top_zone,
    _hosts,
    _limit,
    conflicts,
)

LAYER_CLEAR_MM = 32.0  # as the drawings and the AdSec files
SLIDE_STEP = 5.0  # mm


def _axis(line: list[list[float]]) -> str:
    (x0, y0), (x1, y1) = line
    return "X" if abs(x1 - x0) >= abs(y1 - y0) else "Y"


def _host(hosts: list[Host], along: str, t: float, lo: float, hi: float) -> Host | None:
    """The beam over the wall: same direction, the wall line inside its width, overlapping it along."""
    best = None
    for h in hosts:
        if h.kind != "beam" or _beam_axes(h.data)[0] != along:
            continue
        d = h.data
        b0, b1 = sorted((d["start_m"], d["end_m"]))
        off = abs(t - d["centre_m"])
        if off <= d["width_mm"] / 2e3 and min(hi, b1) > max(lo, b0) and (best is None or off < best[0]):
            best = (off, h)
    return best[1] if best else None


def _land_sign(drawing: dict, results: dict, along: str, t: float) -> float:
    """+1 when the land is on the side of larger across coordinate."""
    for b in drawing.get("beams") or []:
        if b.get("kind") == "rear_beam" and _beam_axes(b)[0] == along:
            return 1.0 if b["centre_m"] >= t else -1.0
    for s in drawing.get("slabs") or []:
        box = s.get("box_m") or {}
        across = "Y" if along == "X" else "X"
        if box.get(across):
            return 1.0 if sum(box[across]) / 2 >= t else -1.0
    return 1.0


def wall_bars(
    w: dict, zone: dict, s0: float, along: str, t: float, land: float, top: float, slide: float = 0.0
) -> tuple[np.ndarray, list[str]]:
    """The zone's vertical bars of one panel starting at ``s0`` (m along), as rows [x, y, Ø, face, bar,
    top] (face 0 front, 1 back), and a word for each row."""
    W, T, c = w["panel_width_mm"], w["thickness_mm"], w["cover_mm"] or 75.0
    end = w.get("end_clear_mm") or 100.0
    rows, words = [], []
    for fi, (face, sgn) in enumerate((("front", -land), ("back", land))):
        f = zone[face]
        phi = f["diameter_mm"]
        phi_h = zone["horizontal"][face]["diameter_mm"] or 0.0
        layers = max(int(f.get("layers") or 1), 1)
        n = max(int((f.get("per_cage") or 2) / layers), 2)
        step = (W - 2 * end) / (n - 1)
        for k in range(layers):
            off = (T / 2 - c - phi_h - phi / 2 - k * (phi + max(phi, LAYER_CLEAR_MM))) / 1e3
            for i in range(n):
                s = s0 + (end + slide + i * step) / 1e3
                a = t + sgn * off
                x, y = (s, a) if along == "X" else (a, s)
                rows.append([x, y, phi, fi, len(words), top])
                words.append(f"{face} face bar {i + 1}" + (f", layer {k + 1}" if layers > 1 else ""))
    return np.array(rows, float).reshape(-1, 6), words


def _count(hits: list[Hit]) -> dict:
    return {
        "pairs": len(hits),
        "clash": sum(1 for h in hits if h.kind == "clash"),
        "tight": sum(1 for h in hits if h.kind == "tight"),
        "wall_bars": len({h.i for h in hits}),
    }


def _describe(hits: list[Hit], words: list[str], hbars, legs) -> list[dict]:
    """Each group of beam bars hit, with the wall bars it meets."""
    out: dict[str, dict] = {}
    for h in hits:
        g = hbars[h.j].group if h.b == "h" else legs[h.j].group
        e = out.setdefault(
            g, {"beam_bars": g, "wall_bars": set(), "clash": 0, "tight": 0, "least_gap_mm": math.inf}
        )
        e["wall_bars"].add(words[h.i].split(" bar ")[0])
        e[h.kind] += 1
        e["least_gap_mm"] = min(e["least_gap_mm"], h.gap)
    return [{**e, "wall_bars": sorted(e["wall_bars"])} for e in out.values()]


def _between(ctx: Ctx, bars: np.ndarray, hbars, along: str, s0: float, width: float) -> list[dict]:
    """Each set of beam bars that crosses the wall (the links' bottom legs, each layer of transverse
    bars) laid again between the wall's bars over one panel: no two further apart than their designed
    spacing, each clear of the wall's bars by the rule. Gives the bars per panel against the design's."""
    pos = bars[:, 0] if along == "X" else bars[:, 1]
    order = np.argsort(pos)
    pos, phis = pos[order], bars[order, 2]
    out = []
    groups: dict[str, list] = {}
    for h in hbars:
        if h.along != along and h.spacing:
            groups.setdefault(h.layer, []).append(h)
    for hs in groups.values():
        h = hs[0]
        pitch = h.spacing / 1e3
        free = []
        edge = s0
        for a, pa in zip(pos, phis, strict=True):
            pad = (float(_limit(ctx.rule, ctx.dg, np.array(pa), np.array(h.phi))) + (pa + h.phi) / 2) / 1e3
            if a - pad > edge:
                free.append((edge, a - pad))
            edge = max(edge, a + pad)
        if s0 + width > edge:
            free.append((edge, s0 + width))
        placed, last, ok = [], s0 - pitch / 2, True
        while last + pitch < s0 + width:
            reach = last + pitch
            spots = [min(hi, reach) for lo, hi in free if lo <= reach and min(hi, reach) > last + 1e-6]
            if not spots:
                ok = False
                break
            last = max(spots)
            placed.append(round(last, 4))
        out.append(
            {
                "bars": h.group.split(", bottom leg")[0],
                "passes": ok,
                "per_panel": len(placed),
                "designed_per_panel": math.ceil(width / pitch),
                "at_m": placed,
            }
        )
    return out


def _slide(ctx: Ctx, head: Head, w, zone, s0, along, t, land, top, hbars, legs) -> dict | None:
    """The least slide of the cage along the wall that clears the links and transverse bars."""
    across = [h for h in hbars if h.along != along]  # links' bottom legs and transverse bars cross the wall
    if not across and not legs:
        return None
    phi = max(zone[f]["diameter_mm"] for f in ("front", "back"))
    spacing = min(zone[f]["spacing_mm"] for f in ("front", "back"))
    best = None
    for k in range(int(spacing / SLIDE_STEP) + 1):
        d = k * SLIDE_STEP
        for sgn in (1, -1) if d else (1,):
            bars, _ = wall_bars(w, zone, s0, along, t, land, top, sgn * d)
            n = len(conflicts(head, ctx.rule, ctx.dg, bars, across, legs, [], with_punch=False))
            if best is None or n < best[1]:
                best = (sgn * d, n)
            if n == 0:
                return {"passes": True, "slide_mm": sgn * d, "left": 0, "bar_mm": phi}
    return {"passes": False, "slide_mm": best[0], "left": best[1], "bar_mm": phi} if best else None


def _shift(ctx: Ctx, bars: np.ndarray, hbars, hits: list[Hit], along: str) -> list[dict]:
    """How far each beam bar along the wall that meets the wall's bars moves sideways to clear them."""
    out = []
    for j in sorted({h.j for h in hits if h.b == "h" and hbars[h.j].along == along}):
        hb = hbars[j]
        cross = bars[:, 1] if along == "X" else bars[:, 0]
        phis = bars[:, 2]

        def clear(at: float, hb=hb, cross=cross, phis=phis) -> bool:
            gap = np.abs(cross - at) * 1e3 - (phis + hb.phi) / 2
            return bool((gap >= _limit(ctx.rule, ctx.dg, phis, np.full_like(phis, hb.phi))).all())

        move = None
        for k in range(1, 61):
            for sgn in (1, -1):
                if clear(hb.at + sgn * k * SHIFT_STEP / 1e3):
                    move = sgn * k * SHIFT_STEP
                    break
            if move is not None:
                break
        out.append({"bar": hb.group, "at_m": hb.at, "move_mm": move})
    return out


def wall_clashes(ctx: Ctx, drawing: dict, results: dict) -> list[dict]:
    """Each diaphragm wall's top bars against the beam over it."""
    walls = drawing.get("diaphragm_walls") or []
    if not walls:
        return []
    hosts = _hosts(ctx.rule, drawing, results)
    need_factor = ctx.settings.piles.head_anchorage_factor
    out = []
    for w in walls:
        base = {"element": w["element"], "host": None}
        line = w.get("line_m")
        if not line or not w.get("zones"):
            out.append({**base, "text": f"{w['element']}: no plan line in its design; design it again."})
            continue
        along = _axis(line)
        k = 0 if along == "X" else 1
        lo, hi = sorted((line[0][k], line[1][k]))
        t = (line[0][1 - k] + line[1][1 - k]) / 2
        host = _host(hosts, along, t, lo, hi)
        if host is None:
            out.append({**base, "text": f"{w['element']}: no beam runs over the wall; nothing to check."})
            continue
        d = host.data
        b0, b1 = sorted((d["start_m"], d["end_m"]))
        W = w["panel_width_mm"] / 1e3
        mid = (max(lo, b0) + min(hi, b1)) / 2
        s0 = mid - W / 2
        land = _land_sign(drawing, results, along, t)
        zone = w["zones"][0]
        top = host.top - _beam_top_zone(d) / 1e3
        wall_top = w["top_level_m"]
        z0 = max(host.soffit, min(wall_top, host.top))
        bars, words = wall_bars(w, zone, s0, along, t, land, top)
        px, py = (mid, t) if along == "X" else (t, mid)
        hbars, legs = _beam_bars(host, px, py, W / 2 + 0.2)
        head = Head(w["element"], "wall", 0, px, py, w["thickness_mm"], [], host, z0)
        hits = conflicts(head, ctx.rule, ctx.dg, bars, hbars, legs, [], with_punch=False)
        phi = max(zone[f]["diameter_mm"] for f in ("front", "back"))
        have = top - wall_top
        need = need_factor * phi / 1e3
        anchorage = {
            "bar_top_m": round(top, 3),
            "wall_top_m": wall_top,
            "into_beam_m": round(have, 3),
            "needed_m": round(need, 3),
            "short_m": round(max(need - have, 0.0), 3),
        }
        ways = []
        if hits:
            sl = _slide(ctx, head, w, zone, s0, along, t, land, top, hbars, legs)
            long_hits = [h for h in hits if h.b == "h" and hbars[h.j].along == along]
            moves = _shift(ctx, bars, hbars, long_hits, along) if long_hits else []
            if sl:
                ways.append(
                    {
                        "id": "set_out",
                        "title": "Slide the cage along the wall",
                        "passes": sl["passes"],
                        "text": (
                            f"Slide the cage {sl['slide_mm']:+.0f} mm along the wall: its bars clear the "
                            "links and transverse bars"
                            if sl["passes"]
                            else f"No slide within one bar spacing clears the links and transverse bars "
                            f"({sl['left']} pairs left at {sl['slide_mm']:+.0f} mm)"
                        )
                        + (
                            "; the beam's bottom bars on the face lines still need moving."
                            if long_hits
                            else "."
                        ),
                        **sl,
                    }
                )
            across = [h for h in hits if h.b == "h" and hbars[h.j].along != along]
            if across and not (sl and sl["passes"]):
                sets = _between(ctx, bars, hbars, along, s0, W)
                if sets:
                    ok = all(x["passes"] for x in sets)
                    ways.append(
                        {
                            "id": "between",
                            "title": "Lay the links and transverse bars between the wall bars",
                            "passes": ok,
                            "sets": sets,
                            "text": "; ".join(
                                f"{x['bars']}: {x['per_panel']} per panel (designed "
                                f"{x['designed_per_panel']}), none further apart than designed"
                                if x["passes"]
                                else f"{x['bars']}: no clear place within the designed spacing"
                                for x in sets
                            )
                            + (
                                "; the beam's bottom bars on the face lines still need moving."
                                if long_hits
                                else "."
                            ),
                        }
                    )
            if moves:
                ok = all(m["move_mm"] is not None for m in moves)
                ways.append(
                    {
                        "id": "shift",
                        "title": "Move the beam's bottom bars off the wall face lines",
                        "passes": ok,
                        "moves": moves,
                        "text": "; ".join(
                            f"{m['bar']} at {m['at_m']:.3f} m: "
                            + (
                                "no clear place within 300 mm"
                                if m["move_mm"] is None
                                else f"{m['move_mm']:+.0f} mm"
                            )
                            for m in moves
                        ),
                    }
                )
        count = _count(hits)
        words_out = (
            f"{w['element']} into {host.element}: "
            + (
                f"{count['wall_bars']} of {len(bars)} bars of a panel meet the beam's bars "
                f"({count['clash']} clash, {count['tight']} tight)."
                if hits
                else f"the {len(bars)} bars of a panel are clear of the beam's bars."
            )
            + (
                f" The bars reach {have:.2f} m into the beam under its top bars; {need:.2f} m "
                f"({need_factor:g}Ø) anchorage asked, {anchorage['short_m']:.2f} m short: hooks or couplers."
                if anchorage["short_m"] > 0
                else f" The bars reach {have:.2f} m into the beam, enough for {need_factor:g}Ø."
            )
        )
        out.append(
            {
                **base,
                "host": host.element,
                "panel_m": [round(s0, 3), round(s0 + W, 3)],
                "along": along,
                "zone": [zone["top_m"], zone["bottom_m"]],
                "bars": len(bars),
                "count": count,
                "anchorage": anchorage,
                "groups": _describe(hits, words, hbars, legs),
                "ways": ways,
                "text": words_out,
            }
        )
    return out
