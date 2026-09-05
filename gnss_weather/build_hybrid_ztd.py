"""Build an IGS-TRO-first ZTD series and validate bias-corrected PPP gap filling."""

from __future__ import annotations

import argparse
import bisect
import json
import logging
import math
import statistics
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from .compare_ztd_sources import DailyPpp, read_igs_days
from .dates import date_range
from .download_products import build_product_plan, download_items
from .download_rinex import download_rinex
from .models import ZtdEstimate
from .rtklib_stat import read_ztd_csv, write_ztd_csv
from .run_rtklib_ppp import DEFAULT_IMAGE, SYSTEM_BITS, run_ppp


LOGGER = logging.getLogger(__name__)


def read_ppp_configuration(path: Path) -> dict[str, object] | None:
    """Read the RTKLIB constellation mask and elevation cutoff for one run."""
    if not path.exists():
        return None
    settings: dict[str, str] = {}
    for line in path.read_text(encoding="ascii", errors="replace").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        settings[key.strip()] = value.strip().split()[0]
    try:
        navsys = int(settings["pos1-navsys"])
        elevation_mask = float(settings["pos1-elmask"])
    except (KeyError, ValueError):
        return None
    systems = [name for name, bit in SYSTEM_BITS.items() if navsys & bit]
    return {
        "navsys": navsys,
        "systems": systems,
        "elevation_mask_deg": elevation_mask,
    }


def _ppp_configuration_matches(path: Path, elevation_mask_deg: float) -> bool:
    configuration = read_ppp_configuration(path)
    if configuration is None:
        return False
    expected_navsys = SYSTEM_BITS["gps"] + SYSTEM_BITS["galileo"]
    return configuration["navsys"] == expected_navsys and math.isclose(
        float(configuration["elevation_mask_deg"]), elevation_mask_deg
    )


def _iso_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _utc_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


def read_available_ppp_days(
    root: Path,
    station: str,
    start: date,
    end: date,
    required_elevation_mask_deg: float | None = None,
) -> dict[date, DailyPpp]:
    result: dict[date, DailyPpp] = {}
    for day in date_range(start, end):
        day_root = root / station.upper() / day.isoformat()
        path = day_root / "ztd.csv"
        if not path.exists():
            continue
        if required_elevation_mask_deg is not None and not _ppp_configuration_matches(
            day_root / "rtklib.conf", required_elevation_mask_deg
        ):
            LOGGER.warning(
                "Ignoring PPP for %s: expected GPS+Galileo with %.1f degree mask",
                day,
                required_elevation_mask_deg,
            )
            continue
        end_exclusive = _utc_start(day + timedelta(days=1))
        estimates = [
            item
            for item in read_ztd_csv(path)
            if _utc_start(day) <= item.epoch < end_exclusive
        ]
        if estimates:
            result[day] = DailyPpp(day, estimates)
    return result


def run_ppp_for_missing_tro_days(
    days: list[date],
    station: str,
    project_root: Path,
    ppp_root: Path,
    *,
    elevation_mask_deg: float = 10.0,
    image: str = DEFAULT_IMAGE,
    build_image: bool = False,
) -> None:
    """Download PPP inputs and run GPS+Galileo RTKLIB only for missing TRO days."""
    project_root = project_root.resolve()
    try:
        output_root = ppp_root.resolve().relative_to(project_root)
    except ValueError as exc:
        raise ValueError("--ppp-root must be inside --project-root") from exc

    for index, day in enumerate(days):
        day_root = ppp_root / station.upper() / day.isoformat()
        expected = day_root / "ztd.csv"
        if expected.exists() and _ppp_configuration_matches(
            day_root / "rtklib.conf", elevation_mask_deg
        ):
            LOGGER.info(
                "Matching GPS+Galileo PPP already present for missing TRO day %s",
                day,
            )
            continue
        if expected.exists():
            LOGGER.info(
                "Re-running %s because its existing PPP configuration does not match",
                day,
            )

        LOGGER.info("Preparing GPS+Galileo PPP fallback for %s", day)
        download_rinex(
            station,
            day,
            day,
            project_root / "data" / "rinex",
        )
        plan = build_product_plan(
            day,
            day,
            project_root / "data" / "products",
            station=station,
            include_erp=False,
            include_antex=True,
            include_multi_gnss=True,
            include_station_metadata=True,
            include_precise_products=False,
            include_mgex_extras=False,
        )
        download_items(plan)
        run_ppp(
            station,
            day,
            project_root,
            image=image,
            build_image=build_image and index == 0,
            systems=("gps", "galileo"),
            elevation_mask_deg=elevation_mask_deg,
            output_root=output_root,
        )


def read_bias_correction(path: Path) -> float:
    """Read a station-specific PPP-minus-TRO correction from a validation report."""
    report = json.loads(path.read_text(encoding="utf-8"))
    value = report.get("recommended_bias_correction_mm")
    if value is None:
        raise ValueError(f"No recommended_bias_correction_mm in {path}")
    return float(value)


def paired_differences_mm(
    daily: DailyPpp,
    reference: list[ZtdEstimate],
    convergence_hours: float,
) -> list[float]:
    cutoff = _utc_start(daily.day) + timedelta(hours=convergence_hours)
    ppp = sorted(
        (item for item in daily.estimates if item.epoch >= cutoff),
        key=lambda item: item.epoch,
    )
    epochs = [item.epoch for item in ppp]
    differences: list[float] = []
    for item in reference:
        if item.epoch < cutoff or not epochs:
            continue
        index = bisect.bisect_left(epochs, item.epoch)
        candidates = [candidate for candidate in (index - 1, index) if 0 <= candidate < len(ppp)]
        nearest = min(candidates, key=lambda candidate: abs(epochs[candidate] - item.epoch))
        if abs(epochs[nearest] - item.epoch) <= timedelta(seconds=30):
            differences.append(1000.0 * (ppp[nearest].ztd_m - item.ztd_m))
    return differences


def difference_summary(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0}
    bias = statistics.mean(values)
    return {
        "count": len(values),
        "bias_mm": round(bias, 3),
        "standard_deviation_mm": round(statistics.stdev(values), 3)
        if len(values) > 1
        else 0.0,
        "rmse_mm": round(math.sqrt(statistics.mean(value * value for value in values)), 3),
    }


def validate_gap_fill(
    ppp_days: dict[date, DailyPpp],
    igs_days: dict[date, list[ZtdEstimate]],
    convergence_hours: float,
) -> dict[str, object]:
    paired = {
        day: paired_differences_mm(daily, igs_days[day], convergence_hours)
        for day, daily in ppp_days.items()
        if day in igs_days
    }
    paired = {day: values for day, values in paired.items() if values}
    daily_biases = {day: statistics.median(values) for day, values in paired.items()}
    correction = statistics.median(daily_biases.values()) if daily_biases else None

    cross_validation: dict[str, object] = {}
    all_corrected: list[float] = []
    for holdout, values in sorted(paired.items()):
        training = [bias for day, bias in daily_biases.items() if day != holdout]
        if not training:
            continue
        training_bias = statistics.median(training)
        corrected = [value - training_bias for value in values]
        all_corrected.extend(corrected)
        cross_validation[holdout.isoformat()] = {
            "training_bias_mm": round(training_bias, 3),
            "raw": difference_summary(values),
            "bias_corrected": difference_summary(corrected),
        }

    return {
        "difference": "RTKLIB_PPP_minus_IGS_TRO",
        "convergence_hours_discarded": convergence_hours,
        "overlap_days": [day.isoformat() for day in sorted(paired)],
        "daily_median_bias_mm": {
            day.isoformat(): round(value, 3) for day, value in sorted(daily_biases.items())
        },
        "recommended_bias_correction_mm": round(correction, 3)
        if correction is not None
        else None,
        "leave_one_day_out": cross_validation,
        "leave_one_day_out_overall": difference_summary(all_corrected),
    }


def _resample_ppp_day(
    daily: DailyPpp,
    bias_mm: float,
    convergence_hours: float,
) -> list[ZtdEstimate]:
    cutoff = _utc_start(daily.day) + timedelta(hours=convergence_hours)
    ppp = sorted(
        (item for item in daily.estimates if item.epoch >= cutoff),
        key=lambda item: item.epoch,
    )
    epochs = [item.epoch for item in ppp]
    result: list[ZtdEstimate] = []
    target = cutoff
    end = _utc_start(daily.day + timedelta(days=1))
    while target < end and epochs:
        index = bisect.bisect_left(epochs, target)
        candidates = [candidate for candidate in (index - 1, index) if 0 <= candidate < len(ppp)]
        nearest = min(candidates, key=lambda candidate: abs(epochs[candidate] - target))
        if abs(epochs[nearest] - target) <= timedelta(seconds=30):
            item = ppp[nearest]
            result.append(
                ZtdEstimate(
                    epoch=target,
                    station=item.station,
                    ztd_m=item.ztd_m - bias_mm / 1000.0,
                    sigma_m=item.sigma_m,
                    source="rtklib_ppp_bias_corrected",
                )
            )
        target += timedelta(minutes=5)
    return result


def build_hybrid(
    start: date,
    end: date,
    igs_days: dict[date, list[ZtdEstimate]],
    ppp_days: dict[date, DailyPpp],
    bias_mm: float | None,
    convergence_hours: float,
) -> tuple[list[ZtdEstimate], list[date], list[date]]:
    estimates: list[ZtdEstimate] = []
    filled: list[date] = []
    still_missing: list[date] = []
    for day in date_range(start, end):
        if day in igs_days:
            estimates.extend(igs_days[day])
        elif day in ppp_days and bias_mm is not None:
            replacement = _resample_ppp_day(
                ppp_days[day], bias_mm, convergence_hours
            )
            if replacement:
                estimates.extend(replacement)
                filled.append(day)
            else:
                still_missing.append(day)
        else:
            still_missing.append(day)
    return sorted(estimates, key=lambda item: item.epoch), filled, still_missing


def plot_validation(
    path: Path,
    station: str,
    ppp_days: dict[date, DailyPpp],
    igs_days: dict[date, list[ZtdEstimate]],
    bias_mm: float | None,
    convergence_hours: float,
) -> None:
    figure, axis = plt.subplots(figsize=(14, 6), constrained_layout=True)
    first_igs = first_ppp = True
    for day, daily in sorted(ppp_days.items()):
        reference = igs_days.get(day)
        if not reference:
            continue
        axis.plot(
            [item.epoch for item in reference],
            [1000.0 * item.ztd_m for item in reference],
            color="tab:orange",
            linewidth=1.4,
            label="IGS TRO" if first_igs else None,
        )
        first_igs = False
        if bias_mm is not None:
            replacement = _resample_ppp_day(daily, bias_mm, convergence_hours)
            axis.plot(
                [item.epoch for item in replacement],
                [1000.0 * item.ztd_m for item in replacement],
                color="tab:blue",
                linewidth=1.0,
                label="PPP GPS+Galileo corretto" if first_ppp else None,
            )
            first_ppp = False
    axis.set_title(f"{station.upper()} — validazione del fallback PPP")
    axis.set_ylabel("ZTD [mm]")
    axis.set_xlabel("Tempo UTC")
    axis.grid(alpha=0.25)
    handles, labels = axis.get_legend_handles_labels()
    if handles:
        axis.legend(handles, labels, loc="best")
    else:
        axis.text(
            0.5,
            0.5,
            "Nessun giorno PPP/TRO sovrapposto nell'intervallo",
            transform=axis.transAxes,
            ha="center",
            va="center",
        )
    locator = mdates.AutoDateLocator()
    axis.xaxis.set_major_locator(locator)
    axis.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=170)
    plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--station", default="MATE00ITA")
    parser.add_argument("--start", type=_iso_date, required=True)
    parser.add_argument("--end", type=_iso_date, required=True)
    parser.add_argument("--tro-root", type=Path, default=Path("data/products/reference_ztd"))
    parser.add_argument("--ppp-root", type=Path, default=Path("data/ppp"))
    parser.add_argument("--output", type=Path, default=Path("data/derived/hybrid_ztd"))
    parser.add_argument("--convergence-hours", type=float, default=4.0)
    parser.add_argument(
        "--run-ppp-for-missing",
        action="store_true",
        help="download inputs and run GPS+Galileo PPP only on days without IGS TRO",
    )
    correction = parser.add_mutually_exclusive_group()
    correction.add_argument(
        "--bias-correction-mm",
        type=float,
        help="known station-specific RTKLIB-minus-TRO ZTD bias",
    )
    correction.add_argument(
        "--bias-report",
        type=Path,
        help="validation JSON containing recommended_bias_correction_mm",
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--elevation-mask", type=float, default=10.0)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--build-image", action="store_true")
    args = parser.parse_args()
    if args.end < args.start:
        parser.error("--end must not precede --start")
    if args.convergence_hours < 0:
        parser.error("--convergence-hours must not be negative")
    if not 0 <= args.elevation_mask < 90:
        parser.error("--elevation-mask must be in [0, 90) degrees")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    igs_days, missing_igs = read_igs_days(
        args.tro_root, args.station, args.start, args.end
    )
    if args.run_ppp_for_missing and missing_igs:
        run_ppp_for_missing_tro_days(
            missing_igs,
            args.station,
            args.project_root,
            args.ppp_root,
            elevation_mask_deg=args.elevation_mask,
            image=args.image,
            build_image=args.build_image,
        )
    ppp_days = read_available_ppp_days(
        args.ppp_root,
        args.station,
        args.start,
        args.end,
        required_elevation_mask_deg=args.elevation_mask,
    )
    report = validate_gap_fill(ppp_days, igs_days, args.convergence_hours)
    if args.bias_correction_mm is not None:
        bias = args.bias_correction_mm
        bias_source = "command_line"
    elif args.bias_report is not None:
        bias = read_bias_correction(args.bias_report)
        bias_source = str(args.bias_report)
    else:
        bias = report["recommended_bias_correction_mm"]
        bias_source = "current_interval_overlap" if bias is not None else None
    hybrid, filled, still_missing = build_hybrid(
        args.start,
        args.end,
        igs_days,
        ppp_days,
        float(bias) if bias is not None else None,
        args.convergence_hours,
    )
    args.output.mkdir(parents=True, exist_ok=True)
    suffix = f"{args.start.isoformat()}_{args.end.isoformat()}"
    csv_path = args.output / f"ztd_hybrid_{suffix}.csv"
    json_path = args.output / f"gapfill_validation_{suffix}.json"
    plot_path = args.output / f"ppp_vs_tro_samples_{suffix}.png"
    write_ztd_csv(csv_path, hybrid)
    report["missing_igs_days"] = [day.isoformat() for day in missing_igs]
    report["filled_with_ppp_days"] = [day.isoformat() for day in filled]
    report["still_missing_days"] = [day.isoformat() for day in still_missing]
    expected_daily_samples = max(
        0,
        math.ceil((24.0 - args.convergence_hours) * 12.0),
    )
    report["ppp_fallback_coverage"] = {
        day.isoformat(): {
            "expected_5_minute_slots_after_convergence": expected_daily_samples,
            "filled_5_minute_slots": sum(
                item.epoch.date() == day
                and item.source == "rtklib_ppp_bias_corrected"
                for item in hybrid
            ),
        }
        for day in missing_igs
    }
    report["applied_bias_correction_mm"] = bias
    report["bias_correction_source"] = bias_source
    report["ppp_fallback_requested"] = args.run_ppp_for_missing
    report["requested_ppp_fallback_configuration"] = {
        "systems": ["gps", "galileo"],
        "elevation_mask_deg": args.elevation_mask,
    } if args.run_ppp_for_missing else None
    report["ppp_day_configurations"] = {
        day.isoformat(): read_ppp_configuration(
            args.ppp_root / args.station.upper() / day.isoformat() / "rtklib.conf"
        )
        for day in sorted(ppp_days)
    }
    report["hybrid_samples"] = len(hybrid)
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    plot_validation(
        plot_path,
        args.station,
        ppp_days,
        igs_days,
        float(bias) if bias is not None else None,
        args.convergence_hours,
    )
    print(f"Hybrid ZTD: {csv_path}")
    print(f"Validation: {json_path}")
    print(f"Plot: {plot_path}")
    if missing_igs and not filled:
        print(
            "WARNING: no missing TRO day was filled; provide --bias-report or "
            "--bias-correction-mm if no PPP/TRO overlap is available"
        )


if __name__ == "__main__":
    main()
