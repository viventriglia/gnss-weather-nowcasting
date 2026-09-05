from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from gnss_weather.rtklib_stat import (
    gps_week_tow_to_utc,
    read_rtklib_ztd,
    read_ztd_csv,
    write_ztd_csv,
)


class RtklibStatTests(TestCase):
    def test_gps_time_is_converted_to_utc(self) -> None:
        self.assertEqual(
            gps_week_tow_to_utc(0, 0.0),
            datetime(1980, 1, 6, tzinfo=timezone.utc),
        )

    def test_trop_record(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "solution.stat"
            path.write_text("$TROP,2369,18.000,6,1,2.3456,0.0042\n", encoding="ascii")
            result = read_rtklib_ztd(path, "mate00ita")

        self.assertEqual(result[0].epoch, datetime(2025, 6, 1, tzinfo=timezone.utc))
        self.assertEqual(result[0].ztd_m, 2.3456)
        self.assertEqual(result[0].sigma_m, 0.0042)

    def test_normalized_csv_round_trip(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "ztd.csv"
            source = read_rtklib_ztd(
                self._write_stat(Path(directory) / "solution.stat"), "MATE00ITA"
            )
            write_ztd_csv(path, source)
            result = read_ztd_csv(path)
        self.assertEqual(result, source)

    @staticmethod
    def _write_stat(path: Path) -> Path:
        path.write_text("$TROP,2369,18.000,6,1,2.3456,0.0042\n", encoding="ascii")
        return path
