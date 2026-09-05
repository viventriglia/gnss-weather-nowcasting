"""Read and interpolate surface meteorological observations."""

from __future__ import annotations

import csv
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path

from .models import SurfaceWeather


def _parse_epoch(value: str) -> datetime:
    epoch = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if epoch.tzinfo is None:
        epoch = epoch.replace(tzinfo=timezone.utc)
    return epoch.astimezone(timezone.utc)


def read_surface_weather(path: Path) -> list[SurfaceWeather]:
    """Read ``epoch_utc,pressure_hpa,temperature_c[,rain_mm]`` from CSV."""
    observations: list[SurfaceWeather] = []
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        required = {"epoch_utc", "pressure_hpa", "temperature_c"}
        missing = required.difference(reader.fieldnames or ())
        if missing:
            raise ValueError(f"Missing meteorological CSV columns: {sorted(missing)}")
        for row in reader:
            rain_value = row.get("rain_mm")
            observations.append(
                SurfaceWeather(
                    epoch=_parse_epoch(row["epoch_utc"]),
                    pressure_hpa=float(row["pressure_hpa"]),
                    temperature_c=float(row["temperature_c"]),
                    rain_mm=float(rain_value) if rain_value not in (None, "") else None,
                )
            )
    if not observations:
        raise ValueError(f"No meteorological observations found in {path}")
    return sorted(observations, key=lambda item: item.epoch)


def interpolate_weather(
    observations: list[SurfaceWeather],
    epoch: datetime,
) -> SurfaceWeather:
    """Linearly interpolate pressure and temperature at ``epoch``."""
    if not observations:
        raise ValueError("at least one meteorological observation is required")
    epochs = [item.epoch for item in observations]
    upper = bisect_right(epochs, epoch)
    before = observations[max(0, upper - 1)]
    after = observations[min(upper, len(observations) - 1)]
    duration = (after.epoch - before.epoch).total_seconds()
    fraction = 0.0 if duration == 0 else (epoch - before.epoch).total_seconds() / duration
    fraction = min(max(fraction, 0.0), 1.0)

    def lerp(left: float, right: float) -> float:
        return left + fraction * (right - left)

    return SurfaceWeather(
        epoch=epoch,
        pressure_hpa=lerp(before.pressure_hpa, after.pressure_hpa),
        temperature_c=lerp(before.temperature_c, after.temperature_c),
        rain_mm=None,
    )
