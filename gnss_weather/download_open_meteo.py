"""Download an hourly ERA5 surface time series through Open-Meteo."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.error import URLError
from urllib.request import Request, urlopen

import pymap3d


API_URL = "https://archive-api.open-meteo.com/v1/archive"
HOURLY_VARIABLES = (
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "surface_pressure",
    "precipitation",
    "rain",
    "total_column_integrated_water_vapour",
)


def _iso_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def station_location(metadata_path: Path) -> tuple[float, float, float]:
    """Return geodetic latitude, longitude and ellipsoidal height from metadata."""
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    latitude, longitude, height = pymap3d.ecef2geodetic(
        metadata["xCoord"], metadata["yCoord"], metadata["zCoord"]
    )
    return float(latitude), float(longitude), float(height)


def build_url(
    latitude: float,
    longitude: float,
    start: date,
    end: date,
    *,
    model: str = "era5",
    elevation_m: float | None = None,
) -> str:
    """Build a deterministic Open-Meteo archive request URL."""
    parameters: dict[str, str | float] = {
        "latitude": round(latitude, 8),
        "longitude": round(longitude, 8),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "hourly": ",".join(HOURLY_VARIABLES),
        "models": model,
        "timezone": "GMT",
        "temperature_unit": "celsius",
        "precipitation_unit": "mm",
    }
    if elevation_m is not None:
        parameters["elevation"] = round(elevation_m, 2)
    return f"{API_URL}?{urlencode(parameters)}"


def download_json(url: str, *, timeout: float = 60.0) -> dict[str, Any]:
    """Retrieve and decode one Open-Meteo response."""
    request = Request(url, headers={"User-Agent": "gnss-weather/0.1"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except URLError:
        # The python.org Windows build does not always use the Windows trust
        # store. Curl does, while still enforcing certificate verification.
        curl = shutil.which("curl")
        if curl is None:
            raise
        result = subprocess.run(
            [
                curl,
                "--fail",
                "--silent",
                "--show-error",
                "--location",
                "--max-time",
                str(int(timeout)),
                "--user-agent",
                "gnss-weather/0.1",
                url,
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        payload = json.loads(result.stdout)
    if payload.get("error"):
        raise RuntimeError(f"Open-Meteo error: {payload.get('reason', 'unknown error')}")
    return payload


def normalized_rows(payload: dict[str, Any], model: str) -> list[dict[str, Any]]:
    """Convert an Open-Meteo response to the project's meteorological schema."""
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        raise ValueError("Open-Meteo response has no hourly data")
    missing = [name for name in ("time", *HOURLY_VARIABLES) if name not in hourly]
    if missing:
        raise ValueError(f"Open-Meteo response is missing variables: {missing}")
    count = len(hourly["time"])
    inconsistent = [name for name in HOURLY_VARIABLES if len(hourly[name]) != count]
    if inconsistent:
        raise ValueError(f"Open-Meteo variables have inconsistent lengths: {inconsistent}")

    rows: list[dict[str, Any]] = []
    for index, timestamp in enumerate(hourly["time"]):
        epoch = datetime.fromisoformat(timestamp).replace(tzinfo=timezone.utc)
        rows.append(
            {
                "epoch_utc": epoch.isoformat().replace("+00:00", "Z"),
                "pressure_hpa": hourly["surface_pressure"][index],
                "temperature_c": hourly["temperature_2m"][index],
                "rain_mm": hourly["rain"][index],
                "precipitation_mm": hourly["precipitation"][index],
                "relative_humidity_percent": hourly["relative_humidity_2m"][index],
                "dew_point_c": hourly["dew_point_2m"][index],
                "total_column_water_vapour_kg_m2": hourly[
                    "total_column_integrated_water_vapour"
                ][index],
                "source": "open-meteo",
                "model": model,
                "grid_latitude": payload.get("latitude"),
                "grid_longitude": payload.get("longitude"),
                "grid_elevation_m": payload.get("elevation"),
            }
        )
    return rows


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".part", dir=path.parent, text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def write_raw_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("No ERA5 observations to write")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".part", dir=path.parent, text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--station", default="MATE00ITA")
    parser.add_argument("--start", required=True, type=_iso_date)
    parser.add_argument("--end", required=True, type=_iso_date)
    parser.add_argument("--model", default="era5", choices=("era5", "era5_land"))
    parser.add_argument("--metadata-root", type=Path, default=Path("data/products/station"))
    parser.add_argument("--latitude", type=float)
    parser.add_argument("--longitude", type=float)
    parser.add_argument(
        "--elevation-m",
        type=float,
        help="optional elevation used by Open-Meteo for statistical downscaling",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--raw-output", type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.end < args.start:
        parser.error("--end must not precede --start")
    if (args.latitude is None) != (args.longitude is None):
        parser.error("--latitude and --longitude must be supplied together")

    station = args.station.upper()
    if args.latitude is None:
        latitude, longitude, _ = station_location(
            args.metadata_root / f"{station}.json"
        )
    else:
        latitude, longitude = args.latitude, args.longitude

    suffix = f"{args.start.isoformat()}_{args.end.isoformat()}"
    output = args.output or Path("data/meteo") / f"{station}_{args.model}_{suffix}.csv"
    raw_output = args.raw_output or output.with_suffix(".json")
    url = build_url(
        latitude,
        longitude,
        args.start,
        args.end,
        model=args.model,
        elevation_m=args.elevation_m,
    )
    print(url)
    if args.dry_run:
        return
    if output.exists() and raw_output.exists() and not args.force:
        print(f"Already present: {output} and {raw_output}")
        return

    payload = download_json(url)
    rows = normalized_rows(payload, args.model)
    write_raw_json(raw_output, payload)
    write_csv(output, rows)
    print(f"Wrote {len(rows)} hourly ERA5 observations to {output}")
    print(f"Saved the original Open-Meteo response to {raw_output}")


if __name__ == "__main__":
    main()
