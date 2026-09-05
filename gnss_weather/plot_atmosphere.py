"""Plot the atmospheric time series produced by analyze_troposphere."""

from __future__ import annotations

import argparse
import csv
import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt


@dataclass(frozen=True)
class AtmosphericSeries:
    epochs: list[datetime]
    ztd_mm: list[float]
    zhd_mm: list[float]
    zwd_mm: list[float]
    pwv_mm: list[float]
    rain_potential: list[float]


def _optional_float(value: str | None) -> float:
    return float("nan") if value in (None, "") else float(value)


def read_atmospheric_csv(path: Path) -> AtmosphericSeries:
    """Read the derived atmospheric CSV in chronological order."""
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError(f"No atmospheric estimates found in {path}")

    rows.sort(key=lambda row: datetime.fromisoformat(row["epoch_utc"]))
    return AtmosphericSeries(
        epochs=[datetime.fromisoformat(row["epoch_utc"]) for row in rows],
        ztd_mm=[1000.0 * float(row["ztd_m"]) for row in rows],
        zhd_mm=[1000.0 * float(row["zhd_m"]) for row in rows],
        zwd_mm=[1000.0 * float(row["zwd_m"]) for row in rows],
        pwv_mm=[_optional_float(row.get("pwv_mm")) for row in rows],
        rain_potential=[_optional_float(row.get("rain_potential")) for row in rows],
    )


def break_time_gaps(
    epochs: list[datetime], values: list[float], gap_factor: float = 3.0
) -> tuple[list[datetime], list[float]]:
    """Insert NaNs so plotting does not connect missing observation periods."""
    if len(epochs) < 3:
        return epochs, values
    intervals = [
        (right - left).total_seconds()
        for left, right in zip(epochs[:-1], epochs[1:], strict=True)
        if right > left
    ]
    if not intervals:
        return epochs, values
    expected = statistics.median_low(intervals)
    plot_epochs: list[datetime] = [epochs[0]]
    plot_values: list[float] = [values[0]]
    for previous, epoch, value in zip(
        epochs[:-1], epochs[1:], values[1:], strict=True
    ):
        if (epoch - previous).total_seconds() > expected * gap_factor:
            plot_epochs.append(previous + timedelta(seconds=expected))
            plot_values.append(math.nan)
        plot_epochs.append(epoch)
        plot_values.append(value)
    return plot_epochs, plot_values


def plot_atmospheric_series(
    series: AtmosphericSeries,
    output: Path,
    title: str = "MATE00ITA — atmosfera da ZTD IGS",
    dpi: int = 160,
) -> None:
    """Write a three-panel diagnostic plot without implying measured rain."""
    figure, axes = plt.subplots(
        3,
        1,
        figsize=(12, 9),
        sharex=True,
        constrained_layout=True,
        height_ratios=(1.0, 1.25, 0.8),
    )
    figure.suptitle(title, fontsize=15)

    delay_axis = axes[0]
    ztd_epochs, ztd_values = break_time_gaps(series.epochs, series.ztd_mm)
    zhd_epochs, zhd_values = break_time_gaps(series.epochs, series.zhd_mm)
    delay_axis.plot(ztd_epochs, ztd_values, label="ZTD osservato", linewidth=1.5)
    delay_axis.plot(zhd_epochs, zhd_values, label="ZHD", linewidth=1.2)
    delay_axis.set_ylabel("Ritardo [mm]")
    delay_axis.legend(loc="best", ncols=2)
    delay_axis.grid(alpha=0.25)

    moisture_axis = axes[1]
    zwd_epochs, zwd_values = break_time_gaps(series.epochs, series.zwd_mm)
    moisture_axis.plot(
        zwd_epochs,
        zwd_values,
        color="tab:blue",
        label="ZWD",
        linewidth=1.5,
    )
    moisture_axis.set_ylabel("ZWD [mm]", color="tab:blue")
    moisture_axis.tick_params(axis="y", labelcolor="tab:blue")
    moisture_axis.grid(alpha=0.25)
    pwv_axis = moisture_axis.twinx()
    pwv_epochs, pwv_values = break_time_gaps(series.epochs, series.pwv_mm)
    pwv_axis.plot(
        pwv_epochs,
        pwv_values,
        color="tab:green",
        label="PWV",
        linewidth=1.2,
        linestyle="--",
    )
    pwv_axis.set_ylabel("PWV [mm]", color="tab:green")
    pwv_axis.tick_params(axis="y", labelcolor="tab:green")
    lines = moisture_axis.lines + pwv_axis.lines
    moisture_axis.legend(lines, [line.get_label() for line in lines], loc="best")

    potential_axis = axes[2]
    potential_axis.axhspan(0.0, 0.4, color="tab:green", alpha=0.10)
    potential_axis.axhspan(0.4, 0.7, color="tab:orange", alpha=0.12)
    potential_axis.axhspan(0.7, 1.0, color="tab:red", alpha=0.10)
    rain_epochs, rain_values = break_time_gaps(series.epochs, series.rain_potential)
    potential_axis.plot(
        rain_epochs,
        rain_values,
        color="tab:purple",
        linewidth=1.2,
    )
    potential_axis.set_ylim(0.0, 1.0)
    potential_axis.set_ylabel("Indice\n[0–1]")
    potential_axis.set_xlabel("Tempo UTC")
    potential_axis.grid(alpha=0.25)
    potential_axis.text(
        0.01,
        0.94,
        "Indice relativo non calibrato — non è probabilità né pioggia misurata",
        transform=potential_axis.transAxes,
        va="top",
        fontsize=9,
    )
    potential_axis.xaxis.set_major_locator(mdates.AutoDateLocator())
    potential_axis.xaxis.set_major_formatter(
        mdates.ConciseDateFormatter(potential_axis.xaxis.get_major_locator())
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=dpi)
    plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/derived/atmosphere.csv"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/derived/atmosphere.png"),
    )
    parser.add_argument("--station", default="MATE00ITA")
    parser.add_argument("--title", help="figure title; defaults to the station name")
    parser.add_argument("--dpi", type=int, default=160)
    args = parser.parse_args()

    series = read_atmospheric_csv(args.input)
    title = args.title or f"{args.station.upper()} — atmosfera da ZTD IGS"
    plot_atmospheric_series(series, args.output, title, args.dpi)
    print(f"Wrote {len(series.epochs)} samples to {args.output}")


if __name__ == "__main__":
    main()
