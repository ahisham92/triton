"""The deck strip as AdSec: Tincan B5&6 deck, station 2.25-4 m column strip (AdSec 8.3, 769 mm deep, 1.05 m
wide: 7T20 + 7T32 top, 7T20 bottom, C40/50, 500B)."""

import math

import numpy as np
import pytest

from triton.design import slab_section as ss
from triton.design.circular import SteelLaw
from triton.materials import concrete

B, H = 1.05, 769.0
A = lambda phi: math.pi * phi * phi / 4  # noqa: E731
TOP = [(7 * A(20) / B, H - 60, 20, 150), (7 * A(32) / B, H - 106, 32, 150)]
BOTTOM = [(7 * A(20) / B, H - 60, 20, 150)]
STEEL = SteelLaw.of(500, 1.15, "adsec")
FCD = 40 / 1.5


def _other(layers):
    return [(a, H - d) for a, d, _, _ in layers]


@pytest.mark.parametrize(
    "n, m, adsec",
    [(-288, 343, 2091), (-780, 315, 1953), (-672, 1974, 1983)],
)
def test_hogging_capacity_is_adsecs(n, m, adsec):
    mrd = ss.mrd([(a, d) for a, d, _, _ in TOP], H, n / B, FCD, STEEL, _other(BOTTOM)) * B
    assert mrd == pytest.approx(adsec, rel=0.002)


def test_sagging_capacity_counts_the_top_bars_at_their_own_strain():
    mrd = ss.mrd([(a, d) for a, d, _, _ in BOTTOM], H, -307 / B, FCD, STEEL, _other(TOP)) * B
    assert mrd == pytest.approx(662, rel=0.002)


def test_needed_steel_is_what_adsec_carries():
    top = sum(a for a, _, _, _ in TOP)
    d = sum(a * dd for a, dd, _, _ in TOP) / top
    need = ss.required_as(np.array([1974 / B]), np.array([-672 / B]), H, d, FCD, STEEL, _other(BOTTOM))[0]
    assert need == pytest.approx(top * 1974 / 1983, rel=0.01)


@pytest.mark.parametrize(
    "n, m, layers, other, adsec",
    [
        (-289, 442, TOP, BOTTOM, 0.1099),
        (-296, 542, TOP, BOTTOM, 0.1316),
        (-313, -25, BOTTOM, TOP, 0.07566),  # hogging: the bottom bars still govern under the tension
        (-305, -11, BOTTOM, TOP, 0.1064),
    ],
)
def test_crack_widths_are_adsecs(n, m, layers, other, adsec):
    conc = concrete("C40/50")
    wk = ss.crack_widths(np.array([m / B]), np.array([n / B]), layers, H, _other(other), conc, conc.ecm / 3)
    assert wk[0] == pytest.approx(adsec, rel=0.01)
