"""Reader for station-specific IGS SINEX TRO/ZPD products."""

from __future__ import annotations

import gzip
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TextIO

from .models import ZtdEstimate


def _open_text(path: Path) -> TextIO:
    if path.suffix.lower() == ".gz":
        return gzip.open(path, "rt", encoding="ascii", errors="replace")
    return path.open("rt", encoding="ascii", errors="replace")


def parse_sinex_epoch(value: str) -> datetime:
    """Parse a SINEX ``YY:DOY:SSSSS`` epoch as a timezone-aware datetime."""
    year_short, doy, seconds = (int(part) for part in value.split(":"))
    year = 1900 + year_short if year_short >= 80 else 2000 + year_short
    return datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(
        days=doy - 1,
        seconds=seconds,
    )


def read_sinex_tro(path: Path) -> list[ZtdEstimate]:
    """Read the ``TROP/SOLUTION`` block from a SINEX TRO file.

    The IGS file expresses total delay and its standard deviation in
    millimetres, while gradients are in millimetres per the format definition.
    Returned values are converted to metres.
    """
    estimates: list[ZtdEstimate] = []
    in_solution = False

    with _open_text(path) as stream:
        for raw_line in stream:
            line = raw_line.rstrip("\n")
            if line == "+TROP/SOLUTION":
                in_solution = True
                continue
            if line == "-TROP/SOLUTION":
                break
            if not in_solution or not line or line.startswith("*"):
                continue

            fields = line.split()
            if len(fields) < 8:
                raise ValueError(f"Malformed TROP/SOLUTION row in {path}: {line!r}")
            station, epoch = fields[0], parse_sinex_epoch(fields[1])
            estimates.append(
                ZtdEstimate(
                    epoch=epoch,
                    station=station,
                    ztd_m=float(fields[2]) / 1000.0,
                    sigma_m=float(fields[3]) / 1000.0,
                    gradient_north_m=float(fields[4]) / 1000.0,
                    gradient_east_m=float(fields[6]) / 1000.0,
                    source="igs_final_tro",
                )
            )

    if not estimates:
        raise ValueError(f"No TROP/SOLUTION estimates found in {path}")
    return estimates

