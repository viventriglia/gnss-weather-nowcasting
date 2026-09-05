from datetime import datetime, timedelta, timezone
from unittest import TestCase

from gnss_weather.atmosphere import (
    pwv_conversion_factor,
    weighted_mean_temperature,
    zenith_hydrostatic_delay,
    zwd_to_pwv_mm,
)
from gnss_weather.analyze_troposphere import build_atmospheric_series
from gnss_weather.models import AtmosphericEstimate, SurfaceWeather, ZtdEstimate
from gnss_weather.rain_potential import estimate_rain_potential
from gnss_weather.sinex_tro import parse_sinex_epoch


class SinexEpochTests(TestCase):
    def test_parse_sinex_epoch(self) -> None:
        self.assertEqual(
            parse_sinex_epoch("25:152:03600"),
            datetime(2025, 6, 1, 1, tzinfo=timezone.utc),
        )


class AtmospherePhysicsTests(TestCase):
    def test_saastamoinen_zhd_has_expected_magnitude(self) -> None:
        zhd = zenith_hydrostatic_delay(1013.25, 45.0, 0.0)
        self.assertAlmostEqual(zhd, 2.307, places=2)

    def test_zwd_to_pwv_conversion(self) -> None:
        tm = weighted_mean_temperature(293.15)
        factor = pwv_conversion_factor(tm)
        self.assertGreater(factor, 0.14)
        self.assertLess(factor, 0.18)
        self.assertAlmostEqual(zwd_to_pwv_mm(0.15, tm), 1000 * factor * 0.15)

    def test_ztd_source_is_preserved_in_atmospheric_series(self) -> None:
        epoch = datetime(2026, 3, 1, tzinfo=timezone.utc)
        result = build_atmospheric_series(
            [],
            [],
            40.6,
            16.7,
            500.0,
            None,  # unused when surface weather is supplied
            surface_weather=[SurfaceWeather(epoch, 960.0, 10.0)],
            ztd_estimates=[
                ZtdEstimate(epoch, "MATE", 2.3, source="rtklib_ppp_bias_corrected")
            ],
        )
        self.assertEqual(result[0].ztd_source, "rtklib_ppp_bias_corrected")


class RainPotentialTests(TestCase):
    def test_tendency_waits_for_a_complete_window(self) -> None:
        start = datetime(2025, 6, 1, tzinfo=timezone.utc)
        estimates = [
            AtmosphericEstimate(start, 2.3, 2.2, 0.10, None),
            AtmosphericEstimate(start + timedelta(minutes=5), 2.31, 2.2, 0.11, None),
        ]

        result = estimate_rain_potential(estimates)

        self.assertEqual(result[1].zwd_rate_mm_per_hour, 0.0)

    def test_increasing_wet_delay_increases_tendency(self) -> None:
        start = datetime(2025, 6, 1, tzinfo=timezone.utc)
        estimates = [
            AtmosphericEstimate(
                epoch=start + timedelta(hours=index),
                ztd_m=2.4 + zwd,
                zhd_m=2.3,
                zwd_m=zwd,
                sigma_m=0.003,
            )
            for index, zwd in enumerate((0.08, 0.09, 0.13))
        ]
        result = estimate_rain_potential(estimates)
        self.assertGreater(result[-1].zwd_rate_mm_per_hour, 0)
        self.assertGreater(result[-1].score, result[0].score)
