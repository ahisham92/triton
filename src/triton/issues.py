"""Findings raised while importing and checking a workbook."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Severity(StrEnum):
    ERROR = "error"  # the sheet cannot be designed until this is fixed
    WARNING = "warning"  # probably a mistake in the workbook; review before designing
    INFO = "info"  # a clean-up the app did automatically


@dataclass
class Issue:
    severity: Severity
    code: str
    message: str
    sheet: str | None = None
    element: str | None = None
    combination: str | None = None
    rows: list[int] = field(default_factory=list)  # Excel row numbers (1-based), truncated

    MAX_ROWS = 20

    def __post_init__(self) -> None:
        self.rows = sorted(set(self.rows))[: self.MAX_ROWS]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d
