"""Slab drawings with column and field strips: every part of the slab gets its strip's bars."""

from __future__ import annotations

from triton.design.export import _strip_zones


def _deck(lines: list[float], box_y: tuple[float, float]) -> dict:
    rows = [
        {"layer": "top_x", "strip": s, "station": [0.0, 4.0], "bars": f"{s} bars", "additional_bars": True}
        for s in ("column", "field")
    ]
    return {
        "box": {"X": [0.0, 20.0], "Y": list(box_y)},
        "strip_design": {
            "along": "X",
            "sign": 1,
            "origin": 0.0,
            "lines": lines,
            "column_width_m": 2.2,
            "field_width_m": 2.0,
            "rows": rows,
        },
    }


def _covered(zones: list[dict]) -> list[tuple[float, float]]:
    spans = sorted(tuple(z["y_m"]) for z in zones)
    merged = [spans[0]]
    for a, b in spans[1:]:
        if a <= merged[-1][1] + 1e-9:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def test_the_edges_beyond_the_outer_pile_lines_take_the_column_strip_bars():
    # Section 01a: pile lines 4.2 m apart, the slab 0.3 m wider each side than the outer column strips.
    lines = [-14.0, -9.8, -5.6, -1.4, 2.8, 7.0, 11.2, 15.4]
    zones = _strip_zones(_deck(lines, (-16.8, 16.8)))["top_x"]
    assert _covered(zones) == [(-16.8, 16.8)]
    edge = [z for z in zones if z["y_m"][0] == -16.8]
    assert edge and edge[0]["label"] == "column bars"


def test_field_strips_fill_wider_gaps_between_column_strips():
    zones = _strip_zones(_deck([0.0, 6.0], (-1.1, 7.1)))["top_x"]
    assert _covered(zones) == [(-1.1, 7.1)]
    field = [z for z in zones if z["label"] == "field bars"]
    assert [tuple(z["y_m"]) for z in field] == [(1.1, 4.9)]
