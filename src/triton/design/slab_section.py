"""A 1 m slab strip as AdSec takes it: strain compatibility with the parabola-rectangle concrete, the
steel law of the setting (500B strain hardening by default), the tension bars and the other face's bars in
compression; and the crack width of 7.3.4 at the outermost tension bar, from a cracked elastic section
of every bar, with AdSec's local effective area round that bar.

Units: mm, N/mm², kN/m and kNm/m. N compression +. Depths are from the compressed face.
"""

from __future__ import annotations

import math

import numpy as np

from .circular import SteelLaw

E_S = 200_000.0
EPS_C2, EPS_CU2 = 0.002, 0.0035  # fck <= 50 MPa
K1, K3, K4, KT = 0.8, 3.4, 0.425, 0.4
ITER = 60


def _block(eps_t: np.ndarray, x: np.ndarray, fcd: float) -> tuple[np.ndarray, np.ndarray]:
    """Parabola-rectangle force (N per mm of width) over depth x with top strain eps_t, and its depth."""
    eta = np.clip(eps_t / EPS_C2, 1e-12, None)
    low = eta <= 1
    s0 = np.where(low, 1.0, 1 / eta)
    cf = np.where(low, eta - eta**2 / 3, 1 - s0 / 3)
    mf = np.where(low, 2 * eta / 3 - eta**2 / 4, 0.5 - s0**2 / 12)
    xx = np.clip(x, 0.0, None)
    return fcd * xx * cf, xx - xx * mf / cf


def _state(t: np.ndarray, d: float, steel: SteelLaw) -> tuple[np.ndarray, np.ndarray]:
    """(top strain, x) along the ULS strain planes, t 0..2: pivot on the bar strain limit (0..1), then on
    the concrete strain limit with x from there to d (1..2). Without a bar strain limit only the second."""
    eud = steel.eps_ud
    if not eud:
        x = np.clip(t - 1, 1e-6, 1) * d
        return np.full_like(t, EPS_CU2), x
    x_lim = d * EPS_CU2 / (EPS_CU2 + eud)
    a = np.clip(t, 0, 1)
    ec = np.where(t < 1, a * EPS_CU2, EPS_CU2)
    x = np.where(t < 1, d * ec / (ec + eud), x_lim + np.clip(t - 1, 0, 1) * (d - x_lim))
    return np.maximum(ec, 1e-9), x


def _forces(t, d, other, fcd, steel):
    """Concrete force and depth, the other face's bars' force and its moment about the tension bars,
    the tension bars' stress and strain."""
    ec, x = _state(t, d, steel)
    x = np.maximum(x, 1e-6)
    c, a = _block(ec, x, fcd)
    c = c * 1000
    eps_s = ec * (d - x) / x
    f2 = np.zeros_like(c)
    m2 = np.zeros_like(c)
    for area, depth in other:
        f = area * steel.stress(ec * (x - depth) / x)
        f2, m2 = f2 + f, m2 + f * (d - depth)
    return c, a, f2, m2, steel.stress(eps_s), eps_s


def required_as(m, n, h: float, d: float, fcd: float, steel: SteelLaw, other: list[tuple[float, float]]):
    """Tension steel (mm²/m) at depth d for |M| with N, the other face's bars ``other`` ((mm²/m, depth)
    each) working with their own strain.

    NaN where this does not apply (the resultant outside the tension bars, or the bars would not yield
    at the needed depth of compression): the caller keeps its own answer there."""
    m = np.abs(np.asarray(m, float)) * 1e6
    n = np.asarray(n, float) * 1e3
    ms = m + n * (d - h / 2)
    lo, hi = np.zeros_like(ms), np.full_like(ms, 2.0)

    def moment(t):
        c, a, _, m2, _, _ = _forces(t, d, other, fcd, steel)
        return c * (d - a) + m2

    for _ in range(ITER):
        mid = (lo + hi) / 2
        up = moment(mid) < ms
        lo, hi = np.where(up, mid, lo), np.where(up, hi, mid)
    t = (lo + hi) / 2
    c, _, f2, _, fs, eps_s = _forces(t, d, other, fcd, steel)
    a_s = (c + f2 - n) / np.maximum(fs, 1e-9)
    bad = (ms <= 0) | (moment(np.full_like(ms, 2.0)) < ms) | (eps_s < steel.fyd / steel.es)
    return np.where(bad, np.nan, np.maximum(a_s, 0.0))


def mrd(
    layers: list[tuple[float, float]],
    h: float,
    n: float,
    fcd: float,
    steel: SteelLaw,
    other: list[tuple[float, float]],
) -> float:
    """Moment capacity (kNm/m) about mid-depth with N (kN/m), the tension face's ``layers`` and the other
    face's ``other`` ((mm²/m, depth) each), every bar at its own strain; NaN where x = d cannot hold N."""
    area = sum(a for a, _ in layers)
    d = max(dd for _, dd in layers)  # the strain limit is at the furthest bar
    both = [(a, dd) for a, dd in layers if dd < d] + list(other)
    area_d = sum(a for a, dd in layers if dd >= d)
    nn = n * 1e3
    lo, hi = np.zeros(1), np.full(1, 2.0)

    def resid(t):
        c, _, f2, _, fs, _ = _forces(t, d, both, fcd, steel)
        return c + f2 - area_d * fs - nn

    if resid(np.zeros(1))[0] > 0:  # more tension than the bars can take
        return 0.0
    if resid(np.full(1, 2.0))[0] < 0:
        return float("nan")
    for _ in range(ITER):
        mid = (lo + hi) / 2
        up = resid(mid) < 0
        lo, hi = np.where(up, mid, lo), np.where(up, hi, mid)
    t = (lo + hi) / 2
    c, a, f2, m2, fs, _ = _forces(t, d, both, fcd, steel)
    # About mid-depth: m2 is about the tension bars, so move it by f2·(d − h/2).
    m = c * (h / 2 - a) + (m2 - f2 * (d - h / 2)) + area_d * fs * (d - h / 2)
    return float(max(m[0], 0.0) / 1e6) if area > 0 else 0.0


def crack_widths(
    m, n, layers: list[tuple], h: float, other: list[tuple[float, float]], conc, e_eff: float, terms=False
):
    """7.3.4 crack widths (mm) at the outermost tension bar, as AdSec, for M (kNm/m, + where it puts the
    ``layers`` face in tension) and N (kN/m).

    ``layers``: the tension face's layers (mm²/m, depth from the compressed face, Ø, spacing), outermost
    first. The bar stress comes from a cracked elastic section of every bar (concrete E ``e_eff``, no
    tension); ρp,eff is one outer bar over its own concrete, the bar spacing by hc,ef = the least of
    2.5(h − d), (h − x)/3, h/2 and half-way to the next layer, less the bar; k2 from the strains (7.13).
    As AdSec, x is that of the strain plane interpolated between the uncracked and cracked ones (7.18).
    With ``terms`` (wk, σs, sr,max, x)."""
    m = np.asarray(m, float) * 1e6
    n = np.asarray(n, float) * 1e3
    a_i = np.array([lay[0] for lay in layers] + [o[0] for o in other])
    d_i = np.array([lay[1] for lay in layers] + [o[1] for o in other])
    b = 1000.0

    def ab(x):
        xc = np.clip(x, 0, None)
        xs = x[:, None]
        big_a = e_eff * b * xc**2 / 2 + (E_S * a_i * (xs - d_i)).sum(axis=1)
        big_b = e_eff * b * xc**2 / 2 * (h / 2 - xc / 3) + (E_S * a_i * (xs - d_i) * (h / 2 - d_i)).sum(
            axis=1
        )
        return big_a, big_b

    def g(q):  # x = h - h/q: q 0..1 puts x from -inf to 0 (all in tension), q > 1 inside the section
        big_a, big_b = ab(h - h / q)
        return (n * big_b - m * big_a) * q

    lo, hi = np.full_like(m, -12.0), np.full_like(m, 12.0)  # log q
    g_hi = g(np.exp(hi))
    for _ in range(ITER):
        mid = (lo + hi) / 2
        g_mid = g(np.exp(mid))
        same = np.sign(g_mid) == np.sign(g_hi)
        hi, lo = np.where(same, mid, hi), np.where(same, lo, mid)
        g_hi = np.where(same, g_mid, g_hi)
    x = np.minimum(h - h / np.exp((lo + hi) / 2), h)
    big_a, big_b = ab(x)
    with np.errstate(divide="ignore", invalid="ignore"):
        # M = κB and N = κA at the root: the least-squares κ of both, as either can be nil.
        kappa = (m * big_b + n * big_a) / (big_b**2 + big_a**2)
    kappa = np.nan_to_num(kappa)
    a_out, d_out, phi, s = layers[0]
    sigma = np.maximum(E_S * kappa * (d_out - x), 0.0)
    e_c, e_t = _interpolated(m, n, x, kappa, a_i, d_i, h, e_eff, conc.fctm)
    # k2 (7.13) from the face strains; where a face is in compression, x is its compressed depth.
    both = e_c > 0
    with np.errstate(divide="ignore", invalid="ignore"):
        k2 = np.where(both, (e_t + e_c) / (2 * np.maximum(e_t, e_c)), 0.5)
        x = np.where(both, -np.inf, h * -e_c / (e_t - e_c))
    hc = np.minimum(2.5 * (h - d_out), h / 2)
    hc = np.where(np.isfinite(x), np.minimum(hc, (h - x) / 3), hc)
    if len(layers) > 1:
        hc = np.minimum(hc, h - (d_out + layers[1][1]) / 2)
    bar = math.pi * phi**2 / 4
    rho = bar / np.maximum(s * hc - bar, 1.0)
    ae = E_S / conc.ecm
    strain = np.maximum((sigma - KT * conc.fctm / rho * (1 + ae * rho)) / E_S, 0.6 * sigma / E_S)
    c = h - d_out - phi / 2
    sr = np.where(
        s > 5 * (c + phi / 2), 1.3 * (h - np.where(np.isfinite(x), x, 0.0)), K3 * c + K1 * k2 * K4 * phi / rho
    )
    wk = sr * strain
    if terms:
        return wk, sigma, sr, x
    return wk


def _interpolated(m, n, x2, k2, a_i, d_i, h, e_c, fctm):
    """Strains (tension +) at the compressed and the tension face of the plane ζ·cracked +
    (1 − ζ)·uncracked, ζ = 1 − 0.5(σsr/σs)² ≥ 0 (7.19), σsr/σs the load share at first cracking.
    Depths from the compressed face."""
    b = 1000.0
    y = d_i - h / 2
    # Uncracked: -N = EA·e0 + ES·κ, M = ES·e0 + EI·κ about mid-depth.
    ea = e_c * b * h + (E_S * a_i).sum()
    es = (E_S * a_i * y).sum()
    ei = e_c * b * h**3 / 12 + (E_S * a_i * y**2).sum()
    det = ea * ei - es * es
    e0 = (-n * ei - es * m) / det
    k1 = (ea * m + es * n) / det
    most = e_c * (e0 + np.abs(k1) * h / 2)  # the most stressed concrete
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(most > 0, fctm / most, np.inf)
    zeta = np.clip(1 - 0.5 * ratio**2, 0.0, 1.0)

    def at(yy):
        return zeta * k2 * (yy - x2) + (1 - zeta) * (e0 + k1 * (yy - h / 2))

    return at(0.0), at(h)
