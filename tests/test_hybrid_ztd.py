from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from gnss_weather.build_hybrid_ztd import (
    build_hybrid,
    read_ppp_configuration,
    validate_gap_fill,
)
from gnss_weather.compare_ztd_sources import DailyPpp
from gnss_weather.models import ZtdEstimate


def estimate(day: date, minute: int, ztd_m: float, source: str) -> ZtdEstimate:
    return ZtdEstimate(
        datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
        + timedelta(minutes=minute),
        "MATE00ITA",
        ztd_m,
        0.003,
        source=source,
    )


class HybridZtdTests(TestCase):
    def test_tro_has_priority_over_ppp(self) -> None:
        day = date(2026, 3, 1)
        tro = estimate(day, 0, 2.3, "igs_final_tro")
        ppp = estimate(day, 0, 2.4, "rtklib_ppp")
        hybrid, filled, missing = build_hybrid(
            day, day, {day: [tro]}, {day: DailyPpp(day, [ppp])}, 100.0, 0.0
        )
        self.assertEqual(hybrid, [tro])
        self.assertEqual(filled, [])
        self.assertEqual(missing, [])

    def test_leave_one_day_out_removes_stable_bias(self) -> None:
        days = [date(2026, 3, 1), date(2026, 3, 2)]
        igs = {day: [estimate(day, 0, 2.3, "igs_final_tro")] for day in days}
        ppp = {
            day: DailyPpp(day, [estimate(day, 0, 2.314, "rtklib_ppp")])
            for day in days
        }
        report = validate_gap_fill(ppp, igs, 0.0)
        self.assertEqual(report["recommended_bias_correction_mm"], 14.0)
        self.assertEqual(report["leave_one_day_out_overall"]["rmse_mm"], 0.0)

    def test_missing_tro_day_is_filled_with_bias_corrected_ppp(self) -> None:
        day = date(2026, 3, 3)
        ppp = estimate(day, 0, 2.314, "rtklib_ppp")
        hybrid, filled, missing = build_hybrid(
            day, day, {}, {day: DailyPpp(day, [ppp])}, 14.0, 0.0
        )
        self.assertAlmostEqual(hybrid[0].ztd_m, 2.3)
        self.assertEqual(hybrid[0].source, "rtklib_ppp_bias_corrected")
        self.assertEqual(filled, [day])
        self.assertEqual(missing, [])

    def test_ppp_configuration_reports_actual_constellations(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "rtklib.conf"
            path.write_text(
                "pos1-navsys        =9\npos1-elmask        =10\n",
                encoding="ascii",
            )
            result = read_ppp_configuration(path)
        self.assertEqual(result["systems"], ["gps", "galileo"])
        self.assertEqual(result["elevation_mask_deg"], 10.0)
