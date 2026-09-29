"""Triton's N–M capacities against the issued Tincan AdSec files (N25185, 2026-07), same bars, same N."""

import math

import numpy as np
import pytest

from triton.design.circular import CircularSection, ConcreteLaw, Ring, SteelLaw
from triton.design.rect import Bars, RectSection
from triton.design.slabs import strip_mrd

C40 = ConcreteLaw(40, 1.5, 1.0)
ADSEC = SteelLaw.of(500, 1.15, "adsec")
A20, A25, A32 = (math.pi * p * p / 4 for p in (20, 25, 32))


@pytest.mark.parametrize(
    "d, count, phi, radius, n, mu",
    [
        (1200, 26, 25, 512.5, 1366, 3130),  # Middle Pile 3 Part 1, LC7
        (1200, 26, 25, 512.5, 2609, 3537),
        (1200, 26, 25, 512.5, 604, 2862),
        (1590, 36, 32, 704, 1034, 8315),  # Combi Wall Part 1 (67% to the concrete), LC7
        (1590, 36, 32, 704, 2203, 8829),
        (1590, 36, 32, 704, 329, 7998),
    ],
)
def test_circular_capacity_follows_adsec(d, count, phi, radius, n, mu):
    m = CircularSection(d, (Ring(count, phi, radius),), C40, ADSEC, deduct=False).moment_capacity(n)
    assert m == pytest.approx(mu, rel=0.006)
    flat = CircularSection(d, (Ring(count, phi, radius),), C40, SteelLaw.of(500, 1.15, "flat"), deduct=False)
    assert flat.moment_capacity(n) < m


def _rear_beam() -> RectSection:
    row = np.linspace(-1902, 1902, 22)
    row2 = np.r_[-1924, np.linspace(-1721, 1721, 20), 1924]
    side = np.linspace(-723.3, 723.3, 10)
    bars = Bars.join(
        *(
            Bars(u, np.full(22, v), np.full(22, A20))
            for u, v in ((row, 924.0), (row2, 884.0), (row, -924.0), (row2, -884.0))
        ),
        *(Bars(np.full(10, u), side, np.full(10, A25)) for u in (-1922.0, 1922.0)),
    )
    return RectSection(4000, 2000, bars, C40, ADSEC, deduct=False)


def test_rectangles_follow_adsec():
    # Rear Beam 4000 x 2000, LC14 and LC10 (hogging).
    beam = _rear_beam()
    for n, mu in ((-416.0, 15770), (-555.0, 15680)):
        assert beam.m_rd("v", np.array([-1]), np.array([n]))[0] == pytest.approx(mu, rel=0.005)
    # Slab 700 M11 station 2.25-4 column strip, 1050 wide, 769 deep, hogging LC3.
    xs = np.array([-450, -300, -150, 0, 150, 300, 450.0])
    bars = Bars.join(
        Bars(xs, np.full(7, 278.5), np.full(7, A32)),
        Bars(xs, np.full(7, 324.5), np.full(7, A20)),
        Bars(xs, np.full(7, -324.5), np.full(7, A20)),
    )
    slab = RectSection(1050, 769, bars, C40, ADSEC, deduct=False)
    assert slab.m_rd("v", np.array([-1]), np.array([-672.0]))[0] == pytest.approx(1983, rel=0.005)


def test_slab_strip_block_takes_the_hardening():
    area = (7 * A32 + 7 * A20) / 1.05
    d = (7 * A32 * 663 + 7 * A20 * 709) / (7 * A32 + 7 * A20)
    flat = strip_mrd(area, d, 769, -640, 26.667, 434.78)
    hard = strip_mrd(area, d, 769, -640, 26.667, 434.78, ADSEC)
    assert flat < hard < flat * 1.03
    assert hard * 1.05 == pytest.approx(1983, rel=0.02)
