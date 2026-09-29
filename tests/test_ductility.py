import pytest

from triton.design import ductility
from triton.design.circular import ConcreteLaw, SteelLaw
from triton.design.rect import Bars, RectSection


def test_strip_x_over_d_and_compression_bars():
    fcd, fyd = 40 / 1.5, 500 / 1.15
    # 1 m strip, d = 600 mm: x = As·fyd / (0.8·1000·fcd).
    light = ductility.strip(3000, 600, 0.0, fcd, fyd)
    assert light["x_d"] == pytest.approx(3000 * fyd / (0.8 * 1000 * fcd) / 600, abs=1e-3)
    assert not ductility.warnings(light["x_d"], light["eps_s"], light["eps_yd"], 0.5)
    heavy = ductility.strip(17000, 600, 0.0, fcd, fyd)
    assert heavy["x_d"] > 0.45 and ductility.warnings(heavy["x_d"], heavy["eps_s"], heavy["eps_yd"], 2.4)
    # Bars on the other face in compression bring the neutral axis up.
    helped = ductility.strip(17000, 600, 0.0, fcd, fyd, area_c=8000, d2=60)
    assert helped["x_d"] < heavy["x_d"]
    # Very heavy: the tension bars do not yield.
    over = ductility.strip(30000, 600, 0.0, fcd, fyd)
    text = ductility.warnings(over["x_d"], over["eps_s"], over["eps_yd"], 5.0)
    assert any("do not yield" in t for t in text) and any("4%" in t for t in text)


def test_rect_ductility_matches_the_block_and_flags_heavy_cages():
    cl = ConcreteLaw(fck=40, gamma_c=1.5, alpha_cc=1.0)
    sl = SteelLaw(fyk=500, gamma_s=1.15)
    light = RectSection(1000, 1000, Bars.row(6, 25, -440, 440), cl, sl)
    r = light.ductility("v", 1, 0.0)
    assert r["d_mm"] == 940 and r["x_d"] < 0.2 and r["eps_s"] > r["eps_yd"]
    heavy = RectSection(400, 600, Bars.join(Bars.row(8, 32, -240, 150), Bars.row(8, 32, -190, 150)), cl, sl)
    h = heavy.ductility("v", 1, 0.0)
    assert h["x_d"] > 0.45
    assert ductility.warnings(h["x_d"], h["eps_s"], h["eps_yd"], None)


def test_slab_rows_and_table_carry_ductility():
    from test_slabs import design_deck

    d = design_deck()
    rows = d["strip_design"]["rows"]
    assert rows and all(0 < r["ductility"]["x_d"] < 1 for r in rows)
    assert all(set(t["ductility"]) == set(t["bars"]) for t in d["strip_design"]["table"])
    assert isinstance(d["ductility"], list)
