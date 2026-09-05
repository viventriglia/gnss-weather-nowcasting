#!/usr/bin/env python3
"""Prepare compressed GNSS inputs and execute RTKLIB inside the container."""

from __future__ import annotations

import argparse
import gzip
import shutil
import subprocess
from pathlib import Path

import hatanaka


def _gunzip(source: Path, destination: Path) -> Path:
    with gzip.open(source, "rb") as input_stream:
        with destination.open("wb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream)
    return destination


def _prepare_observation(source: Path, work: Path) -> Path:
    compressed = work / source.name
    shutil.copy2(source, compressed)
    result = Path(hatanaka.decompress_on_disk(compressed))
    if not result.exists():
        raise RuntimeError(f"Hatanaka conversion did not create {result}")
    return result


def _rinex_antenna(path: Path) -> tuple[str, float, float, float]:
    """Return antenna type and E/N/U eccentricities from a RINEX header."""
    antenna_type = ""
    east = north = up = 0.0
    with path.open("r", encoding="ascii", errors="replace") as stream:
        for line in stream:
            label = line[60:].strip()
            if label == "ANT # / TYPE":
                antenna_type = line[20:40].strip()
            elif label == "ANTENNA: DELTA H/E/N":
                up, east, north = (float(value) for value in line[:42].split())
            elif label == "END OF HEADER":
                break
    if not antenna_type:
        raise ValueError(f"Missing ANT # / TYPE in {path}")
    return antenna_type, east, north, up


def _config(
    args: argparse.Namespace,
    stat_path: Path,
    work: Path,
    antenna: tuple[str, float, float, float],
) -> str:
    antenna_type, east, north, up = antenna
    return f"""\
pos1-posmode       =ppp-static
pos1-frequency     =l1+2
pos1-soltype       =forward
pos1-elmask        ={args.elmask:g}
pos1-dynamics      =off
pos1-tidecorr      =on
pos1-ionoopt       =dual-freq
pos1-tropopt       =est-ztdgrad
pos1-sateph        =precise
pos1-navsys        ={args.navsys}
pos2-armode        =off
pos2-rejionno      =30
pos2-rejgdop       =30
pos2-niter         =2
out-solformat      =xyz
out-outhead        =on
out-outopt         =on
out-timesys        =gpst
out-timeform       =hms
out-timendec       =3
out-fieldsep       =,
out-outstat        =state
stats-errratio     =100
stats-errphase     =0.003
stats-stdbias      =30
stats-stdtrop      =0.3
stats-prnbias      =0.0001
stats-prntrop      =0.0001
ant1-postype       =xyz
ant1-pos1          ={args.x:.4f}
ant1-pos2          ={args.y:.4f}
ant1-pos3          ={args.z:.4f}
ant1-anttype       ={antenna_type}
ant1-antdele       ={east:.4f}
ant1-antdeln       ={north:.4f}
ant1-antdelu       ={up:.4f}
file-satantfile    ={args.antex}
file-rcvantfile    ={args.antex}
file-eopfile       ={work / 'earth.erp'}
file-tempdir       ={work}
file-solstatfile   ={stat_path}
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--obs", type=Path, required=True)
    parser.add_argument("--nav", type=Path, required=True)
    parser.add_argument("--sp3", type=Path, required=True)
    parser.add_argument("--clk", type=Path, required=True)
    parser.add_argument("--erp", type=Path, required=True)
    parser.add_argument("--antex", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--x", type=float, required=True)
    parser.add_argument("--y", type=float, required=True)
    parser.add_argument("--z", type=float, required=True)
    parser.add_argument("--navsys", type=int, required=True)
    parser.add_argument("--elmask", type=float, required=True)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    work = args.output / "work"
    work.mkdir(parents=True, exist_ok=True)
    obs = _prepare_observation(args.obs, work)
    nav = _gunzip(args.nav, work / "broadcast.rnx")
    sp3 = _gunzip(args.sp3, work / "precise.sp3")
    clk = _gunzip(args.clk, work / "precise.clk")
    _gunzip(args.erp, work / "earth.erp")

    solution = args.output / "solution.pos"
    # b34's rnx2rtkp derives this name from the `.pos` output path.
    stat = solution.with_suffix(solution.suffix + ".stat")
    config = args.output / "rtklib.conf"
    config.write_text(_config(args, stat, work, _rinex_antenna(obs)), encoding="ascii")
    command = [
        "rnx2rtkp",
        "-k",
        str(config),
        "-o",
        str(solution),
        str(obs),
        str(nav),
        str(sp3),
        str(clk),
    ]
    log_path = args.output / "rtklib.log"
    with log_path.open("w", encoding="utf-8") as log:
        subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT)
    if not stat.exists():
        raise RuntimeError("RTKLIB did not produce the configured solution.stat")


if __name__ == "__main__":
    main()
