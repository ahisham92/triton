"""What accepting or rejecting each kind of warning does.

Nothing that changes the numbers happens before the user accepts it: repeated rows are kept, and
a sheet with rows Triton cannot read is not used, until the user accepts leaving those rows out.
Warnings that change nothing are used as they are; accepting one records that it was looked at,
rejecting it leaves its sheet out of the design.
"""

from __future__ import annotations

from typing import Any

# code: (what Accept does, what Reject does or None, "auto" | "pending" | "applied" before a decision)
#   auto: a tidy-up that changes no values; done, nothing to decide.
#   pending: nothing happens until accepted (rows kept, or the sheet not used).
#   applied: the sheet is used as it is; Accept records the review, Reject leaves the sheet out.
_LEAVE_OUT = "Leave the sheet out of the design"
_USE = "Use the sheet as it is"
RULES: dict[str, tuple[str, str | None, str]] = {
    "empty_sheet": ("Ignore the empty sheet", None, "auto"),
    "blank_rows_removed": ("Remove the blank rows", None, "auto"),
    "repeated_header_removed": ("Join the tables under one header", None, "auto"),
    "duplicate_rows_removed": ("Remove the repeated rows", "Keep them", "pending"),
    "non_numeric": ("Leave these rows out and use the rest of the sheet", _LEAVE_OUT, "pending"),
    "missing_values": ("Leave these rows out and use the rest of the sheet", _LEAVE_OUT, "pending"),
    "node_coordinates_differ": ("Leave these nodes out and use the rest of the sheet", _LEAVE_OUT, "pending"),
    "content_above_header": (_USE, _LEAVE_OUT, "applied"),
    "unnamed_columns": (_USE, _LEAVE_OUT, "applied"),
    "node_values_differ": (_USE, _LEAVE_OUT, "applied"),
    "outside_envelope": (_USE, _LEAVE_OUT, "applied"),
    "unexpected_units": (_USE, _LEAVE_OUT, "applied"),
    "identical_combinations": (_USE, _LEAVE_OUT, "applied"),
    "node_set_differs": (_USE, _LEAVE_OUT, "applied"),
    "point_count_differs": (_USE, "Leave this element out of the design", "applied"),
    "missing_combination": ("Go on without it", None, "applied"),
    "missing_qp": ("Go on without a crack check", None, "applied"),
}


def choices(code: str) -> dict[str, Any] | None:
    """What the review offers for an issue, or None when it is fixed elsewhere (the workbook, the
    sheet mapping or the load combinations)."""
    rule = RULES.get(code)
    if rule is None:
        return None
    accept, reject, before = rule
    return {"accept": accept, "reject": reject, "before": before}
