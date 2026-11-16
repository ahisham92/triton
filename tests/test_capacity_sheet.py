import numpy as np
import pytest

from triton.design.circular import CircularSection, ConcreteLaw, Ring, SteelLaw

# Points from Ahmed's capacities sheet (1200 mm pile, C40/50, B500): (N kN, compression +; MRd kNm).
# The sheet uses αcc = 1.0, the gross concrete area and bars 60 mm clear of the face.
SHEET = {
    (26, 32): [(5.0, 4103.0), (13659.3, 6315.11), (-6032.6, 1580.95), (32830.34, 2433.68)],
    (26, 16): [(50.0, 1194.0), (13122.94, 4314.41), (-695.09, 836.4)],
}


@pytest.mark.parametrize("bars", SHEET)
def test_pile_capacity_matches_the_capacities_sheet(bars):
    count, phi = bars
    sec = CircularSection(
        1200,
        (Ring(count, phi, 600 - 60 - phi / 2),),
        ConcreteLaw(40, 1.5, 1.0),
        SteelLaw(500, 1.15),
        deduct=False,
    )
    assert sec.interaction()[-1, 0] == pytest.approx(
        -count * 3.14159 * phi**2 / 4 * 500 / 1.15 / 1e3, rel=1e-3
    )
    for n, m in SHEET[bars]:
        c = sec.interaction(0.0)
        order = c[:, 0].argsort()
        assert float(np.interp(n, c[order, 0], c[order, 1])) == pytest.approx(m, rel=0.01)
