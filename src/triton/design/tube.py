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
) -> tuple[dict[str, Any], np.ndarray]:
    """Composite column buckling of the king pile (see the module notes); utilisation per load."""
    length = max(top - toe, 0.1)
    grid = np.linspace(top, toe, 201)
    gi = zone_index(zones, grid)
    filled = grid >= filled_from - 1e-9
    ic = math.pi / 64 * infill_diameter**4
    ac = math.pi / 4 * infill_diameter**2
    ei = np.array([E_STEEL * zones[k][2].i for k in gi]) + np.where(filled, KE * ecm * ic, 0.0)
    npl = np.array([zones[k][2].a_eff * zones[k][2].fy for k in gi]) + np.where(filled, 0.85 * ac * fck, 0.0)
    ei_avg, npl_avg = float(ei.mean()), float(npl.mean())  # N·mm², N
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
    if column is not None:
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
        "governing": governing,
        "profile": profile,
        "notes": list(dict.fromkeys(notes)),
    }


def _num(v: float, d: int) -> float | None:
    return round(float(v), d) if math.isfinite(v) else None
