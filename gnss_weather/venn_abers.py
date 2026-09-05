"""Small, validated wrapper around manual inductive Venn-Abers calibration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
from venn_abers import VennAbersCalibrator


@dataclass(frozen=True)
class VennAbersResult:
    """Binary calibrated probability and Venn multiprobability outputs."""

    probability: np.ndarray
    p0: np.ndarray
    p1: np.ndarray

    @property
    def multiprobability_width(self) -> np.ndarray:
        return self.p1 - self.p0


@dataclass(frozen=True)
class RollingVennAbersResult(VennAbersResult):
    """Causal daily replay of Venn-Abers with a trailing calibration window."""

    effective_window_days: np.ndarray
    calibration_size: np.ndarray


def _probability_matrix(values: Sequence[float] | np.ndarray) -> np.ndarray:
    probabilities = np.asarray(values, dtype=float)
    if probabilities.ndim == 1:
        probabilities = np.column_stack((1.0 - probabilities, probabilities))
    if probabilities.ndim != 2 or probabilities.shape[1] != 2:
        raise ValueError("binary probabilities must have shape (n,) or (n, 2)")
    if len(probabilities) == 0:
        raise ValueError("probabilities must not be empty")
    if not np.isfinite(probabilities).all():
        raise ValueError("probabilities must be finite")
    if ((probabilities < 0.0) | (probabilities > 1.0)).any():
        raise ValueError("probabilities must be in [0, 1]")
    if not np.allclose(probabilities.sum(axis=1), 1.0):
        raise ValueError("each binary probability row must sum to one")
    return probabilities


def calibrate_venn_abers(
    p_cal: Sequence[float] | np.ndarray,
    y_cal: Sequence[int] | np.ndarray,
    p_test: Sequence[float] | np.ndarray,
) -> VennAbersResult:
    """Apply manual inductive Venn-Abers to pre-computed classifier scores.

    Calibration labels must come from examples that were not used to fit or tune
    the underlying classifier. ``p0`` and ``p1`` are Venn multiprobabilities,
    not frequentist confidence bounds.
    """
    calibration_probability = _probability_matrix(p_cal)
    test_probability = _probability_matrix(p_test)
    labels = np.asarray(y_cal, dtype=np.int8)
    if labels.ndim != 1 or len(labels) != len(calibration_probability):
        raise ValueError("y_cal must match the calibration probabilities")
    if not np.isin(labels, (0, 1)).all() or len(np.unique(labels)) != 2:
        raise ValueError("y_cal must contain both binary classes")

    calibrator = VennAbersCalibrator(inductive=True)
    with np.errstate(divide="ignore", invalid="ignore"):
        calibrated, p0_p1 = calibrator.predict_proba(
            p_cal=calibration_probability,
            y_cal=labels,
            p_test=test_probability,
            p0_p1_output=True,
        )
    calibrated = np.asarray(calibrated, dtype=float)
    p0_p1 = np.asarray(p0_p1, dtype=float)
    if calibrated.shape != test_probability.shape or p0_p1.shape != test_probability.shape:
        raise RuntimeError("unexpected Venn-Abers output shape")
    return VennAbersResult(
        probability=calibrated[:, 1],
        p0=p0_p1[:, 0],
        p1=p0_p1[:, 1],
    )


def rolling_calibrate_venn_abers(
    p_history: Sequence[float] | np.ndarray,
    y_history: Sequence[int] | np.ndarray,
    history_times: Sequence[np.datetime64] | np.ndarray,
    p_test: Sequence[float] | np.ndarray,
    y_test: Sequence[int] | np.ndarray,
    test_times: Sequence[np.datetime64] | np.ndarray,
    *,
    window_days: int,
    fallback_window_days: Sequence[int] = (),
    min_per_class: int = 20,
) -> RollingVennAbersResult:
    """Replay daily rolling calibration without using future test labels.

    At the start of each UTC day, the calibrator sees the initial history and
    test labels from completed previous days only. If the requested trailing
    window has too few examples from either class, larger fallback windows are
    tried in the supplied order.
    """
    history_probability = _probability_matrix(p_history)
    test_probability = _probability_matrix(p_test)
    history_labels = _binary_labels(y_history, len(history_probability), "y_history")
    test_labels = _binary_labels(y_test, len(test_probability), "y_test", require_both=False)
    history_time = _datetime_array(history_times, len(history_probability), "history_times")
    test_time = _datetime_array(test_times, len(test_probability), "test_times")

    windows = tuple(dict.fromkeys((int(window_days), *(int(day) for day in fallback_window_days))))
    if not windows or windows[0] <= 0 or any(day <= 0 for day in windows):
        raise ValueError("calibration windows must be positive")
    if any(later <= earlier for earlier, later in zip(windows, windows[1:])):
        raise ValueError("fallback windows must be strictly increasing")
    if min_per_class < 1:
        raise ValueError("min_per_class must be positive")
    if len(test_time) > 1 and np.any(test_time[1:] < test_time[:-1]):
        raise ValueError("test_times must be sorted")
    if len(history_time) and history_time.max() >= test_time.min():
        raise ValueError("history_times must precede test_times")

    probability = np.empty(len(test_probability), dtype=float)
    p0 = np.empty(len(test_probability), dtype=float)
    p1 = np.empty(len(test_probability), dtype=float)
    effective_window = np.empty(len(test_probability), dtype=np.int16)
    calibration_size = np.empty(len(test_probability), dtype=np.int32)
    test_days = test_time.astype("datetime64[D]")

    for day in np.unique(test_days):
        test_indices = np.flatnonzero(test_days == day)
        prior_test = test_time < day
        available_probability = np.concatenate((history_probability, test_probability[prior_test]))
        available_labels = np.concatenate((history_labels, test_labels[prior_test]))
        available_times = np.concatenate((history_time, test_time[prior_test]))

        selected = None
        for candidate_days in windows:
            lower_bound = day - np.timedelta64(candidate_days, "D")
            mask = (available_times >= lower_bound) & (available_times < day)
            candidate_labels = available_labels[mask]
            counts = np.bincount(candidate_labels, minlength=2)
            if counts.min() >= min_per_class:
                selected = (candidate_days, mask)
                break
        if selected is None:
            raise ValueError(
                f"not enough examples of both classes before {day} "
                f"within the largest calibration window ({windows[-1]} days)"
            )

        selected_days, mask = selected
        result = calibrate_venn_abers(
            p_cal=available_probability[mask],
            y_cal=available_labels[mask],
            p_test=test_probability[test_indices],
        )
        probability[test_indices] = result.probability
        p0[test_indices] = result.p0
        p1[test_indices] = result.p1
        effective_window[test_indices] = selected_days
        calibration_size[test_indices] = int(mask.sum())

    return RollingVennAbersResult(
        probability=probability,
        p0=p0,
        p1=p1,
        effective_window_days=effective_window,
        calibration_size=calibration_size,
    )


def _binary_labels(
    values: Sequence[int] | np.ndarray,
    expected_length: int,
    name: str,
    *,
    require_both: bool = True,
) -> np.ndarray:
    labels = np.asarray(values, dtype=np.int8)
    if labels.ndim != 1 or len(labels) != expected_length:
        raise ValueError(f"{name} must match its probabilities")
    if not np.isin(labels, (0, 1)).all():
        raise ValueError(f"{name} must contain binary labels")
    if require_both and len(np.unique(labels)) != 2:
        raise ValueError(f"{name} must contain both binary classes")
    return labels


def _datetime_array(
    values: Sequence[np.datetime64] | np.ndarray,
    expected_length: int,
    name: str,
) -> np.ndarray:
    times = np.asarray(values, dtype="datetime64[ns]")
    if times.ndim != 1 or len(times) != expected_length:
        raise ValueError(f"{name} must match its probabilities")
    if np.isnat(times).any():
        raise ValueError(f"{name} must not contain NaT")
    return times
