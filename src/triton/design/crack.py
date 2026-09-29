"""Crack widths to EN 1992-1-1 7.3.4, and cracking from restrained temperature and shrinkage.

Loads (7.3.4):

    wk = sr,max · (εsm − εcm)
    εsm − εcm = max[(σs − kt·fct,eff/ρp,eff·(1 + αe·ρp,eff)) / Es, 0.6·σs/Es]
    sr,max = k3·c + k1·k2·k4·φ/ρp,eff, or 1.3·(h − x) when the bars are further apart than 5(c + φ/2)

with kt = 0.4 (long-term), k1 = 0.8, k2 = 0.5 in bending and (ε1 + ε2)/2ε1 in tension, k3 = 3.4,
k4 = 0.425, fct,eff = fctm, and hc,ef = min(2.5(h − d), (h − x)/3, h/2) (7.3.2(3)).

Restraint (EN 1992-3 Annex M, CIRIA C660): the free contraction of early-age cooling T1, the
seasonal drop T2 and autogenous shrinkage, times the restraint factor R, less half the
concrete's tensile strain capacity, is taken up in cracks at sr,max:

    εr = R·(α·(T1 + T2) + εca),  wk = sr,max · (εr − 0.5·εctu)

R at the base of a member cast against an older one (edge restraint) follows ACI 207.2R
from the length to height ratio L/H between joints:

    L/H >= 2.5:  R = ((L/H − 2)/(L/H + 1))^(H/L)
    L/H <  2.5:  R = ((L/H − 1)/(L/H + 10))^(H/L)
"""

from __future__ import annotations

from .stop import checkpoint

E_S = 200_000.0  # MPa
K1, K3, K4, KT = 0.8, 3.4, 0.425, 0.4


def crack_width(
    sigma_s: float,
    *,
    h: float,
    d: float,
    x: float,
    b: float,
    area: float,
    phi: float,
    cover: float,
    spacing: float,
    fctm: float,
    ecm: float,
    k2: float = 0.5,
) -> dict:
    """wk (mm) at a face from the tension stress σs (MPa, > 0) in its bars.

    h, d, x in mm on the face's side of the section: d to the face's bars, x the compressed depth
    (0 when the section is wholly in tension). ``area`` is the face's bars (mm²) over width b (mm),
    ``cover`` is to the bars, ``spacing`` between their centres.
    """
    checkpoint()
    if sigma_s <= 0:
        return {"wk": 0.0, "sigma_s": round(max(sigma_s, 0.0), 1), "sr_max": None, "rho_eff": None}
    hc = min(2.5 * (h - d), (h - x) / 3 if x > 0 else h / 2, h / 2)
    rho = area / (b * hc)
    ae = E_S / ecm
    strain = max((sigma_s - KT * fctm / rho * (1 + ae * rho)) / E_S, 0.6 * sigma_s / E_S)
    if spacing > 5 * (cover + phi / 2):
        sr = 1.3 * (h - x)
    else:
        sr = K3 * cover + K1 * k2 * K4 * phi / rho
    return {
        "wk": round(sr * strain, 3),
        "sigma_s": round(sigma_s, 1),
        "sr_max": round(sr),
        "rho_eff": round(rho, 4),
        "hc_eff": round(hc),
    }


def restraint_factor(length: float, height: float) -> float:
    """Edge restraint at the base (ACI 207.2R), from the length between joints and the height (m)."""
    r = length / height
    if r <= 1:
        return 0.0
    base = (r - 2) / (r + 1) if r >= 2.5 else (r - 1) / (r + 10)
    return max(0.0, base) ** (1 / r)


def autogenous_shrinkage(fck: float) -> float:
    """EN 1992-1-1 3.1.4(6), final value: 2.5·(fck − 10)·10⁻⁶."""
    return 2.5 * (fck - 10) * 1e-6


def restraint_crack(
    *,
    restraint: float,
    t1: float,
    t2: float,
    alpha: float,
    eps_ca: float,
    fctm: float,
    ecm: float,
    area: float,
    b: float,
    hc: float,
    phi: float,
    cover: float,
    spacing: float,
    member: float,
) -> dict:
    """Crack width (mm) from restrained contraction at one face.

    ``area`` is the face's bars over width b with an effective depth hc (all mm); ``member`` is the
    member depth used when the bars are far apart (1.3·h for sr,max).
    """
    eps_r = restraint * (alpha * (t1 + t2) + eps_ca)
    eps_ctu = fctm / ecm
    eps_cr = max(eps_r - 0.5 * eps_ctu, 0.0)
    rho = area / (b * hc)
    if spacing > 5 * (cover + phi / 2):
        sr = 1.3 * member
    else:
        sr = K3 * cover + K1 * 1.0 * K4 * phi / rho
    return {
        "wk": round(sr * eps_cr, 3),
        "eps_r": round(eps_r * 1e6),
        "eps_cr": round(eps_cr * 1e6),
        "sr_max": round(sr),
        "rho_eff": round(rho, 4),
    }


def as_min_restraint(fctm: float, act: float, h: float, fyk: float = 500.0) -> float:
    """7.3.2(2) minimum steel (mm²) over the tension area act (mm²) in pure tension (kc = 1)."""
    k = 1.0 if h <= 300 else 0.65 if h >= 800 else 1.0 - 0.35 * (h - 300) / 500
    return 1.0 * k * fctm * act / fyk
