from datetime import datetime, timezone
from unittest import TestCase

from gnss_weather.meteorology import interpolate_weather
from gnss_weather.models import SurfaceWeather


class MeteorologyTests(TestCase):
    def test_linear_interpolation(self) -> None:
        observations = [
            SurfaceWeather(datetime(2025, 6, 1, 0, tzinfo=timezone.utc), 1000.0, 10.0),
            SurfaceWeather(datetime(2025, 6, 1, 2, tzinfo=timezone.utc), 998.0, 14.0),
        ]
        result = interpolate_weather(
            observations,
            datetime(2025, 6, 1, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(result.pressure_hpa, 999.0)
        self.assertEqual(result.temperature_c, 12.0)
