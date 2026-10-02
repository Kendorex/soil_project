from __future__ import annotations

import math
from typing import Any


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def calculate_mineral_n_end(report_row: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Return mineral N without silently treating Urea as NO3 + NH4."""
    configured_source = str(config.get("mineral_n_source", "MineralN"))
    ready = _number(report_row.get(configured_source))
    if ready is not None:
        return {"value": ready, "status": "available", "source": configured_source}

    no3 = _number(report_row.get("NO3kgHa"))
    nh4 = _number(report_row.get("NH4kgHa"))
    if no3 is not None and nh4 is not None:
        return {"value": no3 + nh4, "status": "available", "source": "NO3kgHa+NH4kgHa"}

    with_urea = _number(report_row.get("MineralNWithUrea"))
    if with_urea is not None and bool(config.get("allow_urea_in_mineral_n", False)):
        return {"value": with_urea, "status": "available", "source": "MineralNWithUrea"}

    if any(value is not None for value in (no3, nh4, with_urea)):
        return {"value": math.nan, "status": "partial", "source": "insufficient NO3+NH4 data"}
    return {"value": math.nan, "status": "missing", "source": "no mineral N columns"}
