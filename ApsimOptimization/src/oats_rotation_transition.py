"""Targeted diagnostic for oat undersowing after an established perennial stand."""
from __future__ import annotations

from typing import Any

from .multifield_model import parameters, walk


SCHEDULED_SOWING_OLD = '''            if (!IsWinterCrop(index) && !IsPurePerennial(index) && DateMatches(RotationSowingDates[index]))
            {
                EndPlantsNotIn(index);
                SowGroup(index, "scheduled sowing");
            }'''

SCHEDULED_SOWING_RESTART = '''            if (!IsWinterCrop(index) && !IsPurePerennial(index) && DateMatches(RotationSowingDates[index]))
            {
                // A dated oat sowing after a pure perennial phase represents a new
                // establishment.  End the old stand before sowing oats and their
                // new companion plants; otherwise SowGroup silently retains the
                // previous RedClover and PlantainForage cohorts.
                if (index > 0 && IsPurePerennial(index - 1) &&
                    Parts(RotationCrops[index], '+').Any(crop =>
                        crop.Equals("Oats", StringComparison.OrdinalIgnoreCase)))
                    EndAllPlants();
                else
                    EndPlantsNotIn(index);
                SowGroup(index, "scheduled sowing");
            }'''

HARVEST_OLD = '''            if (DateMatches(RotationHarvestDates[index]))
                HarvestGroup(index);'''

HARVEST_END_OLD_PERENNIAL = '''            if (DateMatches(RotationHarvestDates[index]))
            {
                HarvestGroup(index);
                int following = index + 1;
                // The next dated oat entry is a new establishment.  End the old
                // perennial stand after its existing final cut so it cannot be
                // mistaken for the new undersown companion cohort next spring.
                if (following < RotationCrops.Length && IsPurePerennial(index) &&
                    !IsPurePerennial(following) && !IsWinterCrop(following) &&
                    !string.IsNullOrWhiteSpace(RotationSowingDates[following]) &&
                    RotationSowingDates[following] != "-" &&
                    Parts(RotationCrops[following], '+').Any(crop =>
                        crop.Equals("Oats", StringComparison.OrdinalIgnoreCase)))
                    EndAllPlants();
            }'''


def _parts(value: Any, separator: str = ",") -> list[str]:
    return [item.strip() for item in str(value or "").split(separator)]


def find_oat_after_perennial_targets(model: dict[str, Any], start_year: int) -> list[dict[str, Any]]:
    """List rotation entries where old companion cohorts can survive into new oats."""
    targets: list[dict[str, Any]] = []
    for path, node in walk(model):
        if node.get("Name") != "Sow using a variable rule":
            continue
        values = parameters(node)
        crops = _parts(values.get("RotationCrops"))
        sowing_dates = _parts(values.get("RotationSowingDates"))
        field = path.rsplit("/", 2)[-2]
        for index in range(1, len(crops)):
            previous = set(_parts(crops[index - 1], "+"))
            current = set(_parts(crops[index], "+"))
            if previous and previous <= {"RedClover", "PlantainForage"} and "Oats" in current:
                targets.append({
                    "field": field,
                    "year": start_year + index,
                    "previous_rotation": crops[index - 1],
                    "current_rotation": crops[index],
                    "sowing_date": sowing_dates[index],
                    "reason": "dated oat sowing follows a pure perennial phase",
                })
    return targets


def patch_oat_after_perennial_restart(model: dict[str, Any]) -> list[str]:
    """Patch managers in a disposable candidate model and return changed paths."""
    changed: list[str] = []
    for path, node in walk(model):
        if node.get("Name") != "Sow using a variable rule":
            continue
        source = "\n".join(node.get("CodeArray") or [])
        if source.count(SCHEDULED_SOWING_OLD) != 1:
            raise ValueError(f"{path}: unrecognized scheduled-sowing manager")
        node["CodeArray"] = source.replace(
            SCHEDULED_SOWING_OLD, SCHEDULED_SOWING_RESTART, 1
        ).splitlines()
        changed.append(path)
    return changed


def patch_end_old_perennials_after_final_cut(model: dict[str, Any]) -> list[str]:
    """End the old stand at its existing final cut before next year's new oats."""
    changed: list[str] = []
    for path, node in walk(model):
        if node.get("Name") != "Sow using a variable rule":
            continue
        source = "\n".join(node.get("CodeArray") or [])
        if source.count(HARVEST_OLD) != 1:
            raise ValueError(f"{path}: unrecognized rotation-harvest manager")
        node["CodeArray"] = source.replace(
            HARVEST_OLD, HARVEST_END_OLD_PERENNIAL, 1
        ).splitlines()
        changed.append(path)
    return changed
