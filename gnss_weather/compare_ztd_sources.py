"""Export and plot RTKLIB PPP and IGS TRO ZTD as distinct time series."""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import statistics
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from .dates import date_range
from .models import ZtdEstimate
from .rtklib_stat import read_ztd_csv
from .sinex_tro import read_sinex_tro


@dataclass(frozen=True)
class DailyPpp:
    day: date
    estimates: list[ZtdEstimate]


def _iso_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _utc_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


def read_ppp_days(root: Path, station: str, start: date, end: date) -> list[DailyPpp]:
    result: list[DailyPpp] = []
    for day in date_range(start, end):
        path = root / station.upper() / day.isoformat() / "ztd.csv"
        estimates = [
            item
            for item in read_ztd_csv(path)
            if _utc_start(day) <= item.epoch < _utc_start(day + timedelta(days=1))
        ]
        result.append(DailyPpp(day, estimates))
    return result


def read_igs_days(
    root: Path,
    station: str,
    start: date,
    end: date,
) -> tuple[dict[date, list[ZtdEstimate]], list[date]]:
    available: dict[date, list[ZtdEstimate]] = {}
    missing: list[date] = []
    for day in date_range(start, end):
        year_doy = f"{day.year}{day.timetuple().tm_yday:03d}"
        paths = sorted(root.glob(f"IGS0OPSFIN_{year_doy}0000_01D_05M_{station.upper()}_TRO.TRO.gz"))
        if not paths:
            missing.append(day)
            continue
        available[day] = read_sinex_tro(paths[0])
    return available, missing


def write_ppp_csv(path: Path, days: list[DailyPpp], convergence_hours: float) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            ["epoch_utc", "run_day", "station", "ztd_m", "sigma_m", "converged", "source"]
        )
        for daily in days:
            convergence_end = _utc_start(daily.day) + timedelta(hours=convergence_hours)
            for item in daily.estimates:
                writer.writerow(
                    [
                        item.epoch.isoformat(),
                        daily.day.isoformat(),
                        item.station,
                        item.ztd_m,
                        item.sigma_m,
                        item.epoch >= convergence_end,
                        item.source,
                    ]
                )


def write_igs_csv(path: Path, days: dict[date, list[ZtdEstimate]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["epoch_utc", "day", "station", "ztd_m", "sigma_m", "source"])
        for day, estimates in sorted(days.items()):
            for item in estimates:
                writer.writerow(
                    [item.epoch.isoformat(), day.isoformat(), item.station, item.ztd_m, item.sigma_m, item.source]
                )


def validation_statistics(
    ppp_days: list[DailyPpp],
    igs_days: dict[date, list[ZtdEstimate]],
    convergence_hours: float,
) -> dict[str, object]:
    per_day: dict[str, dict[str, float | int]] = {}
    all_differences: list[float] = []
    for daily in ppp_days:
        reference = igs_days.get(daily.day)
        if not reference:
            continue
        convergence_end = _utc_start(daily.day) + timedelta(hours=convergence_hours)
        ppp = [item for item in daily.estimates if item.epoch >= convergence_end]
        epochs = [item.epoch for item in ppp]
        differences: list[float] = []
        for item in reference:
            if item.epoch < convergence_end or not epochs:
                continue
            index = bisect.bisect_left(epochs, item.epoch)
            candidates = [candidate for candidate in (index - 1, index) if 0 <= candidate < len(ppp)]
            nearest = min(candidates, key=lambda candidate: abs(epochs[candidate] - item.epoch))
            if abs(epochs[nearest] - item.epoch) <= timedelta(seconds=30):
                differences.append(1000.0 * (ppp[nearest].ztd_m - item.ztd_m))
        if differences:
            all_differences.extend(differences)
            per_day[daily.day.isoformat()] = _difference_summary(differences)
    return {
        "difference": "RTKLIB_PPP_minus_IGS_TRO",
        "unit": "mm",
        "convergence_hours_discarded": convergence_hours,
        "overall": _difference_summary(all_differences),
        "per_day": per_day,
    }


def _difference_summary(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0}
    bias = statistics.mean(values)
    return {
        "count": len(values),
        "bias": round(bias, 3),
        "standard_deviation": round(statistics.stdev(values), 3) if len(values) > 1 else 0.0,
        "rmse": round(math.sqrt(statistics.mean(value * value for value in values)), 3),
    }


def plot_sources(
    path: Path,
    station: str,
    ppp_days: list[DailyPpp],
    igs_days: dict[date, list[ZtdEstimate]],
    convergence_hours: float,
) -> None:
    figure, axes = plt.subplots(2, 1, figsize=(13, 8), sharex=True, constrained_layout=True)
    figure.suptitle(f"{station.upper()} — ZTD indipendenti da RTKLIB PPP e IGS TRO", fontsize=15)

    ppp_axis, igs_axis = axes
    for daily in ppp_days:
        cutoff = _utc_start(daily.day) + timedelta(hours=convergence_hours)
        raw = [item for item in daily.estimates if item.epoch < cutoff]
        converged = [item for item in daily.estimates if item.epoch >= cutoff]
        if raw:
            ppp_axis.plot(
                [item.epoch for item in raw],
                [1000.0 * item.ztd_m for item in raw],
                color="0.65",
                linewidth=0.8,
                label="convergenza esclusa" if daily == ppp_days[0] else None,
            )
        if converged:
            ppp_axis.plot(
                [item.epoch for item in converged],
                [1000.0 * item.ztd_m for item in converged],
                color="tab:blue",
                linewidth=1.1,
                label="RTKLIB PPP" if daily == ppp_days[0] else None,
            )
        ppp_axis.axvline(_utc_start(daily.day), color="0.8", linewidth=0.6)
    ppp_axis.set_ylabel("ZTD PPP [mm]")
    ppp_axis.grid(alpha=0.25)
    ppp_axis.legend(loc="best")

    first_igs = True
    for _, estimates in sorted(igs_days.items()):
        igs_axis.plot(
            [item.epoch for item in estimates],
            [1000.0 * item.ztd_m for item in estimates],
            color="tab:orange",
            linewidth=1.4,
            marker=".",
            markersize=2,
            label="IGS TRO" if first_igs else None,
        )
        first_igs = False
    igs_axis.set_ylabel("ZTD IGS [mm]")
    igs_axis.set_xlabel("Tempo UTC")
    igs_axis.grid(alpha=0.25)
    igs_axis.legend(loc="best")
    locator = mdates.AutoDateLocator()
    igs_axis.xaxis.set_major_locator(locator)
    igs_axis.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))

    figure.savefig(path, dpi=170)
    plt.close(figure)


def plot_ppp_only(
    path: Path,
    station: str,
    ppp_days: list[DailyPpp],
    convergence_hours: float,
) -> None:
    figure, axis = plt.subplots(figsize=(13, 5), constrained_layout=True)
    for daily in ppp_days:
        cutoff = _utc_start(daily.day) + timedelta(hours=convergence_hours)
        raw = [item for item in daily.estimates if item.epoch < cutoff]
        converged = [item for item in daily.estimates if item.epoch >= cutoff]
        if raw:
            axis.plot(
                [item.epoch for item in raw],
                [1000.0 * item.ztd_m for item in raw],
                color="0.65",
                linewidth=0.8,
                label="convergenza esclusa" if daily == ppp_days[0] else None,
            )
        if converged:
            axis.plot(
                [item.epoch for item in converged],
                [1000.0 * item.ztd_m for item in converged],
                color="tab:blue",
                linewidth=1.1,
                label="RTKLIB PPP" if daily == ppp_days[0] else None,
            )
    axis.set_title(f"{station.upper()} — ZTD da RTKLIB PPP")
    axis.set_ylabel("ZTD [mm]")
    axis.set_xlabel("Tempo UTC")
    axis.grid(alpha=0.25)
    axis.legend(loc="best")
    locator = mdates.AutoDateLocator()
    axis.xaxis.set_major_locator(locator)
    axis.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    figure.savefig(path, dpi=170)
    plt.close(figure)


def plot_igs_only(
    path: Path,
    station: str,
    igs_days: dict[date, list[ZtdEstimate]],
) -> None:
    figure, axis = plt.subplots(figsize=(13, 5), constrained_layout=True)
    first = True
    for _, estimates in sorted(igs_days.items()):
        axis.plot(
            [item.epoch for item in estimates],
            [1000.0 * item.ztd_m for item in estimates],
            color="tab:orange",
            linewidth=1.4,
            marker=".",
            markersize=2,
            label="IGS TRO" if first else None,
        )
        first = False
    axis.set_title(f"{station.upper()} — ZTD di riferimento IGS TRO")
    axis.set_ylabel("ZTD [mm]")
    axis.set_xlabel("Tempo UTC")
    axis.grid(alpha=0.25)
    axis.legend(loc="best")
    locator = mdates.AutoDateLocator()
    axis.xaxis.set_major_locator(locator)
    axis.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    figure.savefig(path, dpi=170)
    plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--station", default="MATE00ITA")
    parser.add_argument("--start", type=_iso_date, required=True)
    parser.add_argument("--end", type=_iso_date, required=True)
    parser.add_argument("--ppp-root", type=Path, default=Path("data/ppp"))
    parser.add_argument("--tro-root", type=Path, default=Path("data/products/reference_ztd"))
    parser.add_argument("--output", type=Path, default=Path("data/derived/ztd_comparison"))
    parser.add_argument("--convergence-hours", type=float, default=4.0)
    args = parser.parse_args()
    if args.end < args.start:
        parser.error("--end must not precede --start")
    if args.convergence_hours < 0:
        parser.error("--convergence-hours must not be negative")

    args.output.mkdir(parents=True, exist_ok=True)
    ppp_days = read_ppp_days(args.ppp_root, args.station, args.start, args.end)
    igs_days, missing = read_igs_days(args.tro_root, args.station, args.start, args.end)
    suffix = f"{args.start.isoformat()}_{args.end.isoformat()}"
    ppp_csv = args.output / f"ztd_rtklib_ppp_{suffix}.csv"
    igs_csv = args.output / f"ztd_igs_tro_{suffix}.csv"
    plot = args.output / f"ztd_sources_{suffix}.png"
    ppp_plot = args.output / f"ztd_rtklib_ppp_{suffix}.png"
    igs_plot = args.output / f"ztd_igs_tro_{suffix}.png"
    stats = args.output / f"ztd_validation_{suffix}.json"
    write_ppp_csv(ppp_csv, ppp_days, args.convergence_hours)
    write_igs_csv(igs_csv, igs_days)
    plot_sources(plot, args.station, ppp_days, igs_days, args.convergence_hours)
    plot_ppp_only(ppp_plot, args.station, ppp_days, args.convergence_hours)
    plot_igs_only(igs_plot, args.station, igs_days)
    report = validation_statistics(ppp_days, igs_days, args.convergence_hours)
    report["missing_igs_days"] = [day.isoformat() for day in missing]
    stats.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"RTKLIB PPP: {ppp_csv}")
    print(f"IGS TRO:    {igs_csv}")
    print(f"Plot:       {plot}")
    print(f"PPP plot:   {ppp_plot}")
    print(f"IGS plot:   {igs_plot}")
    print(f"Validation: {stats}")


if __name__ == "__main__":
    main()
