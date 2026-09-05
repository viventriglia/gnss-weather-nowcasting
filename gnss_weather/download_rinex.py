"""Download daily RINEX observations and broadcast navigation data."""

from __future__ import annotations

import argparse
import logging
from datetime import date, datetime
from pathlib import Path

from pytecgg.utils import download_nav_bkg, download_obs_euref

from .dates import date_range


def download_rinex(
    station: str,
    start: date,
    end: date,
    output_dir: Path,
) -> None:
    """Download EUREF OBS and BKG broadcast NAV files using PyTECGg."""
    days = date_range(start, end)
    years = {day.year for day in days}
    if len(years) != 1:
        raise ValueError("split downloads at the year boundary")

    year = days[0].year
    doys = [day.timetuple().tm_yday for day in days]
    download_obs_euref(station, year, doys, output_dir / "obs")
    download_nav_bkg(year, doys, output_dir / "nav")


def _iso_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--station", default="MATE00ITA")
    parser.add_argument("--start", type=_iso_date, required=True)
    parser.add_argument("--end", type=_iso_date, required=True)
    parser.add_argument("--output", type=Path, default=Path("data/rinex"))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    download_rinex(args.station, args.start, args.end, args.output)


if __name__ == "__main__":
    main()

