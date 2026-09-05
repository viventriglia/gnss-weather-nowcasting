"""Tropospheric delay and precipitable-water conversion functions."""

from __future__ import annotations

import math

# Water-vapour refractivity constants expressed for pressure in Pa.
K2_PRIME_K_PER_PA = 0.22
K3_K2_PER_PA = 3739.0
R_WATER_VAPOUR_J_PER_KG_K = 461.5
RHO_LIQUID_WATER_KG_PER_M3 = 1000.0


def zenith_hydrostatic_delay(
    pressure_hpa: float,
    latitude_deg: float,
    height_m: float,
) -> float:
    """Compute Saastamoinen zenith hydrostatic delay in metres."""
    if pressure_hpa <= 0:
        raise ValueError("pressure_hpa must be positive")
    latitude_rad = math.radians(latitude_deg)
    denominator = (
        1.0
        - 0.00266 * math.cos(2.0 * latitude_rad)
        - 0.00028 * height_m / 1000.0
    )
    return 0.0022768 * pressure_hpa / denominator


def weighted_mean_temperature(surface_temperature_k: float) -> float:
    """Estimate atmospheric weighted mean temperature with the Bevis relation."""
    if surface_temperature_k <= 0:
        raise ValueError("surface_temperature_k must be positive")
    return 70.2 + 0.72 * surface_temperature_k


def pwv_conversion_factor(weighted_mean_temperature_k: float) -> float:
    """Return the dimensionless factor Π converting ZWD into PWV."""
    if weighted_mean_temperature_k <= 0:
        raise ValueError("weighted_mean_temperature_k must be positive")
    denominator = RHO_LIQUID_WATER_KG_PER_M3 * R_WATER_VAPOUR_J_PER_KG_K * (
        K2_PRIME_K_PER_PA + K3_K2_PER_PA / weighted_mean_temperature_k
    )
    return 1_000_000.0 / denominator


def zwd_to_pwv_mm(zwd_m: float, weighted_mean_temperature_k: float) -> float:
    """Convert zenith wet delay in metres to precipitable water in millimetres."""
    if zwd_m < 0:
        raise ValueError("zwd_m must not be negative")
    return 1000.0 * pwv_conversion_factor(weighted_mean_temperature_k) * zwd_m

