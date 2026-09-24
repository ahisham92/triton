"""Over-reinforced sections: the ductility of slabs and beams at their bending capacity.

At the moment capacity the concrete reaches εcu2 = 0.0035 at the compression face (fck <= 50 MPa).
With the neutral axis at depth x and the furthest tension bars at depth d:

* EN 1992-1-1 5.5(4), with no redistribution, keeps x/d <= (1 − k1)/k2 = (1 − 0.44)/1.25 = 0.448, the
  limit that K' = 0.167 is based on: beyond it the section is flagged as short of ductility;
* the tension bars yield only while εs = εcu2·(d − x)/x >= fyd/Es (x/d <= 0.617 for B500): beyond it the
  section is over-reinforced, the concrete crushes before the bars yield;
* 9.2.1.1(3): the steel of a section at most 4% of the concrete outside laps.

Slabs take the rectangular block 0.8x at fcd on a 1 m strip, the tension bars of the face at fyd and
the other face's bars in the same direction as compression steel at the stress of their strain, under
the row's N (compression +). Beams take their whole cage (``rect.RectSection.ductility``)
under the largest ULS compression along the beam, for sagging and for hogging.
"""

from __future__ import annotations

ECU = 0.0035
X_D_LIMIT = 0.448
MAX_RATIO_PCT = 4.0


def strip(
    area: float,
    d: float,
    n: float,
    fcd: float,
    fyd: float,
    area_c: float = 0.0,
    d2: float = 0.0,
    es: float = 200_000.0,
    block=None,
) -> dict:
    """x/d and the tension bars' strain of a 1 m slab strip at its capacity (mm², mm, kN/m comp. +).

    ``area_c`` at depth ``d2`` from the compression face is compression steel, at the stress its strain
    gives (so it only counts in full where it yields). ``block(a)``, when given, is the concrete area
    (mm² per metre) of a compression block a deep, for a section with voids."""
    lo, hi = 1e-3, max(d, 1.0) * 1.5
    for _ in range(60):  # force balance: concrete block + compression bars = tension bars + N
        x = 0.5 * (lo + hi)
        sc = min(es * ECU * max(x - d2, 0.0) / x, fyd) if area_c else 0.0
        conc = fcd * (block(0.8 * x) if block else 0.8 * x * 1000)
        if conc + area_c * sc - area * fyd - n * 1e3 > 0:
            hi = x
        else:
            lo = x
    x = 0.5 * (lo + hi)
    x_d = x / d if d > 0 else 0.0
    eps_s = ECU * (d - x) / x if x > 0 else float("inf")
    return {"x_d": round(x_d, 3), "eps_s": round(min(eps_s, 1.0), 5), "eps_yd": round(fyd / es, 5)}


def warnings(x_d: float | None, eps_s: float | None, eps_yd: float, ratio_pct: float | None) -> list[str]:
    """What is wrong with a section's ductility, in words (nothing when it is fine)."""
    out = []
    if eps_s is not None and eps_s < eps_yd:
        out.append(
            f"over-reinforced: x/d = {x_d:.2f}, the tension bars do not yield before the concrete crushes"
        )
    elif x_d is not None and x_d > X_D_LIMIT + 1e-9:
        out.append(f"x/d = {x_d:.2f} is above 0.45, the ductility limit of EN 1992-1-1 5.5(4)")
    if ratio_pct is not None and ratio_pct > MAX_RATIO_PCT + 1e-9:
        out.append(f"steel {ratio_pct:.1f}% of the concrete, above the 4% of EN 1992-1-1 9.2.1.1(3)")
    return out
