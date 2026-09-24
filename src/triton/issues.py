"""Findings raised while importing and checking a workbook."""

from __future__ import annotations

import hashlib
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
    decision: str | None = None  # "accept" or "reject", from the section's review of its warnings

    MAX_ROWS = 200

    def __post_init__(self) -> None:
        self.rows = sorted(set(self.rows))[: self.MAX_ROWS]

    @property
    def id(self) -> str:
        """Stable across uploads of the same workbook: what it is and where, not how many rows."""
        key = "|".join(str(x or "") for x in (self.code, self.sheet, self.element, self.combination))
        return hashlib.sha1(key.encode()).hexdigest()[:12]

    @property
    def blocking(self) -> bool:
        """An error keeps its sheet out of the design unless the user accepted what it proposes."""
        return self.severity is Severity.ERROR and getattr(self, "decision", None) != "accept"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        d["id"] = self.id
        d["decision"] = getattr(self, "decision", None)
        return d
