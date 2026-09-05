"""Parse RTKLIB PPP solution-status records into ZTD estimates."""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import ZtdEstimate

GPS_EPOCH = datetime(1980, 1, 6, tzinfo=timezone.utc)
LEAP_SECONDS = (
    (datetime(1981, 7, 1, tzinfo=timezone.utc), 1),
    (datetime(1982, 7, 1, tzinfo=timezone.utc), 2),
    (datetime(1983, 7, 1, tzinfo=timezone.utc), 3),
    (datetime(1985, 7, 1, tzinfo=timezone.utc), 4),
    (datetime(1988, 1, 1, tzinfo=timezone.utc), 5),
    (datetime(1990, 1, 1, tzinfo=timezone.utc), 6),
    (datetime(1991, 1, 1, tzinfo=timezone.utc), 7),
    (datetime(1992, 7, 1, tzinfo=timezone.utc), 8),
    (datetime(1993, 7, 1, tzinfo=timezone.utc), 9),
    (datetime(1994, 7, 1, tzinfo=timezone.utc), 10),
    (datetime(1996, 1, 1, tzinfo=timezone.utc), 11),
    (datetime(1997, 7, 1, tzinfo=timezone.utc), 12),
    (datetime(1999, 1, 1, tzinfo=timezone.utc), 13),
    (datetime(2006, 1, 1, tzinfo=timezone.utc), 14),
    (datetime(2009, 1, 1, tzinfo=timezone.utc), 15),
    (datetime(2012, 7, 1, tzinfo=timezone.utc), 16),
    (datetime(2015, 7, 1, tzinfo=timezone.utc), 17),
    (datetime(2017, 1, 1, tzinfo=timezone.utc), 18),
)


def gps_week_tow_to_utc(week: int, tow: float) -> datetime:
    """Convert RTKLIB's GPS week/seconds-of-week timestamp to UTC."""
    gps_time = GPS_EPOCH + timedelta(weeks=week, seconds=tow)
    offset = 0
    for effective, candidate in LEAP_SECONDS:
        if gps_time - timedelta(seconds=candidate) >= effective:
            offset = candidate
    return gps_time - timedelta(seconds=offset)


def read_rtklib_ztd(path: Path, station: str) -> list[ZtdEstimate]:
    """Read ``$TROP,week,tow,status,receiver,ztd,sigma`` status lines."""
    estimates: list[ZtdEstimate] = []
    with path.open("r", encoding="ascii", errors="replace") as stream:
        for line in stream:
            if not line.startswith("$TROP,"):
                continue
            fields = line.rstrip().split(",")
            if len(fields) != 7:
                continue
            estimates.append(
                ZtdEstimate(
                    epoch=gps_week_tow_to_utc(int(fields[1]), float(fields[2])),
                    station=station.upper(),
                    ztd_m=float(fields[5]),
                    sigma_m=float(fields[6]),
                    source="rtklib_ppp",
                )
            )
    if not estimates:
        raise ValueError(f"No RTKLIB $TROP records found in {path}")
    return estimates


def write_ztd_csv(path: Path, estimates: list[ZtdEstimate]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["epoch_utc", "station", "ztd_m", "sigma_m", "source"])
        for item in estimates:
            writer.writerow(
                [item.epoch.isoformat(), item.station, item.ztd_m, item.sigma_m, item.source]
            )


def read_ztd_csv(path: Path) -> list[ZtdEstimate]:
    """Read the normalized ZTD CSV produced by :func:`write_ztd_csv`."""
    estimates: list[ZtdEstimate] = []
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            sigma = row.get("sigma_m")
            estimates.append(
                ZtdEstimate(
                    epoch=datetime.fromisoformat(row["epoch_utc"]),
                    station=row["station"],
                    ztd_m=float(row["ztd_m"]),
                    sigma_m=float(sigma) if sigma not in (None, "") else None,
                    source=row.get("source", "csv"),
                )
            )
    if not estimates:
        raise ValueError(f"No ZTD estimates found in {path}")
    return estimates
