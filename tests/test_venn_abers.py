from unittest import TestCase

import numpy as np

from gnss_weather.venn_abers import calibrate_venn_abers, rolling_calibrate_venn_abers


class VennAbersTests(TestCase):
    def test_binary_manual_calibration_accepts_positive_class_scores(self) -> None:
        result = calibrate_venn_abers(
            p_cal=[0.05, 0.10, 0.20, 0.35, 0.70, 0.85, 0.95],
            y_cal=[0, 0, 0, 1, 0, 1, 1],
            p_test=[0.15, 0.50, 0.90],
        )
        self.assertEqual(result.probability.shape, (3,))
        self.assertTrue(np.isfinite(result.probability).all())
        self.assertTrue(((result.probability >= 0) & (result.probability <= 1)).all())
        self.assertTrue((result.p0 <= result.p1).all())
        self.assertTrue((result.multiprobability_width >= 0).all())

    def test_calibration_requires_both_classes(self) -> None:
        with self.assertRaisesRegex(ValueError, "both binary classes"):
            calibrate_venn_abers([0.1, 0.2], [0, 0], [0.3])

    def test_probability_rows_must_sum_to_one(self) -> None:
        with self.assertRaisesRegex(ValueError, "sum to one"):
            calibrate_venn_abers(
                np.array([[0.8, 0.3], [0.2, 0.8]]),
                [0, 1],
                [0.5],
            )

    def test_rolling_calibration_uses_completed_previous_days(self) -> None:
        history_times = np.arange(
            np.datetime64("2026-01-01"), np.datetime64("2026-01-05"), np.timedelta64(12, "h")
        )
        test_times = np.arange(
            np.datetime64("2026-01-05"), np.datetime64("2026-01-07"), np.timedelta64(12, "h")
        )
        result = rolling_calibrate_venn_abers(
            p_history=np.linspace(0.05, 0.75, len(history_times)),
            y_history=[0, 0, 1, 0, 1, 0, 1, 0],
            history_times=history_times,
            p_test=[0.2, 0.8, 0.3, 0.7],
            y_test=[1, 0, 0, 1],
            test_times=test_times,
            window_days=3,
            min_per_class=1,
        )
        self.assertEqual(result.probability.shape, (4,))
        self.assertTrue(np.all(result.calibration_size[:2] == 6))
        self.assertTrue(np.all(result.calibration_size[2:] == 6))
        self.assertTrue(np.all(result.effective_window_days == 3))

    def test_rolling_calibration_expands_sparse_window(self) -> None:
        history_times = np.arange(
            np.datetime64("2026-01-01"), np.datetime64("2026-01-11"), np.timedelta64(1, "D")
        )
        result = rolling_calibrate_venn_abers(
            p_history=[0.05, 0.80, 0.10, 0.90, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45],
            y_history=[0, 1, 0, 1, 0, 0, 0, 0, 0, 0],
            history_times=history_times,
            p_test=[0.4],
            y_test=[0],
            test_times=[np.datetime64("2026-01-11")],
            window_days=3,
            fallback_window_days=[10],
            min_per_class=2,
        )
        self.assertEqual(result.effective_window_days[0], 10)
        self.assertEqual(result.calibration_size[0], 10)
