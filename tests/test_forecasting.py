from unittest import TestCase

import pandas as pd

from gnss_weather.forecasting import (
    best_f1_threshold,
    binary_metrics,
    brier_score,
    chronological_split,
    reliability_table,
    split_last_calendar_days,
    walk_forward_splits,
)


class ForecastingTests(TestCase):
    def test_chronological_split_is_contiguous_and_complete(self) -> None:
        frame = pd.DataFrame(
            {"value": range(20)},
            index=pd.date_range("2026-01-01", periods=20, freq="1h", tz="UTC"),
        )
        splits = chronological_split(frame)
        self.assertEqual([len(value) for value in splits.values()], [12, 3, 2, 3])
        reconstructed = pd.concat(splits.values())
        self.assertTrue(reconstructed.equals(frame))

    def test_binary_metrics(self) -> None:
        metrics = binary_metrics([0, 0, 1, 1], [0.1, 0.6, 0.4, 0.9], 0.5)
        self.assertEqual(metrics.true_positive, 1)
        self.assertEqual(metrics.false_positive, 1)
        self.assertEqual(metrics.false_negative, 1)
        self.assertEqual(metrics.true_negative, 1)
        self.assertEqual(metrics.f1, 0.5)

    def test_best_threshold_is_selected_on_probabilities(self) -> None:
        metrics = best_f1_threshold([0, 0, 1, 1], [0.1, 0.3, 0.4, 0.9])
        self.assertAlmostEqual(metrics.threshold, 0.4)
        self.assertEqual(metrics.f1, 1.0)

    def test_brier_score(self) -> None:
        self.assertAlmostEqual(brier_score([0, 1], [0.25, 0.75]), 0.0625)

    def test_reliability_table_preserves_all_rows(self) -> None:
        table = reliability_table(
            [0, 0, 0, 1, 0, 1, 1, 1],
            [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8],
            bins=4,
        )
        self.assertEqual(int(table["count"].sum()), 8)
        self.assertEqual(len(table), 4)

    def test_last_calendar_days_are_reserved_by_date(self) -> None:
        frame = pd.DataFrame(
            {"value": range(72)},
            index=pd.date_range("2026-01-01", periods=72, freq="1h", tz="UTC"),
        ).drop(pd.Timestamp("2026-01-02 12:00", tz="UTC"))
        split = split_last_calendar_days(frame, test_days=2)
        self.assertEqual(split["test"].index.min(), pd.Timestamp("2026-01-02", tz="UTC"))
        self.assertEqual(len(split["test"]), 47)
        self.assertLess(split["development"].index.max(), split["test"].index.min())

    def test_walk_forward_uses_expanding_train_and_disjoint_validation(self) -> None:
        frame = pd.DataFrame(
            {"value": range(24 * 400)},
            index=pd.date_range("2025-01-01", periods=24 * 400, freq="1h", tz="UTC"),
        )
        folds = walk_forward_splits(
            frame, n_splits=3, validation_days=30, gap_hours=1
        )
        self.assertEqual(len(folds), 3)
        self.assertLess(len(folds[0]["train"]), len(folds[-1]["train"]))
        for fold in folds:
            self.assertEqual(len(fold["validation"]), 24 * 30)
            self.assertLess(fold["train"].index.max(), fold["validation"].index.min())
        self.assertLess(
            folds[0]["validation"].index.max(), folds[1]["validation"].index.min()
        )
