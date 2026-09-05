from datetime import date
from pathlib import Path
from unittest import TestCase

from gnss_weather.dates import date_range, gps_week, gps_week_start
from gnss_weather.download_products import build_product_plan


class DateTests(TestCase):
    def test_date_range_is_inclusive(self) -> None:
        self.assertEqual(
            date_range(date(2025, 6, 1), date(2025, 6, 3)),
            [date(2025, 6, 1), date(2025, 6, 2), date(2025, 6, 3)],
        )

    def test_gps_week(self) -> None:
        self.assertEqual(gps_week(date(1980, 1, 6)), 0)
        self.assertEqual(gps_week_start(date(2025, 6, 3)), date(2025, 6, 1))


class ProductPlanTests(TestCase):
    def test_three_day_final_product_plan(self) -> None:
        plan = build_product_plan(
            date(2025, 6, 1),
            date(2025, 6, 3),
            Path("products"),
            station="MATE00ITA",
            include_meteo=True,
        )
        kinds = [item.kind for item in plan]
        self.assertEqual(kinds.count("orbit"), 3)
        self.assertEqual(kinds.count("clock"), 3)
        self.assertEqual(kinds.count("earth_rotation"), 1)
        self.assertEqual(kinds.count("antenna"), 1)
        self.assertEqual(kinds.count("meteo"), 3)
        self.assertIn(
            "IGS0OPSFIN_20251520000_01D_15M_ORB.SP3.gz",
            plan[0].url,
        )

    def test_complete_plan_adds_multi_gnss_and_troposphere_data(self) -> None:
        plan = build_product_plan(
            date(2025, 6, 1),
            date(2025, 6, 3),
            Path("products"),
            station="MATE00ITA",
            include_multi_gnss=True,
            include_vmf3=True,
            include_reference_ztd=True,
            include_station_metadata=True,
        )
        kinds = [item.kind for item in plan]
        self.assertEqual(kinds.count("mgex_orbit"), 3)
        self.assertEqual(kinds.count("mgex_clock"), 3)
        self.assertEqual(kinds.count("mgex_bias"), 3)
        self.assertEqual(kinds.count("mgex_erp"), 3)
        self.assertEqual(kinds.count("mgex_attitude"), 3)
        self.assertEqual(kinds.count("vmf3"), 12)
        self.assertEqual(kinds.count("vmf3_orography"), 1)
        self.assertEqual(kinds.count("reference_ztd"), 3)
        self.assertEqual(kinds.count("station_metadata"), 1)

    def test_mgex_core_omits_large_optional_products(self) -> None:
        plan = build_product_plan(
            date(2026, 3, 1),
            date(2026, 3, 1),
            Path("products"),
            station="MATE00ITA",
            include_multi_gnss=True,
            include_precise_products=False,
            include_mgex_extras=False,
        )
        kinds = [item.kind for item in plan]
        self.assertNotIn("orbit", kinds)
        self.assertNotIn("clock", kinds)
        self.assertIn("mgex_orbit", kinds)
        self.assertIn("mgex_clock", kinds)
        self.assertIn("mgex_erp", kinds)
        self.assertNotIn("mgex_bias", kinds)
        self.assertNotIn("mgex_attitude", kinds)
