from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from datetime import datetime, timezone
import math

from gnss_weather.plot_atmosphere import break_time_gaps, read_atmospheric_csv


class PlotAtmosphereTests(TestCase):
    def test_reader_sorts_epochs_and_converts_delays_to_mm(self) -> None:
        content = (
            "epoch_utc,ztd_m,zhd_m,zwd_m,pwv_mm,rain_potential\n"
            "2025-06-01T00:05:00+00:00,2.3,2.2,0.1,16,0.7\n"
            "2025-06-01T00:00:00+00:00,2.2,2.1,0.1,15,0.5\n"
        )
        with TemporaryDirectory() as directory:
            path = Path(directory) / "atmosphere.csv"
            path.write_text(content, encoding="utf-8")
            result = read_atmospheric_csv(path)

        self.assertEqual(result.epochs[0].minute, 0)
        self.assertEqual(result.ztd_mm, [2200.0, 2300.0])

    def test_daily_gap_is_not_connected(self) -> None:
        epochs = [
            datetime(2025, 6, 1, 0, 0, tzinfo=timezone.utc),
            datetime(2025, 6, 1, 0, 5, tzinfo=timezone.utc),
            datetime(2025, 6, 2, 0, 0, tzinfo=timezone.utc),
        ]
        plot_epochs, values = break_time_gaps(epochs, [1.0, 2.0, 3.0])
        self.assertEqual(len(plot_epochs), 4)
        self.assertTrue(math.isnan(values[2]))
