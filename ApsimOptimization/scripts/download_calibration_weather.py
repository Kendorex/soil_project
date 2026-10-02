#!/usr/bin/env python3
"""Download NASA POWER daily weather and write APSIM .met files.

The default coordinates are taken from simulation/standart.apsimx and refer to
the VNIIMZ field used by this project. The generated data are intended for
cultivar calibration, not as a replacement for the four optimisation weather
scenarios.
"""

from __future__ import annotations

import argparse
import csv
import io
import math
import statistics
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path


POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
PARAMETERS = (
    "T2M_MAX",
    "T2M_MIN",
    "PRECTOTCORR",
    "ALLSKY_SFC_SW_DWN",
    "WS2M",
    "PS",
)


def download_power_csv(latitude: float, longitude: float, start_year: int, end_year: int) -> str:
    query = urllib.parse.urlencode(
        {
            "parameters": ",".join(PARAMETERS),
            "community": "AG",
            "longitude": longitude,
            "latitude": latitude,
            "start": f"{start_year}0101",
            "end": f"{end_year}1231",
            "format": "CSV",
        }
    )
    request = urllib.request.Request(
        f"{POWER_URL}?{query}",
        headers={"User-Agent": "ApsimOptimization-CultivarCalibration/1.0"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read().decode("utf-8-sig")


def parse_power_csv(text: str) -> list[dict[str, float | int]]:
    lines = text.splitlines()
    try:
        header_index = next(index for index, line in enumerate(lines) if line.startswith("YEAR,DOY,"))
    except StopIteration as exc:
        raise ValueError("NASA POWER response does not contain the expected YEAR,DOY header") from exc

    rows: list[dict[str, float | int]] = []
    for source in csv.DictReader(io.StringIO("\n".join(lines[header_index:]))):
        row: dict[str, float | int] = {
            "year": int(source["YEAR"]),
            "day": int(source["DOY"]),
        }
        for name in PARAMETERS:
            value = float(source[name])
            if not math.isfinite(value) or value <= -999:
                raise ValueError(f"Missing NASA POWER value: year={row['year']} day={row['day']} parameter={name}")
            row[name] = value
        rows.append(row)
    if not rows:
        raise ValueError("NASA POWER response contains no daily rows")
    return rows


def calculate_tav_amp(rows: list[dict[str, float | int]]) -> tuple[float, float]:
    daily_means = [
        (float(row["T2M_MAX"]) + float(row["T2M_MIN"])) / 2.0
        for row in rows
    ]
    tav = statistics.fmean(daily_means)

    monthly: dict[tuple[int, int], list[float]] = defaultdict(list)
    from datetime import date, timedelta

    for row, value in zip(rows, daily_means):
        day = date(int(row["year"]), 1, 1) + timedelta(days=int(row["day"]) - 1)
        monthly[(day.year, day.month)].append(value)
    monthly_means = {key: statistics.fmean(values) for key, values in monthly.items()}
    annual_amplitudes = []
    for year in sorted({year for year, _month in monthly_means}):
        values = [monthly_means[(year, month)] for month in range(1, 13)]
        annual_amplitudes.append(max(values) - min(values))
    amp = statistics.fmean(annual_amplitudes)
    return tav, amp


def expected_days(year: int) -> int:
    return 366 if year % 400 == 0 or (year % 4 == 0 and year % 100 != 0) else 365


def validate_year(rows: list[dict[str, float | int]], year: int) -> None:
    days = [int(row["day"]) for row in rows]
    expected = list(range(1, expected_days(year) + 1))
    if days != expected:
        missing = sorted(set(expected) - set(days))
        duplicates = sorted(day for day in set(days) if days.count(day) > 1)
        raise ValueError(f"Incomplete year {year}: missing={missing}, duplicates={duplicates}")


def write_met(
    path: Path,
    rows: list[dict[str, float | int]],
    latitude: float,
    longitude: float,
    tav: float,
    amp: float,
) -> None:
    year = int(rows[0]["year"])
    validate_year(rows, year)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("[weather.met.weather]\n")
        stream.write("! source = NASA POWER daily point data, Agroclimatology community\n")
        stream.write(f"! source_url = {POWER_URL}\n")
        stream.write(f"! coordinates = {latitude:.5f}, {longitude:.5f}\n")
        stream.write("! purpose = preliminary APSIM cultivar calibration; validate against local station data when available\n")
        stream.write(f"station = VNIIMZ NASA POWER {year}\n")
        stream.write(f"latitude = {latitude:.5f}\n")
        stream.write(f"longitude = {longitude:.5f}\n")
        stream.write(f"tav = {tav:.3f}\n")
        stream.write(f"amp = {amp:.3f}\n\n")
        stream.write("year day radn maxt mint rain wind_speed sp\n")
        stream.write("() () (MJ/m2/d) (oC) (oC) (mm) (m/s) (Pa)\n")
        for row in rows:
            stream.write(
                f"{int(row['year'])} {int(row['day'])} "
                f"{float(row['ALLSKY_SFC_SW_DWN']):.3f} "
                f"{float(row['T2M_MAX']):.3f} "
                f"{float(row['T2M_MIN']):.3f} "
                f"{float(row['PRECTOTCORR']):.3f} "
                f"{float(row['WS2M']):.3f} "
                f"{float(row['PS']) * 1000.0:.1f}\n"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--latitude", type=float, default=56.77719)
    parser.add_argument("--longitude", type=float, default=36.11066)
    parser.add_argument("--start-year", type=int, default=2020)
    parser.add_argument("--end-year", type=int, default=2023)
    parser.add_argument("--output-dir", type=Path, default=Path("data/calibration_weather"))
    args = parser.parse_args()
    if args.start_year > args.end_year:
        parser.error("--start-year must not be later than --end-year")

    raw = download_power_csv(args.latitude, args.longitude, args.start_year, args.end_year)
    rows = parse_power_csv(raw)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = args.output_dir / f"nasa_power_{args.start_year}_{args.end_year}_raw.csv"
    raw_path.write_text(raw, encoding="utf-8", newline="\n")

    tav, amp = calculate_tav_amp(rows)
    for year in range(args.start_year, args.end_year + 1):
        year_rows = [row for row in rows if int(row["year"]) == year]
        output = args.output_dir / f"weather_zlata_{year}.met"
        write_met(output, year_rows, args.latitude, args.longitude, tav, amp)
        print(f"created {output}: {len(year_rows)} daily rows")
    print(f"created {raw_path}: {len(rows)} daily rows")
    print(f"common APSIM constants: tav={tav:.3f}, amp={amp:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
