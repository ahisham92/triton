"""The time of day, in Cairo.

Every time Triton records or prints (a project saved, a workbook uploaded, a design run, a report
printed) is Cairo time, whatever the server's own clock is set to (PythonAnywhere's is UTC).
Stamps are stored with their offset, so older stamps written in UTC still read correctly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, tzinfo

ZONE_NAME = "Africa/Cairo"


def _last(year: int, month: int, weekday: int) -> datetime:
    """The last given weekday (Monday = 0) of a month, at midnight."""
    day = datetime(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return day - timedelta(days=(day.weekday() - weekday) % 7)


class _Egypt(tzinfo):
    """Egypt's rule since 2023, for a machine without the time zone database (Windows without
    tzdata): UTC+2, and UTC+3 from the last Friday of April to the last Thursday of October."""

    def utcoffset(self, dt: datetime | None) -> timedelta:
        return timedelta(hours=2) + self.dst(dt)

    def dst(self, dt: datetime | None) -> timedelta:
        if dt is None:
            return timedelta(0)
        naive = dt.replace(tzinfo=None)
        start = _last(naive.year, 4, 4)  # Friday 00:00
        end = _last(naive.year, 10, 3) + timedelta(hours=24)  # Thursday 24:00
        return timedelta(hours=1) if start <= naive < end else timedelta(0)

    def tzname(self, dt: datetime | None) -> str:
        return "EEST" if self.dst(dt) else "EET"


def _zone() -> tzinfo:
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(ZONE_NAME)
    except Exception:  # noqa: BLE001 - no time zone database here
        return _Egypt()


CAIRO = _zone()


def now() -> datetime:
    return datetime.now(UTC).astimezone(CAIRO)


def stamp() -> str:
    """Now, as stored: ISO 8601 with Cairo's offset."""
    return now().isoformat(timespec="seconds")


def parse(text: str) -> datetime | None:
    """A stored stamp as a Cairo time; a stamp without an offset is taken as UTC."""
    try:
        when = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when.astimezone(CAIRO)


def show(text: str) -> str:
    """A stored stamp as people read it: ``2026-09-24 09:42`` in Cairo."""
    when = parse(text)
    return when.strftime("%Y-%m-%d %H:%M") if when else str(text or "")


def order(text: str) -> float:
    """A sort key for stored stamps, whatever offset each was written with."""
    when = parse(text)
    return when.timestamp() if when else 0.0
