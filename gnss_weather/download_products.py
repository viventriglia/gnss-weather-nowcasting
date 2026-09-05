"""Plan and download auxiliary products needed by a tropospheric PPP solution."""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

import requests

from .dates import date_range, gps_week, gps_week_start

LOGGER = logging.getLogger(__name__)
IGS_PRODUCTS_URL = "https://igs.bkg.bund.de/root_ftp/IGS/products"
IGS_ANTEX_URL = "https://files.igs.org/pub/station/general/igs20.atx"
EUREF_RINEX_URL = "https://epncb.oma.be/pub/RINEX"
VMF3_URL = "https://vmf.geo.tuwien.ac.at/trop_products/GRID/1x1/VMF3/VMF3_OP"
VMF3_OROGRAPHY_URL = "https://vmf.geo.tuwien.ac.at/station_coord_files/orography_ell_1x1"
IGS_TRO_URL = "https://maia.usno.navy.mil/gps/products/troposphere/zpd"
BKG_STATION_API = "https://igs.bkg.bund.de/api/collections/stations/items"
CODE_MGEX_URL = "https://www.aiub.unibe.ch/download/CODE_MGEX/CODE"


@dataclass(frozen=True)
class DownloadItem:
    """One remote product and its local destination."""

    kind: str
    url: str
    destination: Path
    required: bool = True
    headers: dict[str, str] | None = None


def _daily_filename(day: date, product: str, sampling: str, extension: str) -> str:
    stamp = f"{day.year}{day.timetuple().tm_yday:03d}0000"
    return f"IGS0OPSFIN_{stamp}_01D_{sampling}_{product}.{extension}.gz"


def build_product_plan(
    start: date,
    end: date,
    output_dir: Path,
    *,
    station: str | None = None,
    include_erp: bool = True,
    include_antex: bool = True,
    include_meteo: bool = False,
    include_multi_gnss: bool = False,
    include_vmf3: bool = False,
    include_reference_ztd: bool = False,
    include_station_metadata: bool = False,
    include_precise_products: bool = True,
    include_mgex_extras: bool = True,
) -> list[DownloadItem]:
    """Build URLs for final IGS products without performing network access.

    SP3 orbits and 30-second CLK files are planned daily. ERP is one weekly
    product, de-duplicated when the interval spans days in the same GPS week.
    RINEX meteorological filenames use the standard EUREF long-name convention.
    """
    days = date_range(start, end)
    items: list[DownloadItem] = []

    for day in days:
        week = gps_week(day)
        base = f"{IGS_PRODUCTS_URL}/{week}"
        orbit = _daily_filename(day, "ORB", "15M", "SP3")
        clock = _daily_filename(day, "CLK", "30S", "CLK")
        if include_precise_products:
            items.extend(
                [
                    DownloadItem("orbit", f"{base}/{orbit}", output_dir / "orbit" / orbit),
                    DownloadItem("clock", f"{base}/{clock}", output_dir / "clock" / clock),
                ]
            )

        if include_multi_gnss:
            stamp = f"{day.year}{day.timetuple().tm_yday:03d}0000"
            code_products = [
                ("mgex_orbit", f"COD0MGXFIN_{stamp}_01D_05M_ORB.SP3.gz", "orbit"),
                ("mgex_clock", f"COD0MGXFIN_{stamp}_01D_30S_CLK.CLK.gz", "clock"),
                ("mgex_erp", f"COD0MGXFIN_{stamp}_01D_12H_ERP.ERP.gz", "erp"),
            ]
            if include_mgex_extras:
                code_products.extend(
                    [
                        ("mgex_bias", f"COD0MGXFIN_{stamp}_01D_01D_OSB.BIA.gz", "bias"),
                        (
                            "mgex_attitude",
                            f"COD0MGXFIN_{stamp}_01D_30S_ATT.OBX.gz",
                            "attitude",
                        ),
                    ]
                )
            for kind, filename, directory in code_products:
                items.append(
                    DownloadItem(
                        kind,
                        f"{CODE_MGEX_URL}/{day.year}/{filename}",
                        output_dir / "mgex" / directory / filename,
                    )
                )

        if include_vmf3:
            for hour in (0, 6, 12, 18):
                filename = f"VMF3_{day:%Y%m%d}.H{hour:02d}"
                items.append(
                    DownloadItem(
                        "vmf3",
                        f"{VMF3_URL}/{day.year}/{filename}",
                        output_dir / "vmf3" / filename,
                    )
                )

        if include_reference_ztd:
            doy = day.timetuple().tm_yday
            filename = (
                f"IGS0OPSFIN_{day.year}{doy:03d}0000_01D_05M_"
                f"{station.upper()}_TRO.TRO.gz"
            )
            items.append(
                DownloadItem(
                    "reference_ztd",
                    f"{IGS_TRO_URL}/{day.year}/{doy:03d}/{filename}",
                    output_dir / "reference_ztd" / filename,
                    required=False,
                )
            )

        if include_meteo:
            if not station:
                raise ValueError("station is required when include_meteo=True")
            doy = day.timetuple().tm_yday
            meteo = f"{station.upper()}_R_{day.year}{doy:03d}0000_01D_05M_MM.rnx.gz"
            meteo_url = f"{EUREF_RINEX_URL}/{day.year}/{doy:03d}/{meteo}"
            items.append(
                DownloadItem(
                    "meteo",
                    meteo_url,
                    output_dir / "meteo" / meteo,
                    required=False,
                )
            )

    if include_erp:
        week_starts = sorted({gps_week_start(day) for day in days})
        for sunday in week_starts:
            week = gps_week(sunday)
            stamp = f"{sunday.year}{sunday.timetuple().tm_yday:03d}0000"
            erp = f"IGS0OPSFIN_{stamp}_07D_01D_ERP.ERP.gz"
            items.append(
                DownloadItem(
                    "earth_rotation",
                    f"{IGS_PRODUCTS_URL}/{week}/{erp}",
                    output_dir / "erp" / erp,
                )
            )

    if include_antex:
        items.append(DownloadItem("antenna", IGS_ANTEX_URL, output_dir / "antex" / "igs20.atx"))

    if include_station_metadata:
        if not station:
            raise ValueError("station is required when include_station_metadata=True")
        items.append(
            DownloadItem(
                "station_metadata",
                f"{BKG_STATION_API}/{station.upper()}?uploadInfo=true&f=json",
                output_dir / "station" / f"{station.upper()}.json",
                headers={"Accept": "application/json"},
            )
        )

    if include_vmf3:
        items.append(
            DownloadItem(
                "vmf3_orography",
                VMF3_OROGRAPHY_URL,
                output_dir / "vmf3" / "orography_ell_1x1",
            )
        )

    return items


def download_items(items: Iterable[DownloadItem], timeout: int = 60) -> list[Path]:
    """Download products atomically, skipping destinations already present."""
    downloaded: list[Path] = []
    failures: list[str] = []
    headers = {"User-Agent": "gnss-weather/0.1"}

    with requests.Session() as session:
        session.headers.update(headers)
        for item in items:
            destination = item.destination
            if destination.exists():
                LOGGER.info("Already present: %s", destination)
                continue

            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f"{destination.name}.part")
            try:
                with session.get(
                    item.url,
                    stream=True,
                    timeout=timeout,
                    headers=item.headers,
                ) as response:
                    response.raise_for_status()
                    with temporary.open("wb") as stream:
                        for chunk in response.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                stream.write(chunk)
                temporary.replace(destination)
                downloaded.append(destination)
                LOGGER.info("Downloaded %s: %s", item.kind, destination)
            except (requests.RequestException, OSError) as exc:
                temporary.unlink(missing_ok=True)
                message = f"{item.kind}: {item.url} ({exc})"
                if item.required:
                    failures.append(message)
                else:
                    LOGGER.warning("Optional product unavailable: %s", message)

    if failures:
        raise RuntimeError("Some downloads failed:\n" + "\n".join(failures))
    return downloaded


def _iso_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=_iso_date, required=True)
    parser.add_argument("--end", type=_iso_date, required=True)
    parser.add_argument("--station", default="MATE00ITA")
    parser.add_argument("--output", type=Path, default=Path("data/products"))
    parser.add_argument("--meteo", action="store_true", help="also fetch RINEX meteorological data")
    parser.add_argument(
        "--complete",
        action="store_true",
        help="include CODE MGEX, VMF3, IGS reference ZTD and station metadata",
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--tro-only",
        action="store_true",
        help="fetch only optional IGS TRO files and station metadata",
    )
    modes.add_argument(
        "--mgex-core",
        action="store_true",
        help="fetch CODE MGEX orbit/clock/ERP, ANTEX and station metadata",
    )
    parser.add_argument("--no-erp", action="store_true")
    parser.add_argument("--no-antex", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="print URLs only")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    plan = build_product_plan(
        args.start,
        args.end,
        args.output,
        station=args.station,
        include_erp=not args.no_erp and not (args.tro_only or args.mgex_core),
        include_antex=not args.no_antex and not args.tro_only,
        include_meteo=args.meteo,
        include_multi_gnss=args.complete or args.mgex_core,
        include_vmf3=args.complete,
        include_reference_ztd=args.complete or args.tro_only,
        include_station_metadata=args.complete or args.tro_only or args.mgex_core,
        include_precise_products=not (args.tro_only or args.mgex_core),
        include_mgex_extras=args.complete,
    )
    if args.dry_run:
        for item in plan:
            print(f"{item.kind:15} {item.url}")
        return
    download_items(plan)


if __name__ == "__main__":
    main()
