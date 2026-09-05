"""Domain models used by the tropospheric analysis pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ZtdEstimate:
    """One zenith total delay estimate in SI units."""

    epoch: datetime
    station: str
    ztd_m: float
    sigma_m: float | None = None
    gradient_north_m: float | None = None
    gradient_east_m: float | None = None
    source: str = "unknown"


@dataclass(frozen=True)
class Vmf3Estimate:
    """VMF3 parameters interpolated at one site and epoch."""

    epoch: datetime
    latitude_deg: float
    longitude_deg: float
    ah: float
    aw: float
    zhd_m: float
    zwd_m: float


@dataclass(frozen=True)
class AtmosphericEstimate:
    """Physical atmospheric quantities derived from a ZTD observation."""

    epoch: datetime
    ztd_m: float
    zhd_m: float
    zwd_m: float
    sigma_m: float | None
    model_zwd_m: float | None = None
    pwv_mm: float | None = None
    zhd_source: str = "unknown"
    pressure_hpa: float | None = None
    surface_temperature_c: float | None = None
    ztd_source: str = "unknown"


@dataclass(frozen=True)
class SurfaceWeather:
    """Surface meteorological observation at the GNSS station."""

    epoch: datetime
    pressure_hpa: float
    temperature_c: float
    rain_mm: float | None = None


@dataclass(frozen=True)
class RainPotential:
    """Heuristic precipitation-potential result, not a rain-rate estimate."""

    epoch: datetime
    score: float
    category: str
    moisture_percentile: float
    zwd_rate_mm_per_hour: float
