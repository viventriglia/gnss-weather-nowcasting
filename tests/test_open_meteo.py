from datetime import date
from tempfile import TemporaryDirectory
from unittest import TestCase
from urllib.parse import parse_qs, urlparse

from gnss_weather.download_open_meteo import build_url, normalized_rows, write_csv
from gnss_weather.meteorology import read_surface_weather


class OpenMeteoTests(TestCase):
    def test_url_forces_era5_and_utc(self) -> None:
        query = parse_qs(
            urlparse(
                build_url(40.65, 16.70, date(2025, 6, 1), date(2025, 6, 2))
            ).query
        )
        self.assertEqual(query["models"], ["era5"])
        self.assertEqual(query["timezone"], ["GMT"])
        self.assertIn("surface_pressure", query["hourly"][0])

    def test_normalized_csv_is_compatible_with_weather_reader(self) -> None:
        payload = {
            "latitude": 40.625,
            "longitude": 16.75,
            "elevation": 502.0,
            "hourly": {
                "time": ["2025-06-01T00:00"],
                "temperature_2m": [18.2],
                "relative_humidity_2m": [72.0],
                "dew_point_2m": [13.1],
                "surface_pressure": [956.4],
                "precipitation": [0.3],
                "rain": [0.3],
                "total_column_integrated_water_vapour": [24.8],
            },
        }
        rows = normalized_rows(payload, "era5")
        self.assertEqual(rows[0]["epoch_utc"], "2025-06-01T00:00:00Z")
        self.assertEqual(rows[0]["total_column_water_vapour_kg_m2"], 24.8)
        with TemporaryDirectory() as directory:
            from pathlib import Path

            path = Path(directory) / "weather.csv"
            write_csv(path, rows)
            observations = read_surface_weather(path)
        self.assertEqual(observations[0].pressure_hpa, 956.4)
        self.assertEqual(observations[0].rain_mm, 0.3)
