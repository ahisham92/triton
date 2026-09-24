"""Manholes, pits and service channels in the deck: cuts the Plaxis model does not have.

The plate results at the cut are used as they are (the model has the slab whole), and the check
is that they can still get round it.

Manholes (and pits), for the bars along X and along Y in turn:

* the bars of width a (the opening's size across them) are cut. Their moment goes to a strip
  each side of width w = max(slab thickness, a/2), so the strips carry
  m·(1 + a/(2w)) per metre. m is the worst sagging and hogging moment per metre (with its N) at
  the nodes round the opening, within one slab thickness of its ends, under each ULS and QP
  combination;
* the strips are designed per metre (N with M, QP cracks at each face) starting from the bars the
  slab has there (its mesh, or the zone of added bars). The trimmer bars each side are the larger of
  half the cut bars and the extra steel the strip needs over its width. They run a lap length past
  the opening;
* shear in the strips, the shear per metre × (1 + a/(2w)), against VRd,c (6.2.2), with links where
  needed;
* two diagonal bars at each corner, top and bottom;
* a pile within 6d of the opening loses the part of its punching perimeter between the tangents
  from its centre to the opening (EC2 6.4.2(3)), so its punching utilisation grows by 1 / (1 − that
  share);
* a pit (not through the slab) also has its floor checked per metre for its own weight and the
  load in it, spanning its shorter side.

If a manhole fails, Triton says the largest opening (in 50 mm steps) that passes.

Service channels (a trough cast into the deck, open at the top) are the beam rooms' check turned
onto the slab (see "Rooms cut into the beam"): a strip as wide as the channel and its walls is
taken as a beam along the channel with the slab's actions along it × that width, on the
section left (bending with N, cracks, shear and torsion in the walls and base). The top bars
that run along it inside the channel come back at the top of the walls. Across the channel the
slab's moment per metre goes through the base and round its corners into the walls (the U-frame,
per metre), with the load in the channel on the base. A base deeper than the slab hangs below the
soffit. If it fails, Triton says what base (or walls) would pass.

For a corner berth the openings are placed in the part whose results they sit in.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from ..axes import sag_factor
from ..materials import REINFORCEMENT_GRADES, concrete
from ..project import BeamRoom, Channel, DesignSettings, Manhole, SlabInput
from .circular import ConcreteLaw, SteelLaw
from .rect import Bars
from .rooms import (
    CONCRETE_WEIGHT,
    GAMMA_G,
    GAMMA_Q,
    PSI2,
    STEP,
    Context,
    _vrdc,
    design_room,
    per_metre,
    room_shape,
)

MIN_PHI = 16


def _bars_at(layer: dict, x0: float, x1: float, y0: float, y1: float) -> tuple[float, int, float]:
    """(mm²/m, Ø, spacing) the slab has in ``layer`` over a plan box: the heaviest zone that touches it,
    else the basic mesh."""
    basic = layer.get("basic") or {}
    area = float(basic.get("as_mm2_per_m") or 0.0)
    for z in layer.get("zones") or []:
        zx, zy = z.get("x") or [0, 0], z.get("y") or [0, 0]
        if zx[0] < x1 and zx[1] > x0 and zy[0] < y1 and zy[1] > y0:
            area = max(area, float(z.get("as_mm2_per_m") or 0.0))
    return area, int(basic.get("phi") or 16), float(basic.get("spacing_mm") or 200.0)


def _laws(slab: SlabInput, settings: DesignSettings):
    pf = settings.partial_factors
    fyk = REINFORCEMENT_GRADES[settings.reinforcement.grade]
    return ConcreteLaw(concrete(slab.concrete).fck, pf.gamma_c, pf.alpha_cc), SteelLaw(fyk, pf.gamma_s)


def _xy(f: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Plan X, Y of the nodes (a corner berth's turned part keeps its plan position in PX, PY)."""
    if "PX" in f:
        return f["PX"].to_numpy(float), f["PY"].to_numpy(float)
    return f["X"].to_numpy(float), f["Y"].to_numpy(float)


def _extremes(f: pd.DataFrame, m: str, n: str) -> tuple[np.ndarray, np.ndarray]:
    """Per combination, the largest sagging and hogging moment per metre with its axial force."""
    ns, ms = [], []
    for _, g in f.groupby("combination"):
        for i in {g[m].idxmax(), g[m].idxmin()}:
            ns.append(float(g.at[i, n]))
            ms.append(float(g.at[i, m]))
    return np.array(ns), np.array(ms)


# --- Manholes -------------------------------------------------------------------------------------


def _pile_share(px: float, py: float, r: float, box: tuple[float, float, float, float], d: float) -> float:
    """Share of a pile's control perimeter cut off by an opening within 6d of it (EC2 6.4.2(3))."""
    x0, x1, y0, y1 = box
    dx = max(x0 - px, 0.0, px - x1)
    dy = max(y0 - py, 0.0, py - y1)
    if math.hypot(dx, dy) - r > 6 * d:
        return 0.0
    if x0 <= px <= x1 and y0 <= py <= y1:
        return 1.0
    ang = [math.atan2(y - py, x - px) for x in (x0, x1) for y in (y0, y1)]
    ref = ang[0]
    rel = [(a - ref + math.pi) % (2 * math.pi) - math.pi for a in ang]
    return min(1.0, (max(rel) - min(rel)) / (2 * math.pi))


def check_manhole(
    mh: Manhole,
    slab: SlabInput,
    settings: DesignSettings,
    uls: pd.DataFrame,
    qp: pd.DataFrame,
    layers: dict,
    punching: list[dict],
    size: tuple[float, float] | None = None,
) -> dict:
    h = slab.thickness
    sx, sy = size or (mh.size_x, mh.size_y)
    cov = max(slab.cover_top or 50.0, slab.cover_bottom or 50.0)
    conc = concrete(slab.concrete)
    laws = _laws(slab, settings)
    e_eff = conc.ecm / (1 + settings.cracking.creep_coefficient)
    limits = {"top": slab.crack_width_limit, "bottom": slab.crack_width_limit_bottom}
    lap = settings.piles.lap_factor
    pf = settings.partial_factors
    fywd = REINFORCEMENT_GRADES[settings.reinforcement.grade] / pf.gamma_s
    box = (mh.x - sx / 2000, mh.x + sx / 2000, mh.y - sy / 2000, mh.y + sy / 2000)
    ux, uy = _xy(uls)
    qx, qy = _xy(qp) if len(qp) else (np.zeros(0), np.zeros(0))
    dirs = {}
    utils = []
    notes = []
    for along in ("X", "Y"):
        # Bars along X are cut over the opening's Y size, and the other way round.
        a = sy if along == "X" else sx
        length = sx if along == "X" else sy
        w = max(h, a / 2)
        grow = 1 + a / (2 * w)
        if along == "X":
            sel_u = (np.abs(ux - mh.x) <= length / 2000 + h / 1000) & (
                np.abs(uy - mh.y) <= a / 2000 + w / 1000
            )
            sel_q = (np.abs(qx - mh.x) <= length / 2000 + h / 1000) & (
                np.abs(qy - mh.y) <= a / 2000 + w / 1000
            )
        else:
            sel_u = (np.abs(uy - mh.y) <= length / 2000 + h / 1000) & (
                np.abs(ux - mh.x) <= a / 2000 + w / 1000
            )
            sel_q = (np.abs(qy - mh.y) <= length / 2000 + h / 1000) & (
                np.abs(qx - mh.x) <= a / 2000 + w / 1000
            )
        fu, fq = uls[sel_u], qp[sel_q] if len(qp) else qp
        m, n, v = f"M{along.lower()}", f"N{along.lower()}", f"V{along.lower()}"
        if fu.empty:
            notes.append(f"No plate results round {mh.name}: it is outside this slab's results.")
            return {"name": mh.name, "passed": False, "utilisation": None, "notes": notes}
        un, um = _extremes(fu, m, n)
        qn, qm = _extremes(fq, m, n) if len(fq) else (np.zeros(0), np.zeros(0))
        key = along.lower()
        top = _bars_at(layers.get(f"top_{key}") or {}, *box)
        bot = _bars_at(layers.get(f"bottom_{key}") or {}, *box)
        strip = per_metre(
            t=h,
            n=un,
            m=um * grow,
            qn=qn,
            qm=qm * grow,
            cover=cov,
            limits=limits,
            settings=settings,
            laws=laws,
            conc=conc,
            e_eff=e_eff,
            start={"top": top[0], "bottom": bot[0]},
        )
        trims = {}
        for face, have in (("top", top), ("bottom", bot)):
            need = strip[face]["as_mm2_per_m"]
            area = max(have[0] * a / 2000, (need - have[0]) * w / 1000, 0.0)
            phi = max(MIN_PHI, have[1])
            count = max(2, math.ceil(area / (math.pi * phi**2 / 4) - 1e-9))
            trims[face] = {
                "count": count,
                "phi": phi,
                "area_mm2": round(area),
                "label": f"{count}Ø{phi} each side",
                "length_mm": round(length + 2 * lap * phi),
            }
        # Shear in the strips beside the opening.
        vmax = float(fu[v].abs().max()) * grow
        d = h - cov - top[1] / 2
        rho = min(top[0], bot[0]) / (1000 * d)
        nmin = float(fu[n].min())
        sig = min(max(nmin, 0.0) * 1e3 / (1000 * h), 0.2 * pf.alpha_cc * conc.fck / pf.gamma_c)
        vrdc = 0.0 if nmin < 0 else _vrdc(conc.fck, pf.gamma_c, 1000, d, rho, sig)
        shear: dict[str, Any] = {"V_kN_per_m": round(vmax, 1), "VRd_c_kN_per_m": round(vrdc, 1)}
        if vmax <= vrdc:
            shear["utilisation"] = round(vmax / vrdc, 3) if vrdc else 0.0
        else:
            need = vmax * 1e3 / (0.9 * d * fywd * 2.5)  # mm² per mm, per metre width
            shear["links_mm2_per_m2"] = round(need * 1000)
            shear["utilisation"] = None
            for phi in (12, 16, 20):
                s = math.floor(min(math.sqrt(math.pi * phi**2 / 4 * 1000 / need), 0.75 * d) / 25) * 25
                if s >= 100:
                    shear["links"] = f"Ø{phi} legs at {s:.0f} × {s:.0f} mm beside the opening"
                    shear["utilisation"] = round(
                        vmax / (math.pi * phi**2 / 4 * 1000 / s / s * 0.9 * d * fywd * 2.5 / 1e3), 3
                    )
                    break
            if shear["utilisation"] is None:
                shear["utilisation"] = 9.99
        shear["passed"] = shear["utilisation"] <= 1 + 1e-6
        dirs[along] = {
            "cut_width_mm": round(a),
            "strip_mm": round(w),
            "factor": round(grow, 3),
            "M_kNm_per_m": {"max": round(float(um.max()), 1), "min": round(float(um.min()), 1)},
            "existing": {"top": _lab(top), "bottom": _lab(bot)},
            "strip": strip,
            "trimmers": trims,
            "shear": shear,
            "passed": strip["passed"] and shear["passed"],
        }
        utils += [strip["utilisation"], shear["utilisation"]]
        utils += [c["wk"] / c["limit"] for c in strip["cracks"].values()]
    # Piles near the opening lose part of their punching perimeter.
    near = []
    for p in punching or []:
        d = p.get("d_mm") or h - cov
        share = _pile_share(p["x"], p["y"], (p.get("D_mm") or 0) / 2000, box, d / 1000)
        if share <= 0:
            continue
        if share >= 1:
            notes.append(f"{p.get('pile')} is inside the opening: move the opening off the pile head.")
        u0 = p.get("utilisation")
        u = None if u0 is None else (u0 / (1 - share) if share < 1 else math.inf)
        near.append(
            {
                "pile": p.get("pile"),
                "x": p["x"],
                "y": p["y"],
                "share": round(share, 3),
                "utilisation_before": u0,
                "utilisation": round(u, 3) if u is not None and math.isfinite(u) else None,
                "passed": u is not None and u <= 1 + 1e-6,
            }
        )
        utils.append(u if u is not None and math.isfinite(u) else 9.99)
    # A pit: its floor per metre under its own weight and the load in it, over the shorter side.
    pit = None
    if mh.depth is not None:
        t = h - mh.depth
        if t <= 0:
            notes.append(f"The pit ({mh.depth:.0f} mm) is as deep as the slab: taken as through the slab.")
        else:
            span = min(sx, sy) / 1000
            g = CONCRETE_WEIGHT * t / 1000
            qu, qq = GAMMA_G * g + GAMMA_Q * mh.floor_load, g + PSI2 * mh.floor_load
            bot = _bars_at(layers.get("bottom_x") or {}, *box)
            pit = per_metre(
                t=t,
                n=np.zeros(2),
                m=np.array([qu * span**2 / 8, -qu * span**2 / 12]),
                qn=np.zeros(2),
                qm=np.array([qq * span**2 / 8, -qq * span**2 / 12]),
                cover=cov,
                limits=limits,
                settings=settings,
                laws=laws,
                conc=conc,
                e_eff=e_eff,
                start={"bottom": bot[0]},
            )
            pit["span_m"] = round(span, 3)
            utils.append(pit["utilisation"])
    diag_phi = max(
        MIN_PHI,
        max(_bars_at(layers.get(k) or {}, *box)[1] for k in ("top_x", "top_y", "bottom_x", "bottom_y")),
    )
    finite = [u for u in utils if u is not None]
    passed = (
        all(d["passed"] for d in dirs.values())
        and all(p["passed"] for p in near)
        and (pit is None or pit["passed"])
    )
    return {
        "name": mh.name,
        "x": mh.x,
        "y": mh.y,
        "size_x_mm": round(sx),
        "size_y_mm": round(sy),
        "depth_mm": mh.depth,
        "through": mh.depth is None or mh.depth >= h,
        "thickness_mm": h,
        "directions": dirs,
        "piles": near,
        "pit": pit,
        "corners": {
            "diagonals": f"2Ø{diag_phi} top and 2Ø{diag_phi} bottom at each corner, "
            f"{lap * diag_phi:.0f} mm long",
            "phi": diag_phi,
            "length_mm": round(lap * diag_phi),
        },
        "utilisation": round(max(finite), 3) if finite else None,
        "passed": bool(passed),
        "notes": notes,
    }


def _lab(o: tuple[float, int, float]) -> dict:
    return {"as_mm2_per_m": round(o[0]), "phi": o[1]}


def design_manhole(mh: Manhole, slab, settings, uls, qp, layers, punching) -> dict:
    out = check_manhole(mh, slab, settings, uls, qp, layers, punching)
    if out["passed"] or out.get("directions") is None:
        return out
    # The largest opening that passes, both sides smaller in 50 mm steps.
    k_max = int(min(mh.size_x, mh.size_y) // STEP) - 1
    sizes = [(mh.size_x - k * STEP, mh.size_y - k * STEP) for k in range(k_max, 0, -1)]
    ok = [s for s in sizes if s[0] >= 300 and s[1] >= 300]
    if not ok or not check_manhole(mh, slab, settings, uls, qp, layers, punching, ok[0])["passed"]:
        out["suggestion"] = {
            "text": "No smaller opening (down to 300 mm) passes: the slab needs a thicker part or "
            "a beam round the opening."
        }
        return out
    lo, hi = 0, len(ok) - 1  # ok[0] passes; find the largest that passes
    if check_manhole(mh, slab, settings, uls, qp, layers, punching, ok[hi])["passed"]:
        lo = hi
    else:
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if check_manhole(mh, slab, settings, uls, qp, layers, punching, ok[mid])["passed"]:
                lo = mid
            else:
                hi = mid
    s = ok[lo]
    out["suggestion"] = {
        "size_x_mm": round(s[0]),
        "size_y_mm": round(s[1]),
        "text": f"An opening of {s[0]:.0f} × {s[1]:.0f} mm passes.",
    }
    return out


# --- Channels -------------------------------------------------------------------------------------


def design_channel(ch: Channel, slab: SlabInput, settings, uls, qp, layers, punching=()) -> dict:
    h = slab.thickness
    base = ch.base if ch.base is not None else h - ch.depth
    notes = []
    if base <= 0:
        return {
            "name": ch.name,
            "passed": False,
            "utilisation": None,
            "notes": [f"{ch.name} is {ch.depth:.0f} mm deep in a {h:.0f} mm slab: give it a base thickness."],
        }
    H = max(h, ch.depth + base)
    if ch.base is not None and ch.depth + base < h - 1:
        notes.append(
            f"The slab leaves {h - ch.depth:.0f} mm under the channel: that is used, not {base:.0f}."
        )
        base, H = h - ch.depth, h
    b = ch.width + 2 * ch.walls
    along, across = ch.direction, "Y" if ch.direction == "X" else "X"
    x, y = _xy(uls)
    s_u, t_u = (x, y) if along == "X" else (y, x)
    sel = (s_u >= ch.start - 1e-6) & (s_u <= ch.end + 1e-6) & (np.abs(t_u - ch.at) <= b / 2000 + h / 1000)
    fu = uls[sel]
    if fu.empty:
        return {
            "name": ch.name,
            "passed": False,
            "utilisation": None,
            "notes": [f"No plate results along {ch.name}: it is outside this slab's results."],
        }
    qx, qy = _xy(qp) if len(qp) else (np.zeros(0), np.zeros(0))
    s_q, t_q = (qx, qy) if along == "X" else (qy, qx)
    fq = (
        qp[(s_q >= ch.start - 1e-6) & (s_q <= ch.end + 1e-6) & (np.abs(t_q - ch.at) <= b / 2000 + h / 1000)]
        if len(qp)
        else qp
    )
    a, c = along.lower(), across.lower()
    width_m = b / 1000

    def along_rows(f: pd.DataFrame, sv: np.ndarray) -> pd.DataFrame:
        rows = pd.DataFrame(
            {
                "combination": f["combination"].to_numpy(),
                "s": np.round(sv / 0.25) * 0.25,
                "N": f[f"N{a}"].to_numpy(float) * width_m,
                "Mv": f[f"M{a}"].to_numpy(float) * width_m,
                "Mh": 0.0,
                "V": f[f"V{a}"].to_numpy(float) * width_m,
                "Vh": 0.0,
                "T": np.abs(f["Mxy"].to_numpy(float)) * width_m,
            }
        )
        # Per combination and station: the largest sagging and hogging row.
        keep = set()
        for _, g in rows.groupby(["combination", "s"]):
            keep.update({g["Mv"].idxmax(), g["Mv"].idxmin()})
        return rows.loc[sorted(keep)].reset_index(drop=True)

    def across_rows(f: pd.DataFrame, sv: np.ndarray) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "combination": f["combination"].to_numpy(),
                "s": sv,
                "N": f[f"N{c}"].to_numpy(float),
                "M": f[f"M{c}"].to_numpy(float),
                "V": f[f"V{c}"].to_numpy(float),
            }
        )

    su = s_u[sel]
    sq = (
        s_q[(s_q >= ch.start - 1e-6) & (s_q <= ch.end + 1e-6) & (np.abs(t_q - ch.at) <= b / 2000 + h / 1000)]
        if len(qp)
        else np.zeros(0)
    )
    # Bars the slab has along the channel, as rows across the strip.
    lo, hi = ch.at - b / 2000, ch.at + b / 2000
    bx = (ch.start, ch.end, lo, hi) if along == "X" else (lo, hi, ch.start, ch.end)
    top = _bars_at(layers.get(f"top_{a}") or {}, *bx)
    bot = _bars_at(layers.get(f"bottom_{a}") or {}, *bx)
    bot_c = _bars_at(layers.get(f"bottom_{c}") or {}, *bx)
    cov = max(slab.cover_top or 50.0, slab.cover_bottom or 50.0)
    link = 12.0

    def row(o, v):
        # The slab's bars over the strip's width, each at its own size.
        one = math.pi * o[1] ** 2 / 4
        n = max(2, math.ceil(o[0] * b / 1000 / one - 1e-6))
        half = b / 2 - cov - link - o[1] / 2
        return Bars(np.linspace(-half, half, n), np.full(n, v), np.full(n, one))

    cage = Bars.join(row(top, H / 2 - cov - link - top[1] / 2), row(bot, -H / 2 + cov + link + bot[1] / 2))
    conc = concrete(slab.concrete)
    laws = _laws(slab, settings)
    limits = {
        "top": slab.crack_width_limit,
        "bottom": slab.crack_width_limit_bottom,
        "side": slab.crack_width_limit,
    }
    ctx = Context(
        None,
        settings,
        cov,
        link,
        cage,
        top[1],
        bot[1],
        1,
        MIN_PHI,
        settings.piles.aggregate_size,
        laws,
        conc,
        conc.ecm / (1 + settings.cracking.creep_coefficient),
        limits,
        along_rows(fu, su),
        along_rows(fq, sq) if len(fq) else pd.DataFrame(columns=["combination", "s", "N", "Mv"]),
        across_rows(fu, su),
        across_rows(fq, sq) if len(fq) else pd.DataFrame(columns=["combination", "s", "N", "M", "V"]),
        bot_c[0],
    )
    room = BeamRoom(
        name=ch.name,
        start=ch.start,
        end=ch.end,
        height=ch.depth,
        width=ch.width,
        bottom=base,
        top=0.0,
        front_wall=ch.walls,
        floor_load=ch.floor_load,
    )
    shape, more = room_shape(room, b, H, 1)
    if shape is None:
        return {"name": ch.name, "passed": False, "utilisation": None, "notes": notes + more}
    res = design_room(ctx, room, shape, kind="channel")
    if H > h + 1:
        notes.append(f"The base hangs {H - h:.0f} mm below the slab soffit.")
    warnings = []
    for p in punching or []:
        r = (p.get("D_mm") or 0) / 2000
        s_p, t_p = (p["x"], p["y"]) if along == "X" else (p["y"], p["x"])
        if ch.start - r < s_p < ch.end + r and abs(t_p - ch.at) < b / 2000 + r:
            warnings.append(
                f"{p.get('pile')} (X {p['x']:g}, Y {p['y']:g}) is under the channel: its bars must anchor "
                f"in the {base:.0f} mm base and its punching is not checked through the channel. Detail it."
            )
    return {
        **res,
        "name": ch.name,
        "direction": along,
        "start_m": ch.start,
        "end_m": ch.end,
        "at_m": ch.at,
        "strip_mm": round(b),
        "length_m": round(ch.end - ch.start, 2),
        "extra_steel_kg": round(
            (
                res["bars"]["extra_kg_per_m"]
                + sum((x.get("link") or {}).get("kg_per_m", 0) for x in res["shear"].values())
            )
            * (ch.end - ch.start)
        ),
        "depth_total_mm": round(H),
        "downstand_mm": round(max(H - h, 0)),
        "existing": {"top": _lab(top), "bottom": _lab(bot), "across_bottom": _lab(bot_c)},
        "stations": int(np.unique(np.round(su / 0.25)).size),
        "warnings": warnings,
        "notes": notes + [n for n in res["notes"] if "beam" not in n],
    }


def design_slab_openings(
    slab: SlabInput, settings: DesignSettings, uls: pd.DataFrame, qp: pd.DataFrame, result: dict
) -> dict[str, list[dict]]:
    """Every manhole and channel of the slab (see the module's description). ``uls``/``qp``: the slab's
    node forces per metre (``slabs.slab_loads``)."""
    layers = result.get("layers") or {}
    out = {
        "manholes": [
            design_manhole(m, slab, settings, uls, qp, layers, result.get("punching") or [])
            for m in slab.manholes
        ],
        "channels": [
            design_channel(c, slab, settings, uls, qp, layers, result.get("punching") or [])
            for c in slab.channels
        ],
    }
    return out


def slab_sag(settings: DesignSettings, sign) -> float:
    return sag_factor(settings.plate_positive_moment, sign)[0]
