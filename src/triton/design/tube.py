"""Steel tube of the combi wall: EN 1993-1-1 section checks and EN 1993-1-6 shell buckling.

Forces are the steel share of the Plaxis results (see ``forces.split_combi_wall``):
the E·I share where the tube is concrete filled, everything below the infill.

* Filled part: EN 1993-5 5.5.4(9) allows the full cross-sectional resistance, so
  the tube is checked plastically whatever its D/t (EN 1993-1-1 6.2).
* Unfilled part: class from EN 1993-1-1 Table 5.2 (d/t ≤ 50ε², 70ε², 90ε²).
  Class 1 and 2 are checked plastically, class 3 elastically, class 4 elastically
  plus meridional shell buckling to EN 1993-1-6 Annex D.1.2 (LS3, stress design),
  as EN 1993-5 5.5.4(7) refers tubes to EN 1993-1-6.

Plastic N–M for a thin tube: M_N,Rd = M_pl,Rd cos(π n / 2), n = N / N_pl,Rd.
Shear area A_v = 2A/π (EN 1993-1-1 6.2.6(3)(g)); above 0.5 V_pl,Rd the yield
strength is reduced by (1 − ρ), ρ = (2V/V_pl,Rd − 1)² (6.2.8).

Corrosion reduces the wall from the outside. Sign: Plaxis N, compression negative.
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
Q_FABRICATION = {"A": 40.0, "B": 25.0, "C": 16.0}  # EN 1993-1-6 Table D.1


@dataclass(frozen=True)
class Tube:
    diameter: float  # mm, as rolled
    thickness: float  # mm, as rolled
    corrosion: float  # mm, lost from the outside
    grade: str
    fabrication_class: str = "B"

    @property
    def d(self) -> float:
        return self.diameter - 2 * self.corrosion

    @property
    def t(self) -> float:
        return self.thickness - self.corrosion

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

    def resistances(self) -> dict[str, float]:
        fy = self.fy
        return {
            "N_pl_kN": self.area * fy / GAMMA_M0 / 1e3,
            "M_pl_kNm": self.w_pl * fy / GAMMA_M0 / 1e6,
            "M_el_kNm": self.w_el * fy / GAMMA_M0 / 1e6,
            "V_pl_kN": 2 * self.area / math.pi * fy / math.sqrt(3) / GAMMA_M0 / 1e3,
        }


def plastic_utilisation(n_kn: np.ndarray, m_knm: np.ndarray, v_kn: np.ndarray, tube: Tube) -> np.ndarray:
    """Radial utilisation against M_pl cos(π n / 2), with the 6.2.8 shear reduction."""
    r = tube.resistances()
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

    Results up to ``above`` (m) over the top level are kept and taken at the top level.
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
            f = f.assign(Z=f["Z"].clip(upper=top))
        filled = f["Z"] >= filled_from - 1e-9
        share = np.where(filled, steel_share, 1.0)
        f = f.assign(
            combination=combo,
            filled=filled,
            **{c: f[c] * share for c in ("Q_12", "Q_13", "M_2", "M_3") if c in f.columns},
            N=f["N"] * share,
            V=np.hypot(f.get("Q_12", 0.0), f.get("Q_13", 0.0)) * share,
            M=np.hypot(f.get("M_2", 0.0), f.get("M_3", 0.0)) * share,
        )
        parts.append(f)
    if not parts:
        return pd.DataFrame(columns=["combination", "Node", "Z", "N", "V", "M", "filled"])
    return pd.concat(parts, ignore_index=True)


def check_tube(tube: Tube, loads: pd.DataFrame) -> dict[str, Any]:
    r = tube.resistances()
    cls = tube.section_class
    buck = tube.buckling() if cls == 4 else None
    notes = []
    if loads.empty:
        return {"utilisation": None, "passed": False, "notes": ["No ULS results."]}

    n, m, v = (loads[c].to_numpy(float) for c in ("N", "M", "V"))
    filled = loads["filled"].to_numpy(bool)
    u_plastic = plastic_utilisation(n, m, v, tube)
    sigma = np.abs(n) * 1e3 / tube.area + m * 1e6 / tube.w_el  # MPa, extreme fibre
    u_elastic = sigma / (tube.fy / GAMMA_M0)
    u_shear = v / r["V_pl_kN"]
    if cls <= 2:
        u_free, check_free = u_plastic, "plastic N–M (EN 1993-1-1 6.2)"
    else:
        u_free, check_free = np.maximum(u_elastic, u_shear), "elastic stress (EN 1993-1-1 6.2.1(7))"
    if buck is not None:
        compression = -n * 1e3 / tube.area + m * 1e6 / tube.w_el  # compression +
        u_buck = np.clip(compression, 0, None) / buck["sigma_Rd_MPa"]
        u_free = np.maximum(u_free, u_buck)
        check_free = "shell buckling (EN 1993-1-6 D.1.2) and elastic stress"
    u = np.where(filled, u_plastic, u_free)
    loads = loads.assign(u=u)

    i = int(np.nanargmax(u))
    g = loads.iloc[i]
    governing = {
        "combination": str(g["combination"]),
        "node": int(g["Node"]) if "Node" in loads.columns and pd.notna(g["Node"]) else None,
        "z": round(float(g["Z"]), 2),
        "zone": "concrete filled" if bool(g["filled"]) else "steel only",
        "N_kN": round(float(g["N"])),
        "M_kNm": round(float(g["M"])),
        "V_kN": round(float(g["V"])),
        "check": "plastic N–M, filled tube (EN 1993-5 5.5.4(9))" if bool(g["filled"]) else check_free,
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
    if cls == 4 and not filled.all():
        notes.append(
            f"Below the infill the corroded tube is class 4 (d/t = {tube.d / tube.t:.0f}), so local "
            f"buckling is checked to EN 1993-1-6 with fabrication quality class {tube.fabrication_class}."
        )
    notes.append(
        "Shell buckling under shear and the forces from the secondary sheet piles are not included yet."
    )
    u_max = float(u[i])
    return {
        "utilisation": _num(u_max, 3),
        "passed": bool(u_max <= 1.0),
        "section": {
            "diameter_mm": tube.diameter,
            "thickness_mm": tube.thickness,
            "corrosion_mm": tube.corrosion,
            "corroded_diameter_mm": tube.d,
            "corroded_thickness_mm": tube.t,
            "grade": tube.grade,
            "fy_MPa": tube.fy,
            "class_unfilled": cls,
            "d_over_t": round(tube.d / tube.t, 1),
            "area_mm2": round(tube.area),
        },
        "resistances": {k: round(v) for k, v in r.items()},
        "buckling": buck,
        "governing": governing,
        "profile": profile,
        "notes": notes,
    }


def _num(v: float, d: int) -> float | None:
    return round(float(v), d) if math.isfinite(v) else None
