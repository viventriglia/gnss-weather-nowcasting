"""Build an atmospheric time series from TRO/PPP ZTD and meteorological data."""

from __future__ import annotations

import argparse
import csv
import json
from bisect import bisect_right
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pymap3d

from .atmosphere import (
    weighted_mean_temperature,
    zenith_hydrostatic_delay,
    zwd_to_pwv_mm,
)
from .meteorology import interpolate_weather, read_surface_weather
from .models import AtmosphericEstimate, SurfaceWeather, Vmf3Estimate, ZtdEstimate
from .rain_potential import estimate_rain_potential
from .rtklib_stat import read_ztd_csv
from .sinex_tro import read_sinex_tro
from .vmf3 import Vmf3Grid, interpolate_time


def _iso_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _station_location(metadata_path: Path) -> tuple[float, float, float]:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    latitude, longitude, height = pymap3d.ecef2geodetic(
        metadata["xCoord"],
        metadata["yCoord"],
        metadata["zCoord"],
    )
    return float(latitude), float(longitude), float(height)


def _vmf3_at_epochs(
    vmf3_paths: list[Path],
    latitude: float,
    longitude: float,
    height_m: float,
    orography_path: Path | None,
) -> list[Vmf3Estimate]:
    return sorted(
        (
            Vmf3Grid.read(path, orography_path).at(latitude, longitude, height_m)
            for path in vmf3_paths
        ),
        key=lambda value: value.epoch,
    )


def build_atmospheric_series(
    tro_paths: list[Path],
    vmf3_paths: list[Path],
    latitude: float,
    longitude: float,
    height_m: float,
    orography_path: Path,
    surface_temperature_c: float | None = None,
    surface_weather: list[SurfaceWeather] | None = None,
    ztd_estimates: list[ZtdEstimate] | None = None,
) -> list[AtmosphericEstimate]:
    """Subtract VMF3 ZHD from ZTD and optionally convert ZWD to PWV."""
    ztd = sorted(
        ztd_estimates
        if ztd_estimates is not None
        else (item for path in tro_paths for item in read_sinex_tro(path)),
        key=lambda item: item.epoch,
    )
    vmf3 = (
        _vmf3_at_epochs(vmf3_paths, latitude, longitude, height_m, orography_path)
        if vmf3_paths
        else []
    )
    vmf3_epochs = [item.epoch for item in vmf3]
    if not vmf3 and not surface_weather:
        raise ValueError("VMF3 grids or surface weather are required")

    constant_tm = (
        weighted_mean_temperature(surface_temperature_c + 273.15)
        if surface_temperature_c is not None
        else None
    )
    atmospheric: list[AtmosphericEstimate] = []
    for observation in ztd:
        model = None
        if vmf3:
            upper = bisect_right(vmf3_epochs, observation.epoch)
            before = vmf3[max(0, upper - 1)]
            after = vmf3[min(upper, len(vmf3) - 1)]
            model = interpolate_time(before, after, observation.epoch)
        weather = (
            interpolate_weather(surface_weather, observation.epoch)
            if surface_weather
            else None
        )
        if weather is not None:
            zhd = zenith_hydrostatic_delay(
                weather.pressure_hpa,
                latitude,
                height_m,
            )
            tm = weighted_mean_temperature(weather.temperature_c + 273.15)
            zhd_source = "surface_pressure"
        else:
            assert model is not None
            zhd = model.zhd_m
            tm = constant_tm
            zhd_source = "vmf3"
        zwd = observation.ztd_m - zhd
        pwv = zwd_to_pwv_mm(zwd, tm) if tm is not None and zwd >= 0 else None
        atmospheric.append(
            AtmosphericEstimate(
                epoch=observation.epoch,
                ztd_m=observation.ztd_m,
                zhd_m=zhd,
                zwd_m=zwd,
                sigma_m=observation.sigma_m,
                model_zwd_m=model.zwd_m if model is not None else None,
                pwv_mm=pwv,
                zhd_source=zhd_source,
                pressure_hpa=weather.pressure_hpa if weather else None,
                surface_temperature_c=weather.temperature_c if weather else surface_temperature_c,
                ztd_source=observation.source,
            )
        )
    return atmospheric


def write_csv(path: Path, estimates: list[AtmosphericEstimate]) -> None:
    potential = estimate_rain_potential(estimates)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "epoch_utc",
                "ztd_m",
                "zhd_m",
                "zwd_m",
                "vmf3_model_zwd_m",
                "ztd_sigma_m",
                "ztd_source",
                "pwv_mm",
                "zhd_source",
                "pressure_hpa",
                "surface_temperature_c",
                "zwd_rate_mm_per_hour",
                "moisture_percentile",
                "rain_potential",
                "rain_category",
            ]
        )
        for estimate, rain in zip(estimates, potential, strict=True):
            writer.writerow(
                [
                    estimate.epoch.isoformat(),
                    estimate.ztd_m,
                    estimate.zhd_m,
                    estimate.zwd_m,
                    estimate.model_zwd_m,
                    estimate.sigma_m,
                    estimate.ztd_source,
                    estimate.pwv_mm,
                    estimate.zhd_source,
                    estimate.pressure_hpa,
                    estimate.surface_temperature_c,
                    rain.zwd_rate_mm_per_hour,
                    rain.moisture_percentile,
                    rain.score,
                    rain.category,
                ]
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/products"))
    parser.add_argument("--output", type=Path, default=Path("data/derived/atmosphere.csv"))
    parser.add_argument("--station", default="MATE00ITA")
    parser.add_argument("--start", type=_iso_date, help="first UTC day to include")
    parser.add_argument("--end", type=_iso_date, help="last UTC day to include")
    parser.add_argument(
        "--ztd-csv",
        type=Path,
        help="use normalized ZTD CSV (for example RTKLIB PPP) instead of IGS TRO",
    )
    parser.add_argument(
        "--discard-first-hours",
        type=float,
        default=0.0,
        help="discard the initial PPP convergence interval",
    )
    parser.add_argument(
        "--surface-temperature-c",
        type=float,
        help="optional representative surface temperature used for PWV conversion",
    )
    parser.add_argument(
        "--meteo-csv",
        type=Path,
        help="CSV with epoch_utc, pressure_hpa, temperature_c and optional rain_mm",
    )
    args = parser.parse_args()

    if (args.start is None) != (args.end is None):
        parser.error("--start and --end must be supplied together")
    if args.start is not None and args.end < args.start:
        parser.error("--end must not precede --start")

    tro_paths = sorted((args.data / "reference_ztd").glob("*.TRO.gz"))
    vmf3_paths = sorted((args.data / "vmf3").glob("VMF3_*"))
    if not tro_paths and args.ztd_csv is None:
        parser.error(f"no IGS TRO files found below {args.data / 'reference_ztd'}")
    latitude, longitude, height = _station_location(
        args.data / "station" / f"{args.station.upper()}.json"
    )
    if args.meteo_csv is not None and args.surface_temperature_c is not None:
        parser.error("use either --meteo-csv or --surface-temperature-c, not both")
    surface_weather = read_surface_weather(args.meteo_csv) if args.meteo_csv else None
    ztd_estimates = read_ztd_csv(args.ztd_csv) if args.ztd_csv else None
    estimates = build_atmospheric_series(
        tro_paths,
        vmf3_paths,
        latitude,
        longitude,
        height,
        args.data / "vmf3" / "orography_ell_1x1",
        args.surface_temperature_c,
        surface_weather,
        ztd_estimates,
    )
    if args.start is not None:
        first_epoch = datetime.combine(args.start, time.min, tzinfo=timezone.utc)
        end_exclusive = datetime.combine(
            args.end + timedelta(days=1), time.min, tzinfo=timezone.utc
        )
        estimates = [
            estimate
            for estimate in estimates
            if first_epoch <= estimate.epoch < end_exclusive
        ]
    if args.discard_first_hours < 0:
        parser.error("--discard-first-hours must not be negative")
    if estimates and args.discard_first_hours:
        convergence_end = estimates[0].epoch + timedelta(hours=args.discard_first_hours)
        estimates = [item for item in estimates if item.epoch >= convergence_end]
    if not estimates:
        parser.error("no ZTD estimates found in the requested interval")
    write_csv(args.output, estimates)
    print(f"Wrote {len(estimates)} atmospheric estimates to {args.output}")


if __name__ == "__main__":
    main()
