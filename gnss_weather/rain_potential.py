"""Transparent heuristic features for precipitation potential from GNSS ZWD."""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from datetime import timedelta

from .models import AtmosphericEstimate, RainPotential


@dataclass(frozen=True)
class RainPotentialConfig:
    """Weights for the exploratory ZWD precipitation-potential index."""

    moisture_weight: float = 0.8
    tendency_weight: float = 0.2
    tendency_scale_mm_per_hour: float = 1.5
    tendency_window: timedelta = timedelta(hours=1)

    def __post_init__(self) -> None:
        if not math.isclose(self.moisture_weight + self.tendency_weight, 1.0):
            raise ValueError("rain-potential weights must sum to one")
        if self.tendency_scale_mm_per_hour <= 0:
            raise ValueError("tendency scale must be positive")


def _percentile_rank(sorted_values: list[float], value: float) -> float:
    if len(sorted_values) == 1:
        return 0.5
    left = bisect.bisect_left(sorted_values, value)
    right = bisect.bisect_right(sorted_values, value)
    return ((left + right) / 2.0) / len(sorted_values)


def estimate_rain_potential(
    estimates: list[AtmosphericEstimate],
    config: RainPotentialConfig = RainPotentialConfig(),
) -> list[RainPotential]:
    """Build a station-relative moisture/tendency index in the interval [0, 1].

    This is intentionally not a rain detector or a rain-rate retrieval. The
    moisture component is the empirical percentile of ZWD in the supplied
    baseline. A meaningful operational baseline should cover at least one
    season, not the three-day demonstration dataset.
    """
    if not estimates:
        return []
    ordered = sorted(estimates, key=lambda item: item.epoch)
    baseline = sorted(item.zwd_m for item in ordered)
    results: list[RainPotential] = []

    previous_index = 0
    for index, item in enumerate(ordered):
        target_epoch = item.epoch - config.tendency_window
        # Do not extrapolate a short (for example five-minute) change to one
        # hour at the beginning of the series: there is not enough history yet.
        if ordered[0].epoch > target_epoch:
            previous = item
        else:
            while (
                previous_index + 1 < index
                and ordered[previous_index + 1].epoch <= target_epoch
            ):
                previous_index += 1
            previous = ordered[previous_index]
        elapsed_hours = max((item.epoch - previous.epoch).total_seconds() / 3600.0, 1e-9)
        rate = 1000.0 * (item.zwd_m - previous.zwd_m) / elapsed_hours
        tendency = max(0.0, math.tanh(rate / config.tendency_scale_mm_per_hour))
        moisture = _percentile_rank(baseline, item.zwd_m)
        score = config.moisture_weight * moisture + config.tendency_weight * tendency
        category = "low" if score < 0.4 else "elevated" if score < 0.7 else "high"
        results.append(
            RainPotential(
                epoch=item.epoch,
                score=score,
                category=category,
                moisture_percentile=moisture,
                zwd_rate_mm_per_hour=rate,
            )
        )
    return results
