"""Leakage-safe evaluation helpers for chronological rain forecasting."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class BinaryMetrics:
    threshold: float
    true_positive: int
    false_positive: int
    false_negative: int
    true_negative: int
    precision: float
    recall: float
    f1: float


def chronological_split(
    frame: pd.DataFrame,
    ratios: Sequence[float] = (0.60, 0.15, 0.10, 0.15),
    names: Sequence[str] = ("train", "validation", "calibration", "test"),
) -> dict[str, pd.DataFrame]:
    """Split an already chronological frame into contiguous, disjoint blocks."""
    if len(ratios) != len(names) or not ratios:
        raise ValueError("ratios and names must have equal non-zero length")
    if any(value <= 0 for value in ratios) or not np.isclose(sum(ratios), 1.0):
        raise ValueError("ratios must be positive and sum to one")
    if len(frame) < len(ratios):
        raise ValueError("not enough rows for the requested split")
    if not frame.index.is_monotonic_increasing:
        raise ValueError("frame must be sorted chronologically")

    boundaries = [0]
    cumulative = 0.0
    for ratio in ratios[:-1]:
        cumulative += ratio
        boundaries.append(int(len(frame) * cumulative))
    boundaries.append(len(frame))
    return {
        name: frame.iloc[left:right].copy()
        for name, left, right in zip(
            names, boundaries[:-1], boundaries[1:], strict=True
        )
    }


def split_last_calendar_days(
    frame: pd.DataFrame, test_days: int = 30
) -> dict[str, pd.DataFrame]:
    """Reserve the last complete calendar-day window as an untouched test set."""
    if test_days < 1:
        raise ValueError("test_days must be positive")
    if frame.empty:
        raise ValueError("frame must not be empty")
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("frame must have a DatetimeIndex")
    if not frame.index.is_monotonic_increasing:
        raise ValueError("frame must be sorted chronologically")

    test_start = frame.index.max().normalize() - pd.Timedelta(days=test_days - 1)
    development = frame.loc[frame.index < test_start].copy()
    test = frame.loc[frame.index >= test_start].copy()
    if development.empty or test.empty:
        raise ValueError("not enough data for the requested test window")
    return {"development": development, "test": test}


def walk_forward_splits(
    frame: pd.DataFrame,
    n_splits: int = 6,
    validation_days: int = 60,
    gap_hours: int = 1,
) -> list[dict[str, pd.DataFrame]]:
    """Build expanding-window folds ending at the end of the input period.

    Validation windows are contiguous calendar periods. Training expands at every
    fold and a small gap can purge samples whose forecast horizon touches the
    validation boundary.
    """
    if n_splits < 1 or validation_days < 1 or gap_hours < 0:
        raise ValueError("invalid walk-forward configuration")
    if frame.empty:
        raise ValueError("frame must not be empty")
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("frame must have a DatetimeIndex")
    if not frame.index.is_monotonic_increasing:
        raise ValueError("frame must be sorted chronologically")

    end_exclusive = frame.index.max().normalize() + pd.Timedelta(days=1)
    validation_span = pd.Timedelta(days=validation_days)
    first_validation_start = end_exclusive - n_splits * validation_span
    gap = pd.Timedelta(hours=gap_hours)
    if first_validation_start <= frame.index.min():
        raise ValueError("not enough history before the first validation window")

    folds: list[dict[str, pd.DataFrame]] = []
    for fold_number in range(n_splits):
        validation_start = first_validation_start + fold_number * validation_span
        validation_end = validation_start + validation_span
        train = frame.loc[frame.index < validation_start - gap].copy()
        validation = frame.loc[
            (frame.index >= validation_start) & (frame.index < validation_end)
        ].copy()
        if train.empty or validation.empty:
            raise ValueError(f"empty train or validation block in fold {fold_number}")
        folds.append({"train": train, "validation": validation})
    return folds


def _binary_arrays(
    y_true: Sequence[int], probability: Sequence[float]
) -> tuple[np.ndarray, np.ndarray]:
    truth = np.asarray(y_true, dtype=np.int8)
    predicted = np.asarray(probability, dtype=float)
    if truth.ndim != 1 or predicted.ndim != 1 or len(truth) != len(predicted):
        raise ValueError("truth and probability must be equal-length vectors")
    if len(truth) == 0:
        raise ValueError("metrics require at least one observation")
    if not np.isin(truth, (0, 1)).all():
        raise ValueError("truth must be binary")
    if not np.isfinite(predicted).all() or ((predicted < 0) | (predicted > 1)).any():
        raise ValueError("probabilities must be finite and in [0, 1]")
    return truth, predicted


def binary_metrics(
    y_true: Sequence[int], probability: Sequence[float], threshold: float
) -> BinaryMetrics:
    truth, predicted = _binary_arrays(y_true, probability)
    if not 0 <= threshold <= 1:
        raise ValueError("threshold must be in [0, 1]")
    label = predicted >= threshold
    positive = truth == 1
    tp = int(np.sum(label & positive))
    fp = int(np.sum(label & ~positive))
    fn = int(np.sum(~label & positive))
    tn = int(np.sum(~label & ~positive))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = (
        2.0 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )
    return BinaryMetrics(
        threshold=float(threshold),
        true_positive=tp,
        false_positive=fp,
        false_negative=fn,
        true_negative=tn,
        precision=precision,
        recall=recall,
        f1=f1,
    )


def best_f1_threshold(
    y_true: Sequence[int], probability: Sequence[float]
) -> BinaryMetrics:
    """Select a threshold on validation data, breaking F1 ties by precision."""
    truth, predicted = _binary_arrays(y_true, probability)
    thresholds = np.unique(np.concatenate(([0.0], predicted, [1.0])))
    candidates = [binary_metrics(truth, predicted, value) for value in thresholds]
    return max(candidates, key=lambda value: (value.f1, value.precision, value.threshold))


def brier_score(y_true: Sequence[int], probability: Sequence[float]) -> float:
    truth, predicted = _binary_arrays(y_true, probability)
    return float(np.mean((predicted - truth) ** 2))


def reliability_table(
    y_true: Sequence[int], probability: Sequence[float], bins: int = 10
) -> pd.DataFrame:
    """Return equal-frequency reliability bins with observed event rates."""
    truth, predicted = _binary_arrays(y_true, probability)
    if bins < 2:
        raise ValueError("at least two bins are required")
    frame = pd.DataFrame({"is_rain": truth, "probability": predicted})
    frame["bin"] = pd.qcut(frame["probability"], q=bins, duplicates="drop")
    return (
        frame.groupby("bin", observed=True)
        .agg(
            predicted_probability=("probability", "mean"),
            observed_frequency=("is_rain", "mean"),
            count=("is_rain", "size"),
        )
        .reset_index(drop=True)
    )
