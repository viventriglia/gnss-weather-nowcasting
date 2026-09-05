"""Build leakage-safe GNSS/ERA5 features for hourly rain nowcasting."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pymap3d

from .analyze_troposphere import build_atmospheric_series
from .meteorology import read_surface_weather
from .models import AtmosphericEstimate, ZtdEstimate
from .sinex_tro import read_sinex_tro


DELAY_COLUMNS = ("pwv_mm", "zwd_mm", "ztd_mm")
DELTA_WINDOWS = {"30m": "30min", "1h": "1h", "3h": "3h"}
ROLLING_WINDOWS = {"1h": ("1h", 12), "3h": ("3h", 36)}
TIME_COLUMNS = ("hour_sin", "hour_cos", "doy_sin", "doy_cos")
SOLAR_COLUMNS = ("solar_zenith_deg",)


@dataclass(frozen=True)
class RainDataset:
    """Intermediate five-minute features and the final hourly training table."""

    features_5min: pd.DataFrame
    hourly: pd.DataFrame
    feature_columns: tuple[str, ...]
    report: dict[str, object]


def _iso_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def read_era5(path: Path) -> pd.DataFrame:
    """Read the normalized Open-Meteo ERA5 file on a unique UTC hourly index."""
    frame = pd.read_csv(path)
    required = {"epoch_utc", "pressure_hpa", "temperature_c", "rain_mm"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"Missing ERA5 columns in {path}: {missing}")
    frame["epoch_utc"] = pd.to_datetime(frame["epoch_utc"], utc=True)
    frame = frame.set_index("epoch_utc").sort_index()
    if frame.index.has_duplicates:
        duplicates = frame.index[frame.index.duplicated()].unique()
        raise ValueError(f"Duplicate ERA5 epochs: {duplicates[:3].tolist()}")
    return frame


def read_tro_interval(
    paths: list[Path], start: date, end: date
) -> list[ZtdEstimate]:
    """Read TRO estimates in the inclusive UTC date interval."""
    first = datetime.combine(start, time.min, tzinfo=timezone.utc)
    end_exclusive = datetime.combine(
        end + timedelta(days=1), time.min, tzinfo=timezone.utc
    )
    return sorted(
        (
            estimate
            for path in paths
            for estimate in read_sinex_tro(path)
            if first <= estimate.epoch < end_exclusive
        ),
        key=lambda estimate: estimate.epoch,
    )


def station_location(metadata_path: Path) -> tuple[float, float, float]:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    latitude, longitude, height = pymap3d.ecef2geodetic(
        metadata["xCoord"], metadata["yCoord"], metadata["zCoord"]
    )
    return float(latitude), float(longitude), float(height)


def atmospheric_frame(estimates: list[AtmosphericEstimate]) -> pd.DataFrame:
    """Convert physical estimates to a five-minute, millimetre-based frame."""
    frame = pd.DataFrame.from_records(
        {
            "epoch_utc": estimate.epoch,
            "pwv_mm": estimate.pwv_mm,
            "zwd_mm": 1000.0 * estimate.zwd_m,
            "ztd_mm": 1000.0 * estimate.ztd_m,
            "pressure_hpa": estimate.pressure_hpa,
            "temperature_c": estimate.surface_temperature_c,
            "ztd_sigma_mm": 1000.0 * estimate.sigma_m
            if estimate.sigma_m is not None
            else np.nan,
            "ztd_source": estimate.ztd_source,
        }
        for estimate in estimates
    )
    if frame.empty:
        raise ValueError("No atmospheric estimates available")
    frame["epoch_utc"] = pd.to_datetime(frame["epoch_utc"], utc=True)
    frame = frame.set_index("epoch_utc").sort_index()
    if frame.index.has_duplicates:
        raise ValueError("Duplicate GNSS atmospheric epochs")
    return frame


def solar_zenith_angle(
    index: pd.DatetimeIndex, latitude_deg: float, longitude_deg: float
) -> np.ndarray:
    """Approximate solar zenith angle in degrees for UTC epochs.

    This is the NOAA fractional-year approximation. Its sub-degree accuracy is
    sufficient as a physical time/season feature; it is not an astronomy tool.
    """
    if index.tz is None:
        raise ValueError("solar zenith calculation requires timezone-aware epochs")
    utc = index.tz_convert("UTC")
    fractional_hour = (
        utc.hour + utc.minute / 60.0 + utc.second / 3600.0
    ).to_numpy(dtype=float)
    fractional_year = (
        2.0
        * math.pi
        / 365.2425
        * (utc.dayofyear.to_numpy(dtype=float) - 1.0 + (fractional_hour - 12.0) / 24.0)
    )
    equation_of_time_minutes = 229.18 * (
        0.000075
        + 0.001868 * np.cos(fractional_year)
        - 0.032077 * np.sin(fractional_year)
        - 0.014615 * np.cos(2.0 * fractional_year)
        - 0.040849 * np.sin(2.0 * fractional_year)
    )
    declination = (
        0.006918
        - 0.399912 * np.cos(fractional_year)
        + 0.070257 * np.sin(fractional_year)
        - 0.006758 * np.cos(2.0 * fractional_year)
        + 0.000907 * np.sin(2.0 * fractional_year)
        - 0.002697 * np.cos(3.0 * fractional_year)
        + 0.00148 * np.sin(3.0 * fractional_year)
    )
    solar_minutes = (
        fractional_hour * 60.0
        + equation_of_time_minutes
        + 4.0 * longitude_deg
    ) % 1440.0
    hour_angle = np.deg2rad(solar_minutes / 4.0 - 180.0)
    latitude = math.radians(latitude_deg)
    cosine_zenith = (
        math.sin(latitude) * np.sin(declination)
        + math.cos(latitude) * np.cos(declination) * np.cos(hour_angle)
    )
    return np.rad2deg(np.arccos(np.clip(cosine_zenith, -1.0, 1.0)))


def add_time_series_features(
    frame: pd.DataFrame,
    latitude_deg: float | None = None,
    longitude_deg: float | None = None,
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    """Add past-only deltas, rolling statistics and cyclic UTC time features."""
    result = frame.copy().sort_index()
    generated: list[str] = list(DELAY_COLUMNS)

    for column in DELAY_COLUMNS:
        series = result[column]
        for suffix, offset in DELTA_WINDOWS.items():
            name = f"{column}_delta_{suffix}"
            result[name] = series - series.shift(freq=offset)
            generated.append(name)
        for suffix, (window, minimum_samples) in ROLLING_WINDOWS.items():
            rolling = series.rolling(
                window, min_periods=minimum_samples, closed="right"
            )
            mean_name = f"{column}_mean_{suffix}"
            std_name = f"{column}_std_{suffix}"
            result[mean_name] = rolling.mean()
            result[std_name] = rolling.std(ddof=0)
            generated.extend((mean_name, std_name))

    hour = (
        result.index.hour
        + result.index.minute / 60.0
        + result.index.second / 3600.0
    )
    day = (
        result.index.dayofyear
        + hour / 24.0
        - 1.0
    )
    result["hour_sin"] = np.sin(2.0 * math.pi * hour / 24.0)
    result["hour_cos"] = np.cos(2.0 * math.pi * hour / 24.0)
    result["doy_sin"] = np.sin(2.0 * math.pi * day / 365.2425)
    result["doy_cos"] = np.cos(2.0 * math.pi * day / 365.2425)
    generated.extend(TIME_COLUMNS)
    if (latitude_deg is None) != (longitude_deg is None):
        raise ValueError("latitude and longitude must be provided together")
    if latitude_deg is not None and longitude_deg is not None:
        result["solar_zenith_deg"] = solar_zenith_angle(
            result.index, latitude_deg, longitude_deg
        )
        generated.extend(SOLAR_COLUMNS)
    generated.extend(("pressure_hpa", "temperature_c"))
    return result, tuple(generated)


def add_next_hour_target(
    features_5min: pd.DataFrame,
    era5: pd.DataFrame,
    feature_columns: tuple[str, ...],
    rain_threshold_mm: float = 0.1,
) -> pd.DataFrame:
    """Create an hourly target using ERA5's *preceding-hour* rain convention.

    ERA5 rain stamped at H+1 is the sum in (H, H+1]. Therefore the value is
    shifted one row backward and joined to GNSS features observed at H. Only
    exact UTC hour boundaries are retained: assigning that label to every
    five-minute epoch would leak part of the target hour into later features.
    """
    if rain_threshold_mm < 0:
        raise ValueError("rain threshold must be non-negative")
    on_hour = (
        (features_5min.index.minute == 0)
        & (features_5min.index.second == 0)
    )
    hourly = features_5min.loc[on_hour].copy()
    next_hour_rain = era5["rain_mm"].shift(-1).rename("rain_next_hour_mm")
    hourly = hourly.join(next_hour_rain, how="left")
    hourly["target_window_end_utc"] = hourly.index + pd.Timedelta(hours=1)
    hourly["is_rain"] = pd.Series(pd.NA, index=hourly.index, dtype="Int8")
    has_label = hourly["rain_next_hour_mm"].notna()
    hourly.loc[has_label, "is_rain"] = (
        hourly.loc[has_label, "rain_next_hour_mm"] > rain_threshold_mm
    ).astype("int8")

    complete_history = hourly[list(feature_columns)].notna().all(axis=1)
    hourly = hourly.loc[complete_history & has_label].copy()
    ordered = [
        "target_window_end_utc",
        "rain_next_hour_mm",
        "is_rain",
        "ztd_source",
        *feature_columns,
        "ztd_sigma_mm",
    ]
    return hourly[ordered]


def build_rain_dataset(
    tro_paths: list[Path],
    era5_path: Path,
    station_metadata_path: Path,
    start: date,
    end: date,
    rain_threshold_mm: float = 0.1,
) -> RainDataset:
    """Build physical five-minute features and a clean hourly ML dataset."""
    era5 = read_era5(era5_path)
    ztd = read_tro_interval(tro_paths, start, end)
    latitude, longitude, height = station_location(station_metadata_path)
    atmospheric = build_atmospheric_series(
        [],
        [],
        latitude,
        longitude,
        height,
        None,
        surface_weather=read_surface_weather(era5_path),
        ztd_estimates=ztd,
    )
    base = atmospheric_frame(atmospheric)
    featured, feature_columns = add_time_series_features(base, latitude, longitude)
    hourly = add_next_hour_target(
        featured, era5, feature_columns, rain_threshold_mm
    )

    expected_days = pd.date_range(start, end, freq="D", tz="UTC")
    observed_days = pd.DatetimeIndex(featured.index.normalize().unique())
    missing_days = expected_days.difference(observed_days)
    positives = int(hourly["is_rain"].sum())
    report: dict[str, object] = {
        "station": station_metadata_path.stem,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "rain_target": (
            "ERA5 rain_mm stamped at epoch+1h (sum of preceding hour) "
            f"> {rain_threshold_mm} mm"
        ),
        "rain_threshold_mm_strictly_greater_than": rain_threshold_mm,
        "gnss_native_interval_minutes": 5,
        "training_interval_minutes": 60,
        "tro_days_available": len(observed_days),
        "tro_days_missing": len(missing_days),
        "missing_tro_dates": [value.date().isoformat() for value in missing_days],
        "five_minute_rows": len(featured),
        "hourly_rows_complete": len(hourly),
        "positive_rows": positives,
        "positive_fraction": positives / len(hourly) if len(hourly) else None,
        "feature_columns": list(feature_columns),
        "time_reference": "UTC",
        "leakage_policy": "features at H use observations at or before H only",
    }
    return RainDataset(featured, hourly, feature_columns, report)


def write_dataset(dataset: RainDataset, output: Path) -> tuple[Path, Path]:
    output.parent.mkdir(parents=True, exist_ok=True)
    frame = dataset.hourly.reset_index()
    frame.to_csv(output, index=False)
    report_path = output.with_suffix(".json")
    report_path.write_text(
        json.dumps(dataset.report, indent=2) + "\n", encoding="utf-8"
    )
    return output, report_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--station", default="MATE00ITA")
    parser.add_argument("--start", type=_iso_date, required=True)
    parser.add_argument("--end", type=_iso_date, required=True)
    parser.add_argument(
        "--tro-root", type=Path, default=Path("data/products/reference_ztd")
    )
    parser.add_argument("--era5", type=Path, required=True)
    parser.add_argument(
        "--station-metadata-root",
        type=Path,
        default=Path("data/products/station"),
    )
    parser.add_argument("--rain-threshold-mm", type=float, default=0.1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.end < args.start:
        parser.error("--end must not precede --start")

    result = build_rain_dataset(
        sorted(args.tro_root.glob("*.TRO.gz")),
        args.era5,
        args.station_metadata_root / f"{args.station.upper()}.json",
        args.start,
        args.end,
        args.rain_threshold_mm,
    )
    csv_path, report_path = write_dataset(result, args.output)
    print(f"Hourly dataset: {csv_path} ({len(result.hourly)} rows)")
    print(f"Metadata:       {report_path}")
    print(
        "Rain events:    "
        f"{result.report['positive_rows']} "
        f"({100.0 * float(result.report['positive_fraction'] or 0):.2f}%)"
    )


if __name__ == "__main__":
    main()
