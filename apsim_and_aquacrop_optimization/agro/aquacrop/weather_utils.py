"""Shared daily weather formatting and FAO-56 Hargreaves reference ET."""

import math


def extraterrestrial_radiation(when, latitude):
    """FAO-56 equations 21, 23-25: Ra in MJ/m2/day, latitude in degrees."""
    day = when.timetuple().tm_yday
    phi = math.radians(latitude)
    distance = 1 + 0.033 * math.cos(2 * math.pi * day / 365)
    declination = 0.409 * math.sin(2 * math.pi * day / 365 - 1.39)
    angle = math.acos(max(-1, min(1, -math.tan(phi) * math.tan(declination))))
    return (24 * 60 / math.pi) * 0.0820 * distance * (
        angle * math.sin(phi) * math.sin(declination)
        + math.cos(phi) * math.cos(declination) * math.sin(angle)
    )


def hargreaves_eto(row, latitude):
    """FAO-56 equation 52; 0.408 converts Ra from MJ/m2/day to mm/day.

    This temperature-only estimate is not APSIM's internal potential evaporation.
    Measured radiation is retained in the audit CSV, not substituted for Ra.
    """
    mean = (row["tmax"] + row["tmin"]) / 2
    return max(0.0, 0.0023 * (mean + 17.8) * math.sqrt(row["tmax"] - row["tmin"])
               * 0.408 * extraterrestrial_radiation(row["date"], latitude))


def weather_text(first, title, records):
    return "\n".join([
        title,
        "     1 : Daily records",
        f"{first.day:6d} : First day of record",
        f"{first.month:6d} : First month of record",
        f"{first.year:6d} : First year of record",
        "", "Daily observations", "==================", *records, "",
    ])


