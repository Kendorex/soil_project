#!/usr/bin/env python3
"""Import the colleague-approved Open-Meteo CSV into APSIM .met files."""

from __future__ import annotations

import argparse
import csv
import math
import shutil
import statistics
from collections import defaultdict
from datetime import date
from pathlib import Path


EXPECTED_COLUMNS = {
    "time": "time",
    "maxt": "temperature_2m_max",
    "mint": "temperature_2m_min",
    "rain": "precipitation_sum",
    "pressure": "surface_pressure_mean",
    "radn": "shortwave_radiation_sum",
    "wind": "wind_speed_10m_mean",
}


def find_column(columns: list[str], prefix: str) -> str:
    matches = [column for column in columns if column.casefold().startswith(prefix.casefold())]
    if len(matches) != 1:
        raise ValueError(f"Expected one column starting with {prefix!r}; found {matches}")
    return matches[0]


def read_open_meteo(path: Path) -> tuple[dict[str, str], list[dict[str, float | date]]]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    if len(lines) < 5:
        raise ValueError("Open-Meteo CSV is too short")
    metadata = dict(zip(next(csv.reader([lines[0]])), next(csv.reader([lines[1]]))))
    reader = csv.DictReader(lines[3:])
    columns = list(reader.fieldnames or [])
    mapping = {
        name: find_column(columns, prefix)
        for name, prefix in EXPECTED_COLUMNS.items()
    }
    rows: list[dict[str, float | date]] = []
    seen: set[date] = set()
    for source in reader:
        day = date.fromisoformat(source[mapping["time"]])
        if day in seen:
            raise ValueError(f"Duplicate Open-Meteo date: {day}")
        seen.add(day)
        row: dict[str, float | date] = {"date": day}
        for target in ("maxt", "mint", "rain", "pressure", "radn", "wind"):
            text = source[mapping[target]].strip()
            if not text:
                raise ValueError(f"Missing {target} on {day}")
            value = float(text)
            if not math.isfinite(value):
                raise ValueError(f"Non-finite {target} on {day}: {value}")
            row[target] = value
        rows.append(row)
    rows.sort(key=lambda item: item["date"])
    for previous, current in zip(rows, rows[1:]):
        if (current["date"] - previous["date"]).days != 1:
            raise ValueError(f"Weather gap between {previous['date']} and {current['date']}")
    return metadata, rows


def tav_amp(rows: list[dict[str, float | date]]) -> tuple[float, float]:
    full_year_rows = [row for row in rows if 2004 <= row["date"].year <= 2023]
    means = [(float(row["maxt"]) + float(row["mint"])) / 2 for row in full_year_rows]
    tav = statistics.fmean(means)
    monthly: dict[tuple[int, int], list[float]] = defaultdict(list)
    for row, mean in zip(full_year_rows, means):
        monthly[(row["date"].year, row["date"].month)].append(mean)
    amplitudes = []
    for year in range(2004, 2024):
        values = [statistics.fmean(monthly[(year, month)]) for month in range(1, 13)]
        amplitudes.append(max(values) - min(values))
    return tav, statistics.fmean(amplitudes)


def expected_days(year: int) -> int:
    return 366 if year % 400 == 0 or (year % 4 == 0 and year % 100 != 0) else 365


def write_met(
    path: Path,
    rows: list[dict[str, float | date]],
    latitude: float,
    longitude: float,
    elevation: float,
    tav: float,
    amp: float,
) -> None:
    year = rows[0]["date"].year
    if len(rows) != expected_days(year) or rows[0]["date"] != date(year, 1, 1) or rows[-1]["date"] != date(year, 12, 31):
        raise ValueError(f"Year {year} is incomplete: {len(rows)} rows")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("[weather.met.weather]\n")
        stream.write("! source = colleague-approved Open-Meteo historical daily CSV\n")
        stream.write(f"! coordinates = {latitude:.6f}, {longitude:.6f}; elevation = {elevation:.1f} m\n")
        stream.write("! timezone = Europe/Moscow (UTC+3)\n")
        stream.write("! wind source height = 10 m\n")
        stream.write(f"station = VNIIMZ Open-Meteo {year}\n")
        stream.write(f"latitude = {latitude:.6f}\n")
        stream.write(f"longitude = {longitude:.6f}\n")
        stream.write(f"tav = {tav:.3f}\n")
        stream.write(f"amp = {amp:.3f}\n\n")
        stream.write("year day radn maxt mint rain wind_speed sp\n")
        stream.write("() () (MJ/m2/d) (oC) (oC) (mm) (m/s) (Pa)\n")
        for row in rows:
            day = row["date"]
            stream.write(
                f"{day.year} {day.timetuple().tm_yday} "
                f"{float(row['radn']):.3f} {float(row['maxt']):.3f} {float(row['mint']):.3f} "
                f"{float(row['rain']):.3f} {float(row['wind']):.3f} {float(row['pressure']) * 100.0:.1f}\n"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("data/calibration_weather"))
    parser.add_argument("--start-year", type=int, default=2020)
    parser.add_argument("--end-year", type=int, default=2023)
    parser.add_argument("--file-prefix", default="weather_zlata", help="output filename prefix before _YEAR.met")
    args = parser.parse_args()
    metadata, rows = read_open_meteo(args.source)
    latitude = float(metadata["latitude"])
    longitude = float(metadata["longitude"])
    elevation = float(metadata["elevation"])
    tav, amp = tav_amp(rows)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    archived_source = args.output_dir / "open_meteo_vniimz_2004_2024_source.csv"
    if args.source.resolve() != archived_source.resolve():
        shutil.copy2(args.source, archived_source)
    for year in range(args.start_year, args.end_year + 1):
        selected = [row for row in rows if row["date"].year == year]
        path = args.output_dir / f"{args.file_prefix}_{year}.met"
        if path.is_file() and "NASA POWER" in path.read_text(encoding="utf-8", errors="ignore"):
            nasa_archive = args.output_dir / f"weather_zlata_{year}_nasa_power.met"
            if not nasa_archive.exists():
                shutil.copy2(path, nasa_archive)
        write_met(path, selected, latitude, longitude, elevation, tav, amp)
        print(f"created {path}: {len(selected)} rows")
    print(f"archived source: {archived_source}")
    print(f"coordinates={latitude},{longitude}; elevation={elevation}; tav={tav:.3f}; amp={amp:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
