"""Run one day of GPS/Galileo PPP with containerized RTKLIB and export its ZTD."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import date, datetime
from pathlib import Path

from .dates import gps_week_start
from .rtklib_stat import read_rtklib_ztd, write_ztd_csv

DEFAULT_IMAGE = "gnss-weather-rtklib:2.4.3-b34"
SYSTEM_BITS = {"gps": 1, "galileo": 8}


def _iso_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _one(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if len(matches) != 1:
        raise FileNotFoundError(
            f"Expected one file matching {directory / pattern}, found {len(matches)}"
        )
    return matches[0]


def _container_path(path: Path, root: Path) -> str:
    return "/project/" + path.resolve().relative_to(root).as_posix()


def _systems(value: str) -> tuple[str, ...]:
    systems = tuple(dict.fromkeys(item.strip().lower() for item in value.split(",") if item.strip()))
    unknown = sorted(set(systems).difference(SYSTEM_BITS))
    if not systems or unknown:
        raise argparse.ArgumentTypeError(
            f"systems must be a comma-separated subset of {sorted(SYSTEM_BITS)}; unknown={unknown}"
        )
    return systems


def run_ppp(
    station: str,
    day: date,
    root: Path,
    image: str = DEFAULT_IMAGE,
    build_image: bool = False,
    systems: tuple[str, ...] = ("gps", "galileo"),
    elevation_mask_deg: float = 10.0,
    output_root: Path = Path("data/ppp"),
) -> Path:
    """Run RTKLIB for one UTC day and return the exported ZTD CSV path."""
    root = root.resolve()
    station = station.upper()
    year_doy = f"{day.year}{day.timetuple().tm_yday:03d}"
    if not 0.0 <= elevation_mask_deg < 90.0:
        raise ValueError("elevation mask must be in [0, 90) degrees")
    navsys = sum(SYSTEM_BITS[name] for name in systems)

    obs = _one(root / "data/rinex/obs" / station, f"{station}_R_{year_doy}0000_01D_*_MO.crx.gz")
    nav = _one(root / "data/rinex/nav", f"BRDC00IGS_R_{year_doy}0000_01D_MN.rnx.gz")
    if "galileo" in systems:
        sp3 = _one(
            root / "data/products/mgex/orbit",
            f"COD0MGXFIN_{year_doy}0000_01D_05M_ORB.SP3.gz",
        )
        clk = _one(
            root / "data/products/mgex/clock",
            f"COD0MGXFIN_{year_doy}0000_01D_30S_CLK.CLK.gz",
        )
        erp = _one(
            root / "data/products/mgex/erp",
            f"COD0MGXFIN_{year_doy}0000_01D_12H_ERP.ERP.gz",
        )
    else:
        week_start = gps_week_start(day)
        week_year_doy = f"{week_start.year}{week_start.timetuple().tm_yday:03d}"
        sp3 = _one(root / "data/products/orbit", f"IGS0OPSFIN_{year_doy}0000_01D_*_ORB.SP3.gz")
        clk = _one(root / "data/products/clock", f"IGS0OPSFIN_{year_doy}0000_01D_*_CLK.CLK.gz")
        erp = _one(root / "data/products/erp", f"IGS0OPSFIN_{week_year_doy}0000_07D_01D_ERP.ERP.gz")
    antex = root / "data/products/antex/igs20.atx"
    metadata_path = root / "data/products/station" / f"{station}.json"
    for required in (antex, metadata_path):
        if not required.exists():
            raise FileNotFoundError(required)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if output_root.is_absolute():
        raise ValueError("output_root must be relative to the project root")
    output = root / output_root / station / day.isoformat()

    if build_image:
        subprocess.run(
            [
                "docker",
                "build",
                "-t",
                image,
                str(root / "docker/rtklib"),
            ],
            check=True,
        )

    inputs = {
        "obs": obs,
        "nav": nav,
        "sp3": sp3,
        "clk": clk,
        "erp": erp,
        "antex": antex,
        "output": output,
    }
    container_args: list[str] = []
    for name, path in inputs.items():
        container_args.extend([f"--{name}", _container_path(path, root)])
    container_args.extend(
        [
            "--x",
            str(metadata["xCoord"]),
            "--y",
            str(metadata["yCoord"]),
            "--z",
            str(metadata["zCoord"]),
            "--navsys",
            str(navsys),
            "--elmask",
            str(elevation_mask_deg),
        ]
    )
    command = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{root}:/project",
        "--entrypoint",
        "python",
        image,
        "/project/docker/rtklib/container_runner.py",
        *container_args,
    ]
    subprocess.run(command, check=True)

    estimates = read_rtklib_ztd(output / "solution.pos.stat", station)
    result = output / "ztd.csv"
    write_ztd_csv(result, estimates)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--station", default="MATE00ITA")
    parser.add_argument("--day", type=_iso_date, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--build", action="store_true", help="build the pinned RTKLIB image first")
    parser.add_argument(
        "--systems",
        type=_systems,
        default=("gps", "galileo"),
        help="comma-separated constellations (default: gps,galileo)",
    )
    parser.add_argument("--elevation-mask", type=float, default=10.0)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("data/ppp"),
        help="project-relative output directory",
    )
    args = parser.parse_args()
    output = run_ppp(
        args.station,
        args.day,
        args.root,
        args.image,
        args.build,
        args.systems,
        args.elevation_mask,
        args.output_root,
    )
    print(f"Wrote RTKLIB PPP ZTD to {output}")


if __name__ == "__main__":
    main()
