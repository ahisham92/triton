"""Steel tube of the combi wall: EN 1993-1-1 section checks, shell buckling and column buckling.

Actions: the steel share of the Plaxis results, the E·I share where the tube is concrete filled
and everything below the infill; or, as an option, every action along the tube.

Two checks (per combi wall):

* Office sheets (default): elastic with class 4 effective properties wherever d/t > 90ε², filled
  or not: A_eff = A·√(90ε²/(d/t)), W_eff = W_el·(140ε²/(d/t))^0.25,
  σ = N/A_eff + M/W_eff ≤ fy/γM0, and shear V ≤ V_pl,Rd.
* EN 1993:

* Filled part: EN 1993-5 5.5.4(9) allows the full cross-sectional resistance, so
  the tube is checked plastically whatever its D/t (EN 1993-1-1 6.2).
* Unfilled part: class from EN 1993-1-1 Table 5.2 (d/t ≤ 50ε², 70ε², 90ε²).
  Class 1 and 2 are checked plastically, class 3 elastically, class 4 elastically
  plus meridional shell buckling to EN 1993-1-6 Annex D.1.2 (LS3, stress design),
  as EN 1993-5 5.5.4(7) refers tubes to EN 1993-1-6.

Plastic N–M for a thin tube: M_N,Rd = M_pl,Rd cos(π n / 2), n = N / N_pl,Rd.
Shear area A_v = 2A/π (EN 1993-1-1 6.2.6(3)(g)); above 0.5 V_pl,Rd the yield
strength is reduced by (1 − ρ), ρ = (2V/V_pl,Rd − 1)² (6.2.8).

Corrosion reduces the wall from the outside, and from the inside where a zone says so (below
the infill); zones down the tube each have their own loss.

Column buckling (both checks), composite column as the office sheets: over the length L from the
top level to the toe, EI_eff = Ea·Ia + Ke·Ecm·Ic (Ke = 0.6, the infill only where filled) and
N_pl,Rk = A_eff·fy + 0.85·Ac·fck are averaged along the length; N_cr = π²·EI_eff/(k·L)²,
λ = √(N_pl,Rk/N_cr), χ from the chosen curve (c: α = 0.49), N_b,Rd = χ·N_pl,Rk/γM1. Each point:
N_Ed/N_b,Rd + k_yy·M_Ed/M_c,Rd ≤ 1 with k_yy = C_my(1 + 0.6·λ·N_Ed/N_b,Rd) ≤ C_my(1 + 0.6·N_Ed/N_b,Rd),
C_my = 0.9 (EN 1993-1-1 Annex B, class 3 and 4), N_Ed the whole compression in the king pile and
M_Ed the tube's moment.

Sign: Plaxis N, compression negative.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from ..elements import CombinationType, combination_type
from ..importer import SheetData
from ..materials import structural_steel_fy

E_STEEL = 210_000.0  # MPa
GAMMA_M0 = 1.0
GAMMA_M1 = 1.1  # EN 1993-1-6 8.5.2 recommends at least 1.1
KE = 0.6  # EN 1994-1-1 6.7.3.3(3)
CMY = 0.9
CURVES = {"a": 0.21, "b": 0.34, "c": 0.49}
Q_FABRICATION = {"A": 40.0, "B": 25.0, "C": 16.0}  # EN 1993-1-6 Table D.1


@dataclass(frozen=True)
class Tube:
    diameter: float  # mm, as rolled
    thickness: float  # mm, as rolled
    corrosion: float  # mm, lost from the outside
    grade: str
    fabrication_class: str = "B"
    inside: float = 0.0  # mm, lost from the inside
    fy_set: float | None = None  # MPa, instead of the grade's value for the thickness
    name: str = ""

    @property
    def d(self) -> float:
        return self.diameter - 2 * self.corrosion

    @property
    def t(self) -> float:
        return self.thickness - self.corrosion - self.inside

    @property
    def i(self) -> float:
        di = self.d - 2 * self.t
        return math.pi / 64 * (self.d**4 - di**4)

    @property
    def _eps2(self) -> float:
        return 235 / self.fy

    @property
    def a_eff(self) -> float:
        """Class 4 effective area as the office sheets: A·√(90ε²/(d/t)); A for class 1 to 3."""
        ratio = self.d / self.t
        return self.area * min(1.0, math.sqrt(90 * self._eps2 / ratio))

    @property
    def w_eff(self) -> float:
        """Class 4 effective modulus as the office sheets: W_el·(140ε²/(d/t))^0.25, at most W_el."""
        ratio = self.d / self.t
        if ratio <= 90 * self._eps2:
            return self.w_el
        return self.w_el * min(1.0, (140 * self._eps2 / ratio) ** 0.25)

    @property
    def fy(self) -> float:
        if self.fy_set:
            return float(self.fy_set)
        return float(structural_steel_fy(self.grade, self.thickness))

    @property
    def area(self) -> float:
        di = self.d - 2 * self.t
        return math.pi / 4 * (self.d**2 - di**2)

    @property
    def w_el(self) -> float:
        di = self.d - 2 * self.t
        return math.pi / 32 * (self.d**4 - di**4) / self.d

    @property
    def w_pl(self) -> float:
        di = self.d - 2 * self.t
        return (self.d**3 - di**3) / 6

    @property
    def section_class(self) -> int:
        eps2 = 235 / self.fy
        ratio = self.d / self.t
        for cls, limit in ((1, 50), (2, 70), (3, 90)):
            if ratio <= limit * eps2:
                return cls
        return 4

    def buckling(self) -> dict[str, float]:
        """EN 1993-1-6 D.1.2: meridional buckling stress of a long cylinder in bending (C_x = 1)."""
        r = (self.d - self.t) / 2
        q = Q_FABRICATION[self.fabrication_class]
        dwk_t = math.sqrt(r / self.t) / q
        alpha = 0.62 / (1 + 1.91 * dwk_t**1.44)
        sigma_cr = 0.605 * E_STEEL * self.t / r
        lam = math.sqrt(self.fy / sigma_cr)
        lam0, beta, eta = 0.2, 0.6, 1.0
        lam_p = math.sqrt(alpha / (1 - beta))
        if lam <= lam0:
            chi = 1.0
        elif lam < lam_p:
            chi = 1 - beta * ((lam - lam0) / (lam_p - lam0)) ** eta
        else:
            chi = alpha / lam**2
        return {
            "r_over_t": round(r / self.t, 1),
            "alpha": round(alpha, 3),
            "sigma_cr_MPa": round(sigma_cr),
            "slenderness": round(lam, 3),
            "chi": round(chi, 3),
            "sigma_Rd_MPa": round(chi * self.fy / GAMMA_M1, 1),
        }

    def resistances(self, gamma_m0: float = GAMMA_M0) -> dict[str, float]:
        fy = self.fy
        return {
            "N_pl_kN": self.area * fy / gamma_m0 / 1e3,
            "M_pl_kNm": self.w_pl * fy / gamma_m0 / 1e6,
            "M_el_kNm": self.w_el * fy / gamma_m0 / 1e6,
            "N_eff_kN": self.a_eff * fy / gamma_m0 / 1e3,
            "M_eff_kNm": self.w_eff * fy / gamma_m0 / 1e6,
            "V_pl_kN": 2 * self.area / math.pi * fy / math.sqrt(3) / gamma_m0 / 1e3,
        }


def plastic_utilisation(
    n_kn: np.ndarray, m_knm: np.ndarray, v_kn: np.ndarray, tube: Tube, gamma_m0: float = GAMMA_M0
) -> np.ndarray:
    """Radial utilisation against M_pl cos(π n / 2), with the 6.2.8 shear reduction."""
    r = tube.resistances(gamma_m0)
    rho = np.where(v_kn > 0.5 * r["V_pl_kN"], (2 * v_kn / r["V_pl_kN"] - 1) ** 2, 0.0)
    k = np.clip(1 - rho, 1e-9, None)
    npl, mpl = r["N_pl_kN"] * k, r["M_pl_kNm"] * k
    n, m = np.abs(n_kn) / npl, np.abs(m_knm) / mpl
    # Solve λ·m = cos(π λ n / 2) for the load factor λ; utilisation is 1/λ.
    lo = np.zeros_like(n)
    hi = np.full_like(n, 1e6)
    np.divide(1.0, n, out=hi, where=n > 0)
    hi = np.minimum(hi, np.where(m > 0, 1 / np.maximum(m, 1e-12), 1e6))
    for _ in range(60):
        mid = (lo + hi) / 2
        ok = mid * m <= np.cos(np.pi * np.minimum(mid * n, 1.0) / 2) + 1e-12
        lo = np.where(ok, mid, lo)
        hi = np.where(ok, hi, mid)
    u = np.where(lo > 0, 1 / np.maximum(lo, 1e-12), np.inf)
    return np.where(v_kn >= r["V_pl_kN"], np.inf, u)


def tube_loads(
    sheets: dict[str, SheetData],
    steel_share: float,
    filled_from: float,
    top: float | None,
    above: float = 0.0,
):
    """ULS points with the tube's share of the actions (Plaxis sign, kN and kNm).

    Results up to ``above`` (m) over the top level are kept at their own level.
    """
    parts = []
    for combo, sheet in sheets.items():
        ctype = combination_type(combo)
        if ctype is CombinationType.SLS_QP:
            continue
        f = sheet.frame
        cols = [c for c in ("Node", "X", "Y", "Z", "N", "Q_12", "Q_13", "M_2", "M_3") if c in f.columns]
        f = f[cols].copy()
        if top is not None:
            f = f[f["Z"] <= top + above + 1e-9]
        filled = f["Z"] >= filled_from - 1e-9
        share = np.where(filled, steel_share, 1.0)
        f = f.assign(
            combination=combo,
            filled=filled,
            N_total=f["N"],
            **{c: f[c] * share for c in ("Q_12", "Q_13", "M_2", "M_3") if c in f.columns},
            N=f["N"] * share,
            V=np.hypot(f.get("Q_12", 0.0), f.get("Q_13", 0.0)) * share,
            M=np.hypot(f.get("M_2", 0.0), f.get("M_3", 0.0)) * share,
        )
        parts.append(f)
    if not parts:
        return pd.DataFrame(columns=["combination", "Node", "Z", "N", "V", "M", "filled"])
    return pd.concat(parts, ignore_index=True)


Zones = list[tuple[float, float, "Tube"]]  # (top, bottom, tube) down the king pile


def zone_index(zones: Zones, z: np.ndarray) -> np.ndarray:
    """Index of the corrosion zone of each level: the first zone from the top whose bottom is at or
    below it; the last zone carries on below its bottom."""
    idx = np.full(len(z), len(zones) - 1)
    for k in range(len(zones) - 1, -1, -1):
        idx = np.where(z >= zones[k][1] - 1e-9, k, idx)
    return idx


def column_buckling(
    zones: Zones,
    loads: pd.DataFrame,
    *,
    top: float,
    toe: float,
    filled_from: float,
    infill_diameter: float,
    fck: float,
    ecm: float,
    factor: float,
    curve: str,
    gamma_m0: float,
    gamma_m1: float,
    firm: float | None = None,
    column_ei: float | None = None,
) -> tuple[dict[str, Any], np.ndarray]:
    """Composite column buckling of the king pile (see the module notes); utilisation per load."""
    length = max(top - (toe if firm is None else max(firm, toe)), 0.1)
    grid = np.linspace(top, toe, 201)
    gi = zone_index(zones, grid)
    filled = grid >= filled_from - 1e-9
    ic = math.pi / 64 * infill_diameter**4
    ac = math.pi / 4 * infill_diameter**2
    ei = np.array([E_STEEL * zones[k][2].i for k in gi]) + np.where(filled, KE * ecm * ic, 0.0)
    npl = np.array([zones[k][2].a_eff * zones[k][2].fy for k in gi]) + np.where(filled, 0.85 * ac * fck, 0.0)
    ei_avg, npl_avg = float(ei.mean()), float(npl.mean())  # N·mm², N
    ei_avg = column_ei or ei_avg
    lcr = factor * length * 1e3
    ncr = math.pi**2 * ei_avg / lcr**2
    lam = math.sqrt(npl_avg / ncr)
    alpha = CURVES[curve]
    phi = 0.5 * (1 + alpha * (lam - 0.2) + lam**2)
    chi = min(1.0, 1 / (phi + math.sqrt(max(phi**2 - lam**2, 0.0))))
    nb_rd = chi * npl_avg / gamma_m1 / 1e3  # kN

    idx = zone_index(zones, loads["Z"].to_numpy(float))
    mc = np.array([zones[k][2].w_eff * zones[k][2].fy / gamma_m0 / 1e6 for k in idx])  # kNm
    n_ed = np.clip(-loads["N_total"].to_numpy(float), 0, None)
    r = n_ed / nb_rd
    kyy = CMY * (1 + 0.6 * np.minimum(lam, 1.0) * r)
    util = r + kyy * loads["M"].to_numpy(float) / mc
    info = {
        "length_m": round(length, 2),
        "buckling_length_m": round(lcr / 1e3, 2),
        "EI_eff_kNm2": round(ei_avg / 1e9),
        "N_cr_kN": round(ncr / 1e3),
        "N_pl_Rk_kN": round(npl_avg / 1e3),
        "slenderness": round(lam, 3),
        "curve": curve,
        "chi": round(chi, 4),
        "N_b_Rd_kN": round(nb_rd),
    }
    return info, util


def _office(
    zones: Zones, loads: pd.DataFrame, column: dict[str, Any], gamma_m0: float, gamma_m1: float
) -> tuple[dict, dict, np.ndarray, np.ndarray, dict | None]:
    """The office sheet per zone, from each zone's largest forces, and its checks at every result."""
    top, toe = column["top"], column["toe"]
    segs = segments(zones, top, toe, column["filled_from"])
    zi = zone_index(zones, loads["Z"].to_numpy(float))
    filled = loads["filled"].to_numpy(bool)
    where = {(sg.zone, sg.filled): j for j, sg in enumerate(segs)}
    seg_of = np.array(
        [where.get((int(k), bool(f)), where.get((int(k), not f), 0)) for k, f in zip(zi, filled, strict=True)]
    )
    n, v, m = (loads[c].to_numpy(float) for c in ("N", "V", "M"))
    nt = loads["N_total"].to_numpy(float) if "N_total" in loads.columns else n
    forces, combos = [], []
    for j in range(len(segs)):
        k = seg_of == j
        pick = lambda a, k=k: float(a[k].max()) if k.any() else 0.0  # noqa: E731
        forces.append(
            {
                "N": max(pick(-n), 0.0),
                "N_t": max(pick(n), 0.0),
                "V": pick(v),
                "M": pick(m),
                "N_total": max(pick(-nt), 0.0),
            }
        )
        combos.append(str(loads["combination"].to_numpy()[k][np.argmax(m[k])]) if k.any() else "")
    firm = column.get("firm")
    length = max(top - (toe if firm is None else max(firm, toe)), 0.1)
    sh = office_sheet(
        segs,
        forces,
        length=length,
        factor=column["factor"],
        infill_diameter=column["infill_diameter"],
        fck=column["fck"],
        ecm=column["ecm"],
        curve=column["curve"],
        column_ei=column.get("column_ei"),
        gamma_m0=gamma_m0,
        gamma_m1=gamma_m1,
    )
    cols = sh["columns"]
    # The same checks at every result, with its own N, V and M.
    get = lambda key: np.array([cols[j][key] for j in seg_of], float)  # noqa: E731
    cls = get("cls")
    n_c = np.clip(-n, 0, None)
    tau = v * 1e3 * get("S") / (get("I") * 2 * get("t"))
    u_tau = tau / (get("fy") / math.sqrt(3) / gamma_m0)
    u_m = m / get("M_Rd")
    kyy = CMY * (1 + 0.6 * np.minimum(get("lam"), 1.0) * n_c / get("Nb_Rd"))
    u_nm = np.clip(-nt, 0, None) / get("Nb_Rd") + kyy * u_m
    u_sig = np.where(cls >= 3, (np.abs(n) * 1e3 / get("A") + m * 1e6 / get("W_el")) / get("fy"), 0.0)
    v_pl = get("V_pl")
    rho = np.where(v >= 0.5 * v_pl, (2 * v / v_pl - 1) ** 2, 0.0)
    u_mv = np.where(rho > 0, u_m / np.clip(1 - rho, 1e-9, None), 0.0)
    stack = np.vstack([u_tau, u_nm, u_sig, u_mv])
    names = np.array(
        [
            "shear τ = V·S/(I·2t)",
            "column buckling N + M (office sheet)",
            "σ = N/A + M/Wel ≤ fy",
            "bending with shear",
        ]
    )
    u_more = stack.max(axis=0)
    why = names[stack.argmax(axis=0)]
    j = max(range(len(cols)), key=lambda q: cols[q]["u"]) if cols else None
    governs = None
    if j is not None:
        c = cols[j]
        governs = {
            "u": c["u"],
            "zone": c["seg"].name,
            "check": f"{c['check']} (zone envelope, as the office sheet)",
            "N": c["N"],
            "M": c["M"],
            "V": c["V"],
            "M_Rd": c["M_Rd"],
            "combination": combos[j],
        }
    b = min(cols, key=lambda c: c["Nb_Rd"]) if cols else None
    col = {
        "length_m": round(length, 2),
        "buckling_length_m": round(sh["Lcr_m"], 2),
        "EI_eff_kNm2": round(sh["EI_col"] / 1e9),
        "EI_from": sh["EI_from"],
        "N_cr_kN": round(sh["N_cr"] / 1e3),
        "N_pl_Rk_kN": round(sh["Npl_w"] / 1e3),
        "slenderness": round(b["lam"], 3) if b else None,
        "curve": column["curve"],
        "chi": round(b["chi"], 4) if b else None,
        "N_b_Rd_kN": round(b["Nb_Rd"]) if b else None,
        "utilisation": _num(max(c["u_NM"] for c in cols), 3) if cols else None,
    }
    return sheet_table(sh, column["ecm"], column["fck"]), col, u_more, why, governs


def check_tube(
    zones: Zones | Tube,
    loads: pd.DataFrame,
    *,
    method: str = "office",
    gamma_m0: float = GAMMA_M0,
    gamma_m1: float = GAMMA_M1,
    column: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Section checks per corrosion zone, and column buckling when ``column`` gives its inputs."""
    if isinstance(zones, Tube):
        zones = [(math.inf, -math.inf, zones)]
    notes = []
    if loads.empty:
        return {"utilisation": None, "passed": False, "notes": ["No ULS results."]}

    n, m, v = (loads[c].to_numpy(float) for c in ("N", "M", "V"))
    filled = loads["filled"].to_numpy(bool)
    zi = zone_index(zones, loads["Z"].to_numpy(float))
    u = np.zeros(len(loads))
    checks = np.empty(len(loads), dtype=object)
    shell_gm1 = max(gamma_m1, GAMMA_M1)
    for k, (_, _, tube) in enumerate(zones):
        mask = zi == k
        if not mask.any():
            continue
        r = tube.resistances(gamma_m0)
        cls = tube.section_class
        nk, mk, vk, fk = n[mask], m[mask], v[mask], filled[mask]
        u_shear = vk / r["V_pl_kN"]
        if method == "office":
            sigma = np.abs(nk) * 1e3 / tube.a_eff + mk * 1e6 / tube.w_eff
            u[mask] = np.maximum(sigma / (tube.fy / gamma_m0), u_shear)
            checks[mask] = "elastic, class 4 effective properties" if cls == 4 else f"elastic, class {cls}"
            continue
        u_plastic = plastic_utilisation(nk, mk, vk, tube, gamma_m0)
        sigma = np.abs(nk) * 1e3 / tube.area + mk * 1e6 / tube.w_el
        u_elastic = sigma / (tube.fy / gamma_m0)
        if cls <= 2:
            u_free, check_free = u_plastic, "plastic N–M (EN 1993-1-1 6.2)"
        else:
            u_free, check_free = np.maximum(u_elastic, u_shear), "elastic stress (EN 1993-1-1 6.2.1(7))"
        if cls == 4:
            b = tube.buckling()
            sigma_rd = b["chi"] * tube.fy / shell_gm1
            compression = -nk * 1e3 / tube.area + mk * 1e6 / tube.w_el
            u_free = np.maximum(u_free, np.clip(compression, 0, None) / sigma_rd)
            check_free = "shell buckling (EN 1993-1-6 D.1.2) and elastic stress"
            if not fk.all():
                notes.append(
                    f"Below the infill the corroded tube is class 4 (d/t = {tube.d / tube.t:.0f}), so local "
                    "buckling is checked to EN 1993-1-6 with fabrication quality class "
                    f"{tube.fabrication_class}."
                )
        u[mask] = np.where(fk, u_plastic, u_free)
        checks[mask] = np.where(fk, "plastic N–M, filled tube (EN 1993-5 5.5.4(9))", check_free)

    col = None
    sheet = None
    governs = None
    if column is not None and method == "office":
        sheet, col, u_more, why, governs = _office(zones, loads, column, gamma_m0, gamma_m1)
        checks = np.where(u_more > u, why, checks)
        u = np.maximum(u, u_more)
    elif column is not None:
        col, u_col = column_buckling(zones, loads, gamma_m0=gamma_m0, gamma_m1=gamma_m1, **column)
        col["utilisation"] = _num(float(np.nanmax(u_col)), 3)
        worse = u_col > u
        checks = np.where(worse, "column buckling (composite, N + M)", checks)
        u = np.maximum(u, u_col)
    loads = loads.assign(u=u)

    i = int(np.nanargmax(u))
    g = loads.iloc[i]
    tube = zones[int(zi[i])][2]
    r = tube.resistances(gamma_m0)
    governing = {
        "combination": str(g["combination"]),
        "node": int(g["Node"]) if "Node" in loads.columns and pd.notna(g["Node"]) else None,
        "z": round(float(g["Z"]), 2),
        "zone": "concrete filled" if bool(g["filled"]) else "steel only",
        "N_kN": round(float(g["N"])),
        "M_kNm": round(float(g["M"])),
        "V_kN": round(float(g["V"])),
        "M_Rd_kNm": round(r["M_eff_kNm"] if method == "office" else r["M_pl_kNm"]),
        "check": str(checks[i]),
    }
    prof = (
        loads.assign(z=(loads["Z"] * 2).round() / 2)  # 0.5 m bands
        .groupby("z")
        .agg(u=("u", "max"), M=("M", "max"), N=("N", "min"))
        .reset_index()
        .sort_values("z", ascending=False)
    )
    profile = [
        {"z": float(p.z), "util": _num(p.u, 3), "M_kNm": round(float(p.M)), "N_kN": round(float(p.N))}
        for p in prof.itertuples()
    ]
    notes.append(
        "Shell buckling under shear and the forces from the secondary sheet piles are not included yet."
    )
    first = zones[0][2]
    u_max = float(u[i])
    if governs is not None and governs["u"] >= u_max - 1e-9:
        # The sheet's zone forces are the zone's largest N, V and M together: at least any one result.
        u_max = governs["u"]
        governing.update(
            zone=governs["zone"],
            check=governs["check"],
            N_kN=round(-governs["N"]),
            M_kNm=round(governs["M"]),
            V_kN=round(governs["V"]),
            M_Rd_kNm=round(governs["M_Rd"]),
            combination=governs["combination"],
            envelope=True,
        )
    return {
        "utilisation": _num(u_max, 3),
        "passed": bool(u_max <= 1.0),
        "method": method,
        "section": {
            "diameter_mm": first.diameter,
            "thickness_mm": first.thickness,
            "corrosion_mm": first.corrosion,
            "corroded_diameter_mm": first.d,
            "corroded_thickness_mm": first.t,
            "grade": first.grade,
            "fy_MPa": first.fy,
            "class_unfilled": first.section_class,
            "d_over_t": round(first.d / first.t, 1),
            "area_mm2": round(first.area),
        },
        "zones": [
            {
                "top": None if not math.isfinite(t) else t,
                "bottom": None if not math.isfinite(bt) else bt,
                "outside_mm": z.corrosion,
                "inside_mm": z.inside,
                "t_mm": round(z.t, 2),
                "class": z.section_class,
                "A_eff_mm2": round(z.a_eff),
                "W_eff_cm3": round(z.w_eff / 1e3),
                "M_eff_kNm": round(z.resistances(gamma_m0)["M_eff_kNm"]),
                "M_pl_kNm": round(z.resistances(gamma_m0)["M_pl_kNm"]),
            }
            for t, bt, z in zones
        ],
        "resistances": {k: round(v) for k, v in r.items()},
        "buckling": first.buckling() if first.section_class == 4 and method != "office" else None,
        "column": col,
        "sheet": sheet,
        "governing": governing,
        "profile": profile,
        "notes": list(dict.fromkeys(notes)),
    }


def _num(v: float, d: int) -> float | None:
    return round(float(v), d) if math.isfinite(v) else None


# --- The office king pile sheet ("SECTION #1") --------------------------------------------------


@dataclass(frozen=True)
class Segment:
    """One column of the office sheet: a corrosion zone, split where the infill stops."""

    name: str
    top: float
    bottom: float
    tube: Tube
    filled: bool
    zone: int

    @property
    def length(self) -> float:
        return max(self.top - self.bottom, 0.0)


def segments(zones: Zones, top: float, toe: float, filled_from: float) -> list[Segment]:
    out = []
    for k, (zt, zb, tube) in enumerate(zones):
        hi, lo = min(zt, top), max(zb, toe) if k < len(zones) - 1 else toe
        if hi <= lo + 1e-9:
            continue
        cuts = [(hi, max(lo, filled_from), True), (min(hi, filled_from), lo, False)]
        parts = [(a, b, f) for a, b, f in cuts if a > b + 1e-9]
        for a, b, f in parts:
            name = tube.name or f"{_lvl(a)} to {_lvl(b)} m"
            if len(parts) > 1:
                name += " (filled)" if f else " (steel only)"
            out.append(Segment(name, a, b, tube, f, k))
    return out


def _lvl(z: float) -> str:
    return f"{z:+.2f}".rstrip("0").rstrip(".")


def _chi(lam: float, alpha: float) -> tuple[float, float]:
    phi = 0.5 * (1 + alpha * (lam - 0.2) + lam**2)
    return phi, min(1.0, 1 / (phi + math.sqrt(max(phi**2 - lam**2, 0.0))))


def office_sheet(
    segs: list[Segment],
    forces: list[dict[str, float]],
    *,
    length: float,
    factor: float,
    infill_diameter: float,
    fck: float,
    ecm: float,
    curve: str,
    column_ei: float | None,
    gamma_m0: float,
    gamma_m1: float,
) -> dict[str, Any]:
    """The office king pile sheet, one column per segment, from the segment's design forces.

    ``forces`` per segment: N (the tube's compression, kN), N_t (the tube's tension), V, M (kN, kNm)
    and N_total (the whole king pile's compression, composite column). ``column_ei`` in N·mm².
    Row by row as the sheet, with two rows added: the class 3/4 stress with effective properties
    and γM0 (EN 1993-1-1 6.2.1(7)) and the shear with γM0 as a utilisation.
    """
    ac = math.pi / 4 * infill_diameter**2
    ic = math.pi / 64 * infill_diameter**4
    cols = []
    for sg in segs:
        t = sg.tube
        fy, d, tw = t.fy, t.d, t.t
        di = d - 2 * tw
        eps2 = 235 / fy
        cls = t.section_class
        a, a_eff = t.area, t.a_eff
        w_el, w_eff, w_pl, i = t.w_el, t.w_eff, t.w_pl, t.i
        s1 = (d**3 - di**3) / 12
        av = 2 * a / math.pi
        ei = E_STEEL * i + (KE * ecm * ic if sg.filled else 0.0)
        npl_rk = a_eff * fy + (0.85 * ac * fck if sg.filled else 0.0)
        cols.append(
            {
                "seg": sg,
                "fy": fy,
                "d": d,
                "di": di,
                "t": tw,
                "eps": math.sqrt(eps2),
                "cls": cls,
                "A": a,
                "A_eff": a_eff,
                "I": i,
                "W_el": w_el,
                "W_eff": w_eff if cls == 4 else w_el,
                "W_pl": w_pl,
                "S": s1,
                "A_v": av,
                "EI": ei,
                "Npl_Rk": npl_rk,
            }
        )
    total = sum(c["seg"].length for c in cols) or 1.0
    npl_w = sum(c["Npl_Rk"] * c["seg"].length for c in cols) / total
    ei_avg = sum(c["EI"] * c["seg"].length for c in cols) / total
    ei_col = column_ei or ei_avg
    lcr = factor * length * 1e3
    ncr = math.pi**2 * ei_col / lcr**2
    alpha = CURVES[curve]
    for c, f in zip(cols, forces, strict=True):
        fy, cls = c["fy"], c["cls"]
        n = max(f["N"], f.get("N_t", 0.0))  # kN, the larger of compression and tension
        v, m, nc = f["V"], f["M"], f["N_total"]
        n_rd = (c["A_eff"] if cls == 4 else c["A"]) * fy / gamma_m0 / 1e3
        w_rd = {1: c["W_pl"], 2: c["W_pl"], 3: c["W_el"]}.get(cls, c["W_eff"])
        m_rd = w_rd * fy / gamma_m0 / 1e6
        v_pl = c["A_v"] * fy / math.sqrt(3) / gamma_m0 / 1e3
        tau = v * 1e3 * c["S"] / (c["I"] * 2 * c["t"])
        u_m = m / m_rd
        rho = (2 * v / v_pl - 1) ** 2 if v >= 0.5 * v_pl else None
        nn = n / n_rd
        mn_rd = m_rd * max(1 - nn**1.7, 0.0) if cls <= 2 else None
        sigma = n * 1e3 / c["A"] + m * 1e6 / c["W_el"]
        sigma_eff = n * 1e3 / (c["A_eff"] if cls == 4 else c["A"]) + m * 1e6 / c["W_eff"]
        lam = math.sqrt(c["Npl_Rk"] / ncr)
        phi, chi = _chi(lam, alpha)
        nb_rd = chi * npl_w / gamma_m1 / 1e3
        kyy = CMY * (1 + 0.6 * min(lam, 1.0) * f["N"] / nb_rd)
        c.update(
            N=f["N"],
            N_t=f.get("N_t", 0.0),
            V=v,
            M=m,
            N_total=nc,
            N_Rd=n_rd,
            u_N=n / n_rd,
            M_Rd=m_rd,
            u_M=u_m,
            tau=tau,
            u_tau=tau / (fy / math.sqrt(3) / gamma_m0),
            V_pl=v_pl,
            u_V=v / v_pl,
            rho=rho,
            u_MV=u_m / (1 - rho) if rho is not None and rho < 1 else (math.inf if rho is not None else None),
            n=nn,
            MN_Rd=mn_rd,
            u_MN=(m / mn_rd if mn_rd else math.inf) if mn_rd is not None else None,
            sigma=sigma,
            u_sigma=sigma / fy if cls >= 3 else None,
            sigma_eff=sigma_eff,
            u_sigma_eff=sigma_eff / (fy / gamma_m0) if cls >= 3 else None,
            lam=lam,
            phi=phi,
            chi=chi,
            Nb_Rd=nb_rd,
            u_Nb=nc / nb_rd,
            kyy=kyy,
            u_NM=nc / nb_rd + kyy * u_m,
        )
        checks = {
            "compression / tension": c["u_N"],
            "bending": u_m,
            "shear": c["u_tau"],
            "bending with shear": c["u_MV"],
            (
                "bending with axial force (plastic)"
                if cls <= 2
                else "N/Aeff + M/Weff ≤ fy/γM0 (EN 1993-1-1 6.2.1(7))"
            ): c["u_MN"] if cls <= 2 else c["u_sigma_eff"],
            "stress N/A + M/Wel (sheet)": c["u_sigma"],
            "column buckling N + M": c["u_NM"],
        }
        checks = {k: u for k, u in checks.items() if u is not None}
        c["check"] = max(checks, key=checks.get)
        c["u"] = checks[c["check"]]
    return {
        "columns": cols,
        "length_m": length,
        "Lcr_m": lcr / 1e3,
        "EI_col": ei_col,
        "EI_avg": ei_avg,
        "EI_from": "entered" if column_ei else "zones averaged over the length",
        "Npl_w": npl_w,
        "N_cr": ncr,
        "alpha": alpha,
    }


def sheet_table(sheet: dict[str, Any], ecm: float, fck: float) -> dict[str, Any]:
    """The office sheet as rows × zone columns for the results card and the report."""
    cols = sheet["columns"]

    def row(item: str, unit: str, key, fmt: str = "num", check: bool = False) -> dict[str, Any]:
        vals = [key(c) if callable(key) else c.get(key) for c in cols]
        out = []
        for v in vals:
            if v is None or isinstance(v, str):
                out.append(v)
            elif not math.isfinite(v):
                out.append(None)
            elif fmt == "pct":
                out.append(round(100 * v, 2))
            elif fmt == "sci":
                out.append(float(f"{v:.3e}"))
            else:
                out.append(round(v, 2))
        return {"item": item, "unit": unit, "values": out, "format": fmt, "check": check}

    t = lambda c: c["seg"].tube  # noqa: E731
    groups = [
        (
            "Pile parameters",
            [
                row("Inner pile (infill) diameter", "mm", lambda c: c["di"] + 2 * t(c).inside),
                row("Thickness", "mm", lambda c: t(c).thickness),
                row("Corrosion, outside", "mm", lambda c: t(c).corrosion),
                row("Corrosion, inside", "mm", lambda c: t(c).inside),
                row("Length of each segment", "m", lambda c: c["seg"].length),
                row("Levels", "m", lambda c: f"{_lvl(c['seg'].top)} to {_lvl(c['seg'].bottom)}", "text"),
                row("L, pile head to firm soil", "m", lambda c: sheet["length_m"]),
            ],
        ),
        (
            "Section class",
            [
                row("Effective thickness", "mm", "t"),
                row("Effective outer diameter", "mm", "d"),
                row("Effective inner diameter", "mm", "di"),
                row("d/t", "", lambda c: c["d"] / c["t"]),
                row("Yield strength fy", "MPa", "fy"),
                row("ε", "", "eps"),
                row("Class 1 limit 50ε²", "", lambda c: 50 * c["eps"] ** 2),
                row("Class 2 limit 70ε²", "", lambda c: 70 * c["eps"] ** 2),
                row("Class 3 limit 90ε²", "", lambda c: 90 * c["eps"] ** 2),
                row("Pile class", "", lambda c: f"Class {c['cls']}", "text"),
                row("Filled with concrete", "", lambda c: "Yes" if c["seg"].filled else "No", "text"),
            ],
        ),
        (
            "Action forces (tube)",
            [
                row("Compression force NEd", "kN", "N"),
                row("Tension force", "kN", "N_t"),
                row("Shear force VEd", "kN", "V"),
                row("Moment MEd", "kNm", "M"),
            ],
        ),
        (
            "Properties of area",
            [
                row("Area A", "mm²", "A"),
                row("Effective area Aeff = A·√(90ε²/(d/t))", "mm²", "A_eff"),
                row("Aeff / A", "%", lambda c: c["A_eff"] / c["A"], "pct"),
                row("Concrete area", "mm²", lambda c: math.pi / 4 * (c["di"] + 2 * t(c).inside) ** 2),
                row("Second moment of area I", "mm⁴", "I", "sci"),
                row("Plastic modulus Wpl", "mm³", "W_pl", "sci"),
                row("Elastic modulus Wel = I/(D/2)", "mm³", "W_el", "sci"),
                row("Effective modulus Weff = Wel·(140ε²/(d/t))^0.25", "mm³", "W_eff", "sci"),
                row("Weff / Wel", "%", lambda c: c["W_eff"] / c["W_el"], "pct"),
                row("S, first moment of half the tube", "mm³", "S", "sci"),
                row("Shear area Av = 2A/π", "mm²", "A_v"),
                row("τEd = VEd·S/(I·2t)", "MPa", "tau"),
            ],
        ),
        (
            "Section check",
            [
                row("NRd (A or Aeff)·fy/γM0", "kN", "N_Rd"),
                row("NEd / NRd", "%", "u_N", "pct", True),
                row("MRd (Wpl, Wel or Weff)·fy/γM0", "kNm", "M_Rd"),
                row("MEd / MRd", "%", "u_M", "pct", True),
                row("τEd / (fy/√3/γM0)", "%", "u_tau", "pct", True),
                row(
                    "MEd / MV,Rd (bending with shear)",
                    "%",
                    lambda c: "No interaction: VEd/Vpl,Rd < 0.5" if c["rho"] is None else c["u_MV"],
                    "pct",
                    True,
                ),
                row("n = NEd / NRd", "", "n"),
                row(
                    "MEd / MN,Rd, class 1 and 2",
                    "%",
                    lambda c: c["u_MN"] if c["cls"] <= 2 else "Not applicable",
                    "pct",
                    True,
                ),
                row("σ = N/A + M/Wel, class 3 and 4", "MPa", lambda c: c["sigma"] if c["cls"] >= 3 else None),
                row(
                    "σ / fy (as the sheet)",
                    "%",
                    lambda c: c["u_sigma"] if c["cls"] >= 3 else "Not applicable",
                    "pct",
                    True,
                ),
                row(
                    "N/Aeff + M/Weff ≤ fy/γM0 (EN 1993-1-1 6.2.1(7))",
                    "%",
                    lambda c: c["u_sigma_eff"] if c["cls"] >= 3 else "Not applicable",
                    "pct",
                    True,
                ),
            ],
        ),
        (
            "Buckling check",
            [
                row("Ecm", "MPa", lambda c: ecm if c["seg"].filled else "N/A", "num"),
                row("Ke", "", lambda c: KE if c["seg"].filled else "N/A"),
                row(
                    "I concrete",
                    "mm⁴",
                    lambda c: math.pi / 64 * (c["di"] + 2 * t(c).inside) ** 4 if c["seg"].filled else "N/A",
                    "sci",
                ),
                row("Total compression on the composite section", "kN", "N_total"),
                row("Lcr = factor × L", "m", lambda c: sheet["Lcr_m"]),
                row("EIeff = Ea·Ia + Ke·Ecm·Ic", "N·mm²", "EI", "sci"),
                row(
                    f"EI of the whole column ({sheet['EI_from']})", "N·mm²", lambda c: sheet["EI_col"], "sci"
                ),
                row(f"Npl,Rk = Aeff·fy + 0.85·Ac·fck (fck {fck:g})", "kN", lambda c: c["Npl_Rk"] / 1e3),
                row("Npl,Rk of the whole column, length-weighted", "kN", lambda c: sheet["Npl_w"] / 1e3),
                row("Ncr = π²·EI/Lcr²", "kN", lambda c: sheet["N_cr"] / 1e3),
                row("λ = √(Npl,Rk / Ncr)", "", "lam"),
                row("α", "", lambda c: sheet["alpha"]),
                row("Φ = 0.5·(1 + α(λ − 0.2) + λ²)", "", "phi"),
                row("χ = 1/(Φ + √(Φ² − λ²))", "", "chi"),
                row("Nb,Rd = χ·Npl,Rk,whole/γM1", "kN", "Nb_Rd"),
                row("Nc / Nb,Rd", "%", "u_Nb", "pct", True),
                row("MEd / MRd", "%", "u_M", "pct", True),
                row("Cmy", "", lambda c: CMY),
                row("kyy = Cmy·(1 + 0.6·λ·NEd/Nb,Rd)", "", "kyy"),
                row("Interaction Nc/Nb,Rd + kyy·MEd/MRd", "%", "u_NM", "pct", True),
            ],
        ),
        (
            "Result",
            [
                row("Governing check", "", "check", "text"),
                row("Utilisation", "%", "u", "pct", True),
            ],
        ),
    ]
    return {
        "columns": [c["seg"].name for c in cols],
        "groups": [{"title": g, "rows": rows} for g, rows in groups],
    }
