import numpy as np
import pandas as pd

from triton.design.tension import beam_tension, face_bits, pack, pile_bits, pile_tension, slab_tension


def test_face_bits_bottom_top_and_whole():
    # 1 m wide, 1 m deep: A = 1 m2, W = 1/6 m3. Sagging 100 kNm = 0.6 MPa bending stress.
    bits = face_bits(
        np.array([0.0, 0.0, 0.0, -2000.0, 1000.0]), np.array([100.0, -100.0, 0.0, 10.0, 100.0]), 1.0, 1 / 6
    )
    assert bits.tolist() == [1, 2, 0, 4, 0]  # sagging, hogging, nothing, net tension, prestressed by N


def test_pile_bits_and_side_change():
    assert pile_bits(
        np.array([5000.0, 0.0, -3000.0]), np.zeros(3), np.array([10.0, 500.0, 0.0]), 1200
    ).tolist() == [0, 1, 4]
    loads = pd.DataFrame(
        {
            "combination": ["A", "B"],
            "X": [0.0, 0.0],
            "Y": [0.0, 0.0],
            "Z": [-5.0, -5.0],
            "N": [0.0, 0.0],
            "M_2": [0.0, 0.0],
            "M_3": [500.0, -300.0],
        }
    )
    t = pile_tension(loads, 1200)
    assert t["combinations"] == ["A", "B"]
    assert t["codes"]["A"] == "1" and t["codes"]["B"] == "1"
    assert t["codes"][""] == chr(48 + 3)  # one side, and the side changes


def test_pack_marks_missing_points():
    combos, codes = pack(np.array([0, 1, 0]), np.array(["A", "A", "B"]), np.array([1, 2, 4]), 3)
    assert combos == ["A", "B"]
    assert codes["A"] == "12 " and codes["B"] == "4  " and codes[""] == chr(48 + 5) + "2 "


def test_beam_and_slab_tension():
    beam = pd.DataFrame(
        {"s": [0.1, 0.2, 0.8], "N": [0.0, 0.0, 0.0], "Mv": [500.0, -500.0, 500.0], "combination": "A"}
    )
    t = beam_tension([beam], 0.0, 0.5, lambda i: [0.0, i * 0.5, 2.0], 2000, 1600)
    assert t["codes"][""] == "31" and t["points"][1] == [0.0, 0.5, 2.0]
    slab = pd.DataFrame(
        {
            "i": [0, 1],
            "j": [0, 0],
            "Nx": [0.0, 0.0],
            "Mx": [100.0, 0.0],
            "Ny": [0.0, 0.0],
            "My": [0.0, -100.0],
            "combination": "A",
        }
    )
    t = slab_tension([slab], [(0, 0), (1, 0)], 0.0, 0.0, 1.0, 3.0, 700)
    assert t["codes"]["A"] == chr(48 + 1) + chr(48 + (2 << 3))
    assert t["points"][1] == [1.5, 0.5, 3.0, 1.0]
