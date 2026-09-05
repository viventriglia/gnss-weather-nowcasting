"""Date helpers shared by GNSS data downloaders."""

from __future__ import annotations

from datetime import date, timedelta

GPS_EPOCH = date(1980, 1, 6)


def date_range(start: date, end: date) -> list[date]:
    """Return all dates in the inclusive interval ``start`` ... ``end``."""
    if end < start:
        raise ValueError("end date must not precede start date")
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def gps_week(day: date) -> int:
    """Return the GPS week containing ``day``."""
    if day < GPS_EPOCH:
        raise ValueError("GPS week is undefined before 1980-01-06")
    return (day - GPS_EPOCH).days // 7


def gps_week_start(day: date) -> date:
    """Return the Sunday at the start of the GPS week containing ``day``."""
    return GPS_EPOCH + timedelta(weeks=gps_week(day))

