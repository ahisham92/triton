"""Steel sheet pile sections checked to EN 1993-5, the way ArcelorMittal Durability 4.2.1 does it.

Durability's Part B technical manual (section 3.1.2) sets out the checks; each gives a
utilisation factor Uf and the level's Uf is the largest of them:

1. Bending, EN 1993-5 5.2.2 (1)(2): Mc,Rd = W fy / γM0, W = Wpl for class 1 and 2
   (unless "Wel only"), Wel for class 3. Class 4 is taken as class 3 with
   fy,red = 235 (k + 0.5)² tf² / b² (k = 66, Z-piles), EN 1993-1-1 5.5.2 (9); Durability classes
   the rounded (b / tf) / ε, hence the 0.5. The reduced fy is used for shear too.
2. Bending and shear, 5.2.2 (3)(4)(5)(8)(9): Vpl,Rd = Av fy / (√3 γM0) with
   Av = (h − tf) tw / bs per metre; above 0.5 Vpl,Rd the moment resistance drops to
   MV,Rd = [W − ρ (Av bd)² / (4 (2 tw) sin α bd)] fy / γM0, ρ = (2 VEd / Vpl,Rd − 1)².
3. Web shear buckling, 5.2.2 (6)(7), only when c / tw > 72 ε: Vb,Rd = (h − tf) tw fbv / (γM0 bs),
   fbv from EN 1993-1-3 Table 6.1 (web without stiffening at the support),
   λw = 0.346 (c / tw) √(fy / E).
4. Buckling, 5.2.3 (1)-(4): none when NEd / Ncr ≤ 0.04 (Uf = NEd / Npl,Rd); otherwise
   NEd / (χ Npl,Rd) + 1.15 MEd / Mc,Rd ≤ γM0 / γM1, χ from curve d (α = 0.76),
   Ncr = E I βD π² / l².
5. Bending and axial force, 5.2.3 (9)-(11): no interaction while NEd / Npl,Rd ≤ k1 (0.10 for
   Z-piles), else MN,Rd = k2 Mc,Rd (1 − NEd / Npl,Rd) ≤ Mc,Rd with k2 = 1.
6. Bending, shear and axial force, 5.2.3 (12): when both N exceeds k1 Npl,Rd and VEd > 0.5 Vpl,Rd,
   fy is reduced to (1 − ρ) fy over the shear area.

EN 1993-5 5.2.4 (differential water head over 5 m) reduces fy for bending by ρP, Table 5.2,
unless the interlocks are welded.

Section properties are the ArcelorMittal catalogue values per metre of wall. Corrosion takes
the total loss (front + back) off every plate: flanges and webs get thinner and the height drops
by the same amount, as Durability shows for its reduced sections. Each section is idealised as a
thin-walled Z (two flanges, two inclined webs and the interlock mass at the flanges per double
pile) that reproduces the catalogue area and inertia exactly. Where Triton holds the real web
angle (AZ 14-770, from the office's Durability run) the flange follows from it and the
interlocks' lever is fitted; otherwise the flange width is fitted. The corroded properties are
the catalogue values scaled by the idealised section's ratio (Wel in proportion to I, as
Durability), which reproduces Durability's corroded AZ 14-770 within 0.5 %. The flange width
and the web angle can be given instead.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import cache
from typing import Any

import numpy as np

E_STEEL = 210_000.0  # MPa
ALPHA_D = 0.76  # imperfection factor, buckling curve d

# ArcelorMittal AZ sections (Z-type), per metre of wall unless noted: width of a single pile b (mm),
# height h, flange and web thicknesses, area (cm²/m), inertia (cm⁴/m), Wel and Wpl (cm³/m), and
# the class the catalogue gives. Source: ArcelorMittal "Z-sections" 2021 table.
_AZ = """
AZ 18-800        800 449 8.5 8.5   129 41320 1840 2135 3
AZ 20-800        800 450 9.5 9.5   141 45050 2000 2330 3
AZ 22-800        800 451 10.5 10.5 153 48790 2165 2525 3
AZ 23-800        800 474 11.5 9.0  151 55260 2330 2680 3
AZ 25-800        800 475 12.5 10.0 163 59410 2500 2890 3
AZ 27-800        800 476 13.5 11.0 176 63570 2670 3100 2
AZ 28-750        750 509 12.0 10.0 171 71540 2810 3245 3
AZ 30-750        750 510 13.0 11.0 185 76670 3005 3485 3
AZ 32-750        750 511 14.0 12.0 198 81800 3200 3720 2
AZ 12-770        770 344 8.5 8.5   120 21430 1245 1480 3
AZ 13-770        770 344 9.0 9.0   126 22360 1300 1546 3
AZ 14-770        770 345 9.5 9.5   131.6 23300 1355 1611 3
AZ 14-770-10/10  770 345 10.0 10.0 137 24240 1405 1677 3
AZ 12-700        700 314 8.5 8.5   123 18880 1205 1415 3
AZ 13-700        700 315 9.5 9.5   135 20540 1305 1540 3
AZ 13-700-10/10  700 316 10.0 10.0 140 21370 1355 1600 3
AZ 14-700        700 316 10.5 10.5 146 22190 1405 1665 3
AZ 17-700        700 420 8.5 8.5   133 36230 1730 2027 3
AZ 18-700        700 420 9.0 9.0   139 37800 1800 2116 3
AZ 19-700        700 421 9.5 9.5   146 39380 1870 2206 3
AZ 20-700        700 421 10.0 10.0 152 40960 1945 2296 3
AZ 24-700        700 459 11.2 11.2 174 55820 2430 2867 3
AZ 26-700        700 460 12.2 12.2 187 59720 2600 3070 2
AZ 28-700        700 461 13.2 13.2 200 63620 2760 3273 2
AZ 36-700N       700 499 15.0 11.2 216 89610 3590 4110 2
AZ 38-700N       700 500 16.0 12.2 230 94840 3795 4360 2
AZ 40-700N       700 501 17.0 13.2 244 100080 3995 4605 2
AZ 42-700N       700 499 18.0 14.0 259 104930 4205 4855 2
AZ 44-700N       700 500 19.0 15.0 273 110150 4405 5105 2
AZ 46-700N       700 501 20.0 16.0 287 115370 4605 5350 2
AZ 48-700        700 503 22.0 15.0 288 119650 4755 5490 2
AZ 50-700        700 504 23.0 16.0 303 124890 4955 5735 2
AZ 52-700        700 505 24.0 17.0 317 130140 5155 5985 2
AZ 18            630 380 9.5 9.5   150 34200 1800 2104 3
AZ 18-10/10      630 381 10.0 10.0 157 35540 1870 2189 3
AZ 26            630 427 13.0 12.2 198 55510 2600 3059 2
"""

DEFAULT_SECTION = "AZ 14-770"

# Real flange width and web angle, where known (the office's Durability 4.2.1 Sheet pile tab).
_GEOMETRY = {"AZ 14-770": (351.0, 39.5)}


@dataclass(frozen=True)
class Section:
    name: str
    width: float  # single pile, mm
    h: float  # mm
    tf: float
    tw: float
    area: float  # cm²/m
    inertia: float  # cm⁴/m
    wel: float  # cm³/m
    wpl: float  # cm³/m
    catalogue_class: int
    b: float | None = None  # flange width that classifies, mm, where Triton has the real one
    alpha: float | None = None  # web angle, degrees, likewise

    @property
    def mass(self) -> float:
        """kg/m² of wall."""
        return round(self.area * 0.785, 1)


def _parse() -> dict[str, Section]:
    out = {}
    for line in _AZ.strip().splitlines():
        *name, b, h, tf, tw, a, i, wel, wpl, cls = line.split()
        n = " ".join(name)
        out[n] = Section(n, *map(float, (b, h, tf, tw, a, i, wel, wpl)), int(cls), *_GEOMETRY.get(n, ()))
    return out


SECTIONS = _parse()
SECTION_NAMES = tuple(SECTIONS)


def normalise(name: str | None) -> str:
    """'AZ14-770', 'az 14 - 770' -> 'AZ 14-770'; empty -> the default section."""
    if not name or not name.strip():
        return DEFAULT_SECTION
    key = "".join(name.upper().split())
    for n in SECTIONS:
        if "".join(n.split()) == key:
            return n
    return name.strip()


def section(name: str) -> Section:
    try:
        return SECTIONS[normalise(name)]
    except KeyError:
        raise ValueError(f"'{name}' is not an AZ section Triton knows.") from None


# --- The idealised thin-walled section -------------------------------------------------------


# Interlock metal lost per mm of plate loss (mm² per interlock), for the area and, since it sits
# nearer the neutral axis than the interlock's centre, the equivalent for inertia and Wpl. Fitted to
# Durability's corroded AZ 14-770 (A and I within 0.3 % at 2.5 to 4.5 mm loss).
LUMP_LOSS_AREA = 25.0
LUMP_LOSS_LEVER = 10.0


def _thin(s: Section, bf: float, ai: float, loss: np.ndarray | float, lever: float = 1.0) -> dict[str, Any]:
    """Area (mm²/mm), inertia (mm⁴/mm), plastic modulus (mm³/mm) of the idealised double pile
    per mm of wall, with every plate ``loss`` mm thinner and the height ``loss`` mm lower (the
    plate centre lines stay put). ``lever``: the interlocks' distance from the neutral axis as a
    share of the flanges'."""
    loss = np.asarray(loss, dtype=float)
    p = 2 * s.width
    tf, tw, h = s.tf - loss, s.tw - loss, s.h - loss
    w = s.width - bf  # horizontal run of a web
    rise = h - tf
    web = np.hypot(rise, w)
    lump_a = np.maximum(ai - LUMP_LOSS_AREA * loss, 0.0)
    lump_i = np.maximum(ai - LUMP_LOSS_LEVER * loss, 0.0)
    d = rise / 2
    area = (2 * bf * tf + 2 * web * tw + 2 * lump_a) / p
    inertia = (
        2 * bf * tf * d**2 + 2 * lump_i * (lever * d) ** 2 + 2 * bf * tf**3 / 12 + 2 * tw * web * rise**2 / 12
    ) / p
    wpl = (2 * bf * tf * d + 2 * lump_i * lever * d + 2 * tw * web * rise / 4) / p
    return {"area": area, "inertia": inertia, "wpl": wpl, "h": h}


def _bisect(err, lo: float, hi: float) -> float:
    flo = err(lo)
    for _ in range(80):
        mid = (lo + hi) / 2
        fm = err(mid)
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
    return (lo + hi) / 2


@cache
def idealised(
    name: str, flange: float | None = None, angle: float | None = None
) -> tuple[float, float, float]:
    """Flange width (mm, centre line), interlock area (mm², one interlock) and interlock lever of
    the idealised section that reproduces the catalogue area and inertia. With the web angle known
    (given, or the real one Triton holds), the flange follows from it and the lever is fitted;
    with the flange given, only the interlock area is fitted; otherwise the flange is fitted."""
    s = section(name)
    angle = angle or s.alpha

    def lump(bf: float) -> float:
        web = math.hypot(s.h - s.tf, s.width - bf)
        return (s.area * 0.1 * 2 * s.width - 2 * bf * s.tf - 2 * web * s.tw) / 2

    def inertia(bf: float, lever: float = 1.0) -> float:
        return float(_thin(s, bf, lump(bf), 0.0, lever)["inertia"]) * 0.1 - s.inertia

    if flange is not None:
        return float(flange), max(lump(float(flange)), 0.0), 1.0
    if angle:
        bf = s.width - (s.h - s.tf) / math.tan(math.radians(angle))
        lever = _bisect(lambda k: inertia(bf, k), 0.3, 1.5)
        return round(bf, 1), round(lump(bf), 1), round(lever, 4)
    bf = _bisect(inertia, 50.0, s.width - 50.0)
    return round(bf, 1), round(lump(bf), 1), 1.0


def web_angle(name: str, flange: float | None = None) -> float:
    s = section(name)
    if s.alpha and flange is None:
        return s.alpha
    bf, _, _ = idealised(name, flange)
    return math.degrees(math.atan2(s.h - s.tf, s.width - bf))


def reduced(
    name: str, loss: np.ndarray | float, flange: float | None = None, angle: float | None = None
) -> dict[str, Any]:
    """Section properties per metre with ``loss`` mm (front + back) taken off every plate.
    Units: mm, cm²/m, cm⁴/m, cm³/m; Av in cm²/m; b is the flange width used to classify."""
    s = section(name)
    bf, ai, lever = idealised(name, flange, angle)
    base = _thin(s, bf, ai, 0.0, lever)
    now = _thin(s, bf, ai, loss, lever)
    loss = np.asarray(loss, dtype=float)
    tf, tw, h = s.tf - loss, s.tw - loss, s.h - loss
    inertia = s.inertia * now["inertia"] / base["inertia"]
    alpha = math.radians(angle or s.alpha or math.degrees(math.atan2(s.h - s.tf, s.width - bf)))
    return {
        "tf": tf,
        "tw": tw,
        "h": h,
        # The flange width that classifies: given, the catalogue's, or the idealised centre line.
        "b": flange or s.b or bf,
        "alpha": math.degrees(alpha),
        "c": (s.h - s.tf) / math.sin(alpha),  # slant height of the web (Z-piles), as Durability
        "area": s.area * now["area"] / base["area"],
        "inertia": inertia,
        # Wel in proportion to I, as Durability's reduced sections.
        "wel": s.wel * inertia / s.inertia,
        "wpl": s.wpl * now["wpl"] / base["wpl"],
        "av": (h - tf) * tw / s.width * 10,  # (h − tf) tw per web, one web per pile width
    }


# --- Checks ------------------------------------------------------------------------------------

# EN 1993-5 Table 5.2: ρP against the differential head w (m) and (b / tmin) / ε.
_RHO_W = (5.0, 10.0, 15.0, 20.0)
_RHO_R = (20.0, 30.0, 40.0, 50.0)
_RHO = (
    (1.00, 1.00, 1.00, 1.00),
    (0.99, 0.97, 0.95, 0.87),
    (0.98, 0.96, 0.92, 0.76),
    (0.98, 0.94, 0.88, 0.60),
)


def rho_p(head: float, slenderness: float) -> float:
    """ρP for Z-piles (EN 1993-5 Table 5.2), interpolated; heads up to 5 m give 1.0. Beyond the
    table the last row or column is used."""
    if head <= 5.0:
        return 1.0
    w = min(head, _RHO_W[-1])
    r = min(max(slenderness, _RHO_R[0]), _RHO_R[-1])
    rows = [float(np.interp(r, _RHO_R, row)) for row in _RHO]
    return round(float(np.interp(w, _RHO_W, rows)), 3)


def fbv(lam: np.ndarray, fy: float) -> np.ndarray:
    """EN 1993-1-3 Table 6.1, web without stiffening at the support."""
    return np.where(lam <= 0.83, 0.58 * fy, np.where(lam < 1.40, 0.48 * fy / lam, 0.67 * fy / lam**2))


@dataclass
class Options:
    fy: float = 355.0
    gamma_m0: float = 1.0
    gamma_m1: float = 1.1
    buckling_length: float = 10.0  # m
    wel_only: bool = False
    class_floor: int = 1  # never better than this class (the catalogue's, by default)
    head: float = 0.0  # differential water head, m
    welded: bool = False
    flange: float | None = None
    angle: float | None = None
    # U-piles (for checking against Durability's worked examples): class limits 37 / 49, k = 49,
    # k1 = 0.25 and k2 = 1.33 for class 1 and 2, βB on the moduli and βD on the inertia.
    kind: str = "Z"
    beta_b: float = 1.0
    beta_d: float = 1.0


_PLATES = ("tf", "tw", "h", "area", "inertia", "wel", "wpl", "av")
CHECKS = ("bending", "bending_shear", "web_buckling", "buckling", "bending_axial", "bending_shear_axial")
CHECK_TITLES = {
    "bending": "Bending",
    "bending_shear": "Bending & shear",
    "web_buckling": "Web shear buckling",
    "buckling": "Buckling",
    "bending_axial": "Bending & axial",
    "bending_shear_axial": "Bending, shear & axial",
}


def check(
    name: str, opts: Options, M: np.ndarray, V: np.ndarray, N: np.ndarray, loss: np.ndarray
) -> dict[str, np.ndarray]:
    """EN 1993-5 checks of every row. ``M``, ``V``: magnitudes per metre (kNm/m, kN/m; M includes
    N e); ``N``: kN/m, compression positive; ``loss``: total corrosion (mm) at the row.

    Returns arrays: one Uf per check (NaN where the check is not needed), the governing Uf, and
    the intermediate values the report prints."""
    loss = np.asarray(loss, float)
    p = reduced(name, loss, opts.flange, opts.angle)
    return evaluate(p, section(name).width, opts, M, V, N)


def evaluate(
    p: dict[str, Any], bs: float, opts: Options, M: np.ndarray, V: np.ndarray, N: np.ndarray
) -> dict[str, np.ndarray]:
    """The checks of ``check`` for given (reduced) properties ``p`` (see ``reduced``) and single
    pile width ``bs`` (mm)."""
    M = np.abs(np.asarray(M, float))
    V = np.abs(np.asarray(V, float))
    N = np.asarray(N, float)
    shape = np.broadcast(M, V, N).shape
    M, V, N = (np.broadcast_to(x, shape).astype(float) for x in (M, V, N))
    p = {k: (np.broadcast_to(v, shape).astype(float) if k in _PLATES else v) for k, v in p.items()}
    u = opts.kind == "U"
    lim2, lim3, k = (37.0, 49.0, 49.0) if u else (45.0, 66.0, 66.0)
    fy, g0, g1 = opts.fy, opts.gamma_m0, opts.gamma_m1
    eps = math.sqrt(235.0 / fy)
    slender = p["b"] / p["tf"] / eps
    # Durability classifies the rounded (b / tf) / ε, so a class holds up to its limit + 0.5.
    cls = np.where(slender < lim2 + 0.5, 2, np.where(slender < lim3 + 0.5, 3, 4))
    cls = np.maximum(cls, opts.class_floor)
    # Class 4: Durability takes class 3 with fy,red = 235 (k + 0.5)² tf² / b² (k = 66 for Z-piles),
    # the fy that brings (b / tf) / ε down to the class 3 limit as it rounds.
    fy_cls = np.where(cls == 4, np.minimum(fy, 235.0 * (k + 0.5) ** 2 * p["tf"] ** 2 / p["b"] ** 2), fy)
    rho_water = (
        1.0 if opts.welded else rho_p(opts.head, float(np.max(p["b"] / np.minimum(p["tf"], p["tw"]) / eps)))
    )
    plastic = (cls <= 2) & (not opts.wel_only)
    W = opts.beta_b * np.where(plastic, p["wpl"], p["wel"])  # cm³/m
    Mc = W * fy_cls * rho_water / g0 / 1000  # kNm/m
    A = p["area"]
    Npl = A * fy_cls / g0 / 10  # kN/m
    Av = p["av"]
    # Durability takes the class 4 reduced fy for shear too (its AU 14 example: Vpl,Rd = 906 kN/m).
    Vpl = Av * fy_cls / math.sqrt(3) / g0 / 10  # kN/m

    out: dict[str, np.ndarray] = {}
    out["bending"] = M / Mc
    # Bending and shear.
    rho = np.where(V > 0.5 * Vpl, (2 * V / Vpl - 1) ** 2, 0.0)
    sin_a = math.sin(math.radians(p["alpha"]))
    # Per metre: ρ Av² / (4 tw sin α) per web, two webs per double pile of width 2 bs.
    av_web = Av * 100 * bs / 1000  # mm² per web
    loss_w = rho * av_web**2 / (4 * p["tw"] * sin_a) / bs  # mm³/mm = cm³/m
    Mv = np.minimum((W - loss_w) * fy_cls * rho_water / g0 / 1000, Mc)
    out["bending_shear"] = np.where(V > 0.5 * Vpl, np.maximum(V / Vpl, M / np.maximum(Mv, 1e-9)), V / Vpl)
    # Web shear buckling.
    c_tw = p["c"] / p["tw"]
    eps_w = np.sqrt(235.0 / fy_cls)
    needs = c_tw > 72 * eps_w
    lam = 0.346 * c_tw * np.sqrt(fy_cls / E_STEEL)
    Vb = (p["h"] - p["tf"]) * p["tw"] * fbv(lam, fy_cls) / g0 / bs  # N/mm = kN/m
    out["web_buckling"] = np.where(needs, V / Vb, np.nan)
    # Buckling (compression only).
    comp = np.maximum(N, 0.0)
    Ncr = E_STEEL * p["inertia"] * opts.beta_d * 1e-5 * math.pi**2 / opts.buckling_length**2  # kN/m
    lam_b = np.sqrt(Npl * g0 / Ncr)
    phi = 0.5 * (1 + ALPHA_D * (lam_b - 0.2) + lam_b**2)
    chi = np.minimum(1.0, 1 / (phi + np.sqrt(np.maximum(phi**2 - lam_b**2, 0.0))))
    inter = (comp / (chi * Npl) + 1.15 * M / Mc) / (g0 / g1)
    out["buckling"] = np.where(
        comp <= 0, np.nan, np.where(comp / Ncr <= 0.04, comp / Npl, np.maximum(comp / Npl, inter))
    )
    # Bending and axial force (tension too: EN 1993-5 5.2.3 (9) speaks of any axial force).
    n_abs = np.abs(N)
    k1 = np.where(u & (cls <= 2), 0.25, 0.10)
    k2 = np.where(u & (cls <= 2), 1.33, 1.0)
    Mn = np.minimum(k2 * Mc * (1 - n_abs / Npl), Mc)
    big_n = n_abs / Npl > k1
    # Once N reaches Npl,Rd there is no moment resistance left: Uf is N / Npl,Rd (≥ 1) then.
    n_ratio = n_abs / Npl
    with_m = np.where(n_ratio < 1, np.maximum(n_ratio, M / np.maximum(Mn, 1e-9)), n_ratio)
    out["bending_axial"] = np.where(n_abs <= 0, np.nan, np.where(big_n, with_m, n_ratio))
    # Bending, shear and axial force: fy (1 − ρ) over the shear area.
    both = big_n & (V > 0.5 * Vpl)
    npl_red = np.maximum(A - rho * Av, 1e-9) * fy_cls / g0 / 10
    n_red = n_abs / npl_red
    mn_red = np.maximum(np.minimum(k2 * Mv * (1 - n_red), Mv), 1e-9)
    out["bending_shear_axial"] = np.where(
        both, np.where(n_red < 1, np.maximum(n_red, M / mn_red), n_red), np.nan
    )
    stacked = np.vstack([np.nan_to_num(out[k], nan=0.0) for k in CHECKS])
    out["uf"] = stacked.max(axis=0)
    out["governs"] = stacked.argmax(axis=0)
    out.update(
        {
            "class": cls,
            "fy_used": fy_cls,
            "rho_p": np.full_like(M, rho_water),
            "W": W,
            "Mc": Mc,
            "Mv": Mv,
            "Vpl": Vpl,
            "Vb": np.where(needs, Vb, np.nan),
            "c_tw_eps": c_tw / eps_w,
            "Npl": Npl,
            "Ncr": np.broadcast_to(Ncr, M.shape).astype(float),
            "chi": chi,
            "Mn": Mn,
            "slender": slender,
        }
    )
    for k in ("tf", "tw", "h", "area", "inertia", "wel", "wpl", "av"):
        out[k] = np.broadcast_to(p[k], M.shape).astype(float)
    return out
