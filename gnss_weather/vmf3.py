"""Parse and interpolate TU Wien gridded VMF3 products."""

from __future__ import annotations

import re
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .models import Vmf3Estimate

EPOCH_PATTERN = re.compile(
    r"^! Epoch:\s+(\d{4})\s+(\d{2})\s+(\d{2})\s+(\d{2})\s+(\d{2})\s+([\d.]+)"
)


@dataclass(frozen=True)
class Vmf3Grid:
    """A regular VMF3 latitude/longitude grid at one epoch."""

    epoch: datetime
    latitudes: np.ndarray
    longitudes: np.ndarray
    values: np.ndarray
    orography_m: np.ndarray | None = None

    @classmethod
    def read(cls, path: Path, orography_path: Path | None = None) -> "Vmf3Grid":
        epoch: datetime | None = None
        with path.open("rt", encoding="ascii") as stream:
            for line in stream:
                match = EPOCH_PATTERN.match(line)
                if match:
                    year, month, day, hour, minute = map(int, match.groups()[:5])
                    second = float(match.group(6))
                    epoch = datetime(
                        year,
                        month,
                        day,
                        hour,
                        minute,
                        int(second),
                        tzinfo=timezone.utc,
                    )
                    break
        if epoch is None:
            raise ValueError(f"VMF3 epoch header not found in {path}")

        rows = np.loadtxt(path, comments="!", dtype=float)
        if rows.ndim != 2 or rows.shape[1] != 6:
            raise ValueError(f"Expected six VMF3 columns in {path}, got {rows.shape}")

        latitudes = np.unique(rows[:, 0])
        longitudes = np.unique(rows[:, 1])
        expected_rows = len(latitudes) * len(longitudes)
        if len(rows) != expected_rows:
            raise ValueError(f"VMF3 grid is incomplete in {path}")

        values = np.empty((len(latitudes), len(longitudes), 4), dtype=float)
        lat_index = np.searchsorted(latitudes, rows[:, 0])
        lon_index = np.searchsorted(longitudes, rows[:, 1])
        values[lat_index, lon_index, :] = rows[:, 2:]
        orography = None
        if orography_path is not None:
            raw_orography = np.loadtxt(orography_path, dtype=float)
            if raw_orography.size != expected_rows:
                raise ValueError(f"VMF3 orography is incomplete in {orography_path}")
            # The official file follows the VMF3 order: latitude descending,
            # longitude ascending. Our arrays use latitude ascending.
            orography = np.flip(
                raw_orography.reshape(len(latitudes), len(longitudes)),
                axis=0,
            )
        return cls(epoch, latitudes, longitudes, values, orography)

    def at(
        self,
        latitude_deg: float,
        longitude_deg: float,
        height_m: float = 0.0,
    ) -> Vmf3Estimate:
        """Bilinearly interpolate ``ah``, ``aw``, ZHD and ZWD at a site."""
        longitude = longitude_deg % 360.0
        latitude = float(np.clip(latitude_deg, self.latitudes[0], self.latitudes[-1]))

        lat_hi = min(np.searchsorted(self.latitudes, latitude), len(self.latitudes) - 1)
        lat_lo = max(lat_hi - 1, 0)

        extended_lons = np.append(self.longitudes, self.longitudes[0] + 360.0)
        lon_hi = int(np.searchsorted(extended_lons, longitude))
        lon_lo = (lon_hi - 1) % len(self.longitudes)
        lon_hi_wrapped = lon_hi % len(self.longitudes)

        lat0, lat1 = self.latitudes[lat_lo], self.latitudes[lat_hi]
        lon0 = self.longitudes[lon_lo]
        lon1 = extended_lons[lon_hi]
        if lon1 <= lon0:
            lon1 += 360.0
        lon_value = longitude if longitude >= lon0 else longitude + 360.0

        fy = 0.0 if lat1 == lat0 else (latitude - lat0) / (lat1 - lat0)
        fx = 0.0 if lon1 == lon0 else (lon_value - lon0) / (lon1 - lon0)
        v00 = self._at_height(lat_lo, lon_lo, latitude_deg, height_m)
        v01 = self._at_height(lat_lo, lon_hi_wrapped, latitude_deg, height_m)
        v10 = self._at_height(lat_hi, lon_lo, latitude_deg, height_m)
        v11 = self._at_height(lat_hi, lon_hi_wrapped, latitude_deg, height_m)
        value = (
            v00 * (1.0 - fx) * (1.0 - fy)
            + v01 * fx * (1.0 - fy)
            + v10 * (1.0 - fx) * fy
            + v11 * fx * fy
        )
        return Vmf3Estimate(
            epoch=self.epoch,
            latitude_deg=latitude_deg,
            longitude_deg=longitude_deg,
            ah=float(value[0]),
            aw=float(value[1]),
            zhd_m=float(value[2]),
            zwd_m=float(value[3]),
        )

    def _at_height(
        self,
        latitude_index: int,
        longitude_index: int,
        site_latitude_deg: float,
        site_height_m: float,
    ) -> np.ndarray:
        value = self.values[latitude_index, longitude_index].copy()
        if self.orography_m is None:
            return value

        grid_height = float(self.orography_m[latitude_index, longitude_index])
        delta_height = site_height_m - grid_height
        latitude = math.radians(site_latitude_deg)
        gravity_grid = 1.0 - 0.00266 * math.cos(2.0 * latitude) - 2.8e-7 * grid_height
        pressure = value[2] / 0.0022768 * gravity_grid
        pressure *= (1.0 - 2.26e-5 * delta_height) ** 5.225
        gravity_site = 1.0 - 0.00266 * math.cos(2.0 * latitude) - 2.8e-7 * site_height_m
        value[2] = 0.0022768 * pressure / gravity_site
        value[3] *= math.exp(-delta_height / 2000.0)
        return value


def interpolate_time(
    before: Vmf3Estimate,
    after: Vmf3Estimate,
    epoch: datetime,
) -> Vmf3Estimate:
    """Linearly interpolate two site-wise VMF3 estimates in time."""
    if after.epoch < before.epoch:
        raise ValueError("VMF3 estimates are not time ordered")
    duration = (after.epoch - before.epoch).total_seconds()
    fraction = 0.0 if duration == 0 else (epoch - before.epoch).total_seconds() / duration
    fraction = min(max(fraction, 0.0), 1.0)

    def lerp(left: float, right: float) -> float:
        return left + fraction * (right - left)

    return Vmf3Estimate(
        epoch=epoch,
        latitude_deg=before.latitude_deg,
        longitude_deg=before.longitude_deg,
        ah=lerp(before.ah, after.ah),
        aw=lerp(before.aw, after.aw),
        zhd_m=lerp(before.zhd_m, after.zhd_m),
        zwd_m=lerp(before.zwd_m, after.zwd_m),
    )
