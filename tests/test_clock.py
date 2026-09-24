"""Times are Cairo times, whatever the server's clock is set to."""

from datetime import UTC, datetime

from triton import clock


def test_a_stamp_carries_cairo_offset():
    offset = clock.parse(clock.stamp()).utcoffset().total_seconds() / 3600
    assert offset in (2, 3)


def test_an_old_utc_stamp_reads_in_cairo():
    assert clock.show("2026-01-10T08:00:00+00:00") == "2026-01-10 10:00"  # winter, UTC+2
    assert clock.show("2026-07-10T08:00:00") == "2026-07-10 11:00"  # summer, UTC+3; no offset = UTC


def test_stamps_sort_by_instant_across_offsets():
    early = "2026-09-24T09:30:00+03:00"  # 06:30 UTC
    late = "2026-09-24T07:00:00+00:00"
    assert clock.order(early) < clock.order(late)


def test_fallback_rule_matches_the_time_zone_database():
    fallback = clock._Egypt()
    for month in range(1, 13):
        for day in (1, 15, 28):
            instant = datetime(2026, month, day, 12, tzinfo=UTC)
            assert instant.astimezone(fallback).utcoffset() == instant.astimezone(clock.CAIRO).utcoffset()
