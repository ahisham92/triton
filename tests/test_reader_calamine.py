"""The fast .xlsb reader gives pyxlsb's rows, or hands the sheet back to pyxlsb."""

import datetime
from types import SimpleNamespace

from triton import reader


class FastBook:
    def __init__(self, sheets):
        self.sheets = sheets

    def get_sheet_by_name(self, name):
        return SimpleNamespace(to_python=lambda skip_empty_area=True: self.sheets[name])


class SlowBook:
    """pyxlsb's workbook, only for the size it records for a sheet."""

    def __init__(self, height):
        self.height = height

    def get_sheet(self, name):
        return self

    def __enter__(self):
        return SimpleNamespace(dimension=SimpleNamespace(r=0, c=0, h=self.height, w=3))

    def __exit__(self, *a):
        return None


def rows(fast, name, height=6):
    return reader._calamine_rows(FastBook({name: fast}), SlowBook(height), name)


def test_rows_as_pyxlsb_gives_them():
    got = rows([["Node", "X [m]", ""], [1, 2.5, ""], ["", "", ""], [True, "", 3.0], ["", "", ""]], "S")
    # Numbers as floats, empty cells None, rows ending at their last value, blank rows kept and the
    # sheet padded with empty rows to its recorded size (6 rows).
    assert got == [["Node", "X [m]"], [1.0, 2.5], [], [True, None, 3.0], [], []]
    assert type(got[1][0]) is float
    assert rows([["", ""], ["", ""]], "Empty") == []


def test_cells_calamine_reads_differently_go_to_pyxlsb():
    assert rows([["Node", "#N/A"]], "S") is None  # an error cell: pyxlsb gives its code
    assert rows([["Node", datetime.date(2026, 9, 24)]], "S") is None  # a date: pyxlsb gives the number


def test_without_calamine_pyxlsb_reads(monkeypatch):
    import builtins

    real = builtins.__import__

    def no_calamine(name, *a, **k):
        if name == "python_calamine":
            raise ImportError(name)
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_calamine)
    assert reader._calamine("any.xlsb") is None
