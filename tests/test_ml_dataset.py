from datetime import datetime, timedelta, timezone
from unittest import TestCase

import pandas as pd

from gnss_weather.ml_dataset import (
    add_next_hour_target,
    add_time_series_features,
    solar_zenith_angle,
)


class MlDatasetTests(TestCase):
    def _features(self) -> tuple[pd.DataFrame, tuple[str, ...]]:
        index = pd.date_range(
            "2026-03-01T00:00:00Z", periods=49, freq="5min"
        )
        base = pd.DataFrame(
            {
                "pwv_mm": range(49),
                "zwd_mm": range(100, 149),
                "ztd_mm": range(2300, 2349),
                "pressure_hpa": 960.0,
                "temperature_c": 10.0,
                "ztd_sigma_mm": 3.0,
                "ztd_source": "igs_final_tro",
            },
            index=index,
        )
        return add_time_series_features(base)

    def test_deltas_use_exact_past_epochs(self) -> None:
        featured, _ = self._features()
        epoch = pd.Timestamp("2026-03-01T03:00:00Z")
        self.assertEqual(featured.loc[epoch, "pwv_mm_delta_30m"], 6)
        self.assertEqual(featured.loc[epoch, "pwv_mm_delta_1h"], 12)
        self.assertEqual(featured.loc[epoch, "pwv_mm_delta_3h"], 36)

    def test_rain_at_h_plus_one_labels_features_at_h(self) -> None:
        featured, columns = self._features()
        era5_index = pd.date_range(
            "2026-03-01T00:00:00Z", periods=6, freq="1h"
        )
        era5 = pd.DataFrame(
            {"rain_mm": [0.0, 0.2, 0.0, 0.1, 0.3, 0.0]},
            index=era5_index,
        )
        result = add_next_hour_target(featured, era5, columns, 0.1)

        epoch = pd.Timestamp("2026-03-01T03:00:00Z")
        self.assertEqual(result.loc[epoch, "rain_next_hour_mm"], 0.3)
        self.assertEqual(result.loc[epoch, "is_rain"], 1)
        self.assertEqual(
            result.loc[epoch, "target_window_end_utc"],
            epoch + pd.Timedelta(hours=1),
        )

    def test_threshold_is_strictly_greater_than(self) -> None:
        featured, columns = self._features()
        era5 = pd.DataFrame(
            {"rain_mm": [0.0, 0.0, 0.0, 0.0, 0.1]},
            index=pd.date_range("2026-03-01T00:00:00Z", periods=5, freq="1h"),
        )
        result = add_next_hour_target(featured, era5, columns, 0.1)
        self.assertEqual(result.loc[pd.Timestamp("2026-03-01T03:00:00Z"), "is_rain"], 0)

    def test_solar_zenith_is_lower_near_local_noon(self) -> None:
        epochs = pd.DatetimeIndex(
            ["2026-06-21T00:00:00Z", "2026-06-21T11:00:00Z"]
        )
        zenith = solar_zenith_angle(epochs, latitude_deg=40.65, longitude_deg=16.70)
        self.assertGreater(zenith[0], 90.0)
        self.assertLess(zenith[1], 25.0)
