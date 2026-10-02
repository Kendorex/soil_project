"""Helpers for a bounded surface-residue sensitivity diagnostic."""
from __future__ import annotations

from typing import Any

from .multifield_model import child, kind, parameters, walk


MANAGER_NAME = "Diagnostic remove 2022 surface residues"
REMOVAL_DATE = "2022-08-17"
CAP_MANAGER_NAME = "Diagnostic cap 2023 pre-sowing surface residue"
CAP_DATE = "2023-05-03"

MANAGER_CODE = '''using Models.Core;
using Models.Surface;
using System;

namespace Models
{
    [Serializable]
    public class Script : Model
    {
        [Link] private Clock Clock;
        [Link] private SurfaceOrganicMatter SurfaceOrganicMatter;

        public double Triggered { get; private set; }
        public double RemovedMassKgHa { get; private set; }
        public double RemovedNKgHa { get; private set; }

        [EventSubscribe("DoManagement")]
        private void OnDoManagement(object sender, EventArgs e)
        {
            if (Clock.Today.Date != new DateTime(2022, 8, 17))
                return;
            RemovedMassKgHa = SurfaceOrganicMatter.Wt;
            RemovedNKgHa = SurfaceOrganicMatter.N;
            SurfaceOrganicMatter.Remove(RemovedMassKgHa);
            Triggered = 1.0;
        }
    }
}'''

REPORT_EXPRESSIONS = (
    f"[{MANAGER_NAME}].Script.Triggered as ResidueRemovalTriggered",
    f"[{MANAGER_NAME}].Script.RemovedMassKgHa as ResidueRemovedMassKgHa",
    f"[{MANAGER_NAME}].Script.RemovedNKgHa as ResidueRemovedNKgHa",
)

CAP_REPORT_EXPRESSIONS = (
    f"[{CAP_MANAGER_NAME}].Script.Triggered as ResidueRemovalTriggered",
    f"[{CAP_MANAGER_NAME}].Script.RemovedMassKgHa as ResidueRemovedMassKgHa",
    f"[{CAP_MANAGER_NAME}].Script.RemovedNKgHa as ResidueRemovedNKgHa",
    f"[{CAP_MANAGER_NAME}].Script.TargetMassKgHa as ResidueTargetMassKgHa",
    f"[{CAP_MANAGER_NAME}].Script.BeforeMassKgHa as ResidueBeforeMassKgHa",
    f"[{CAP_MANAGER_NAME}].Script.AfterMassKgHa as ResidueAfterMassKgHa",
)

CEREAL_REPORT_EXPRESSIONS = (
    "[Sow using a variable rule].Script.DiagnosticResidueRemovalCount as CerealResidueRemovalCount",
    "[Sow using a variable rule].Script.DiagnosticResidueRemovedMassKgHa as CerealResidueRemovedMassKgHa",
    "[Sow using a variable rule].Script.DiagnosticResidueRemovedNKgHa as CerealResidueRemovedNKgHa",
)

CEREAL_HARVEST_OLD = '''            if (DateMatches(RotationHarvestDates[index]))
                HarvestGroup(index);'''

CEREAL_HARVEST_REMOVAL = '''            if (DateMatches(RotationHarvestDates[index]))
            {
                HarvestGroup(index);
                if (IsCerealToOatsTransition(index))
                {
                    DiagnosticResidueRemovedMassKgHa = SurfaceOrganicMatter.Wt;
                    DiagnosticResidueRemovedNKgHa = SurfaceOrganicMatter.N;
                    SurfaceOrganicMatter.Remove(DiagnosticResidueRemovedMassKgHa);
                    DiagnosticResidueRemovalCount += 1.0;
                }
            }'''

CEREAL_HARVEST_NEW_RESIDUE_EXPORT = '''            if (DateMatches(RotationHarvestDates[index]))
            {
                double residueBeforeHarvest = SurfaceOrganicMatter.Wt;
                HarvestGroup(index);
                if (IsCerealToOatsTransition(index))
                {
                    double residueAfterHarvest = SurfaceOrganicMatter.Wt;
                    double nitrogenBeforeRemoval = SurfaceOrganicMatter.N;
                    DiagnosticResidueRemovedMassKgHa = Math.Max(
                        0.0, residueAfterHarvest - residueBeforeHarvest);
                    if (DiagnosticResidueRemovedMassKgHa > 0.0)
                        SurfaceOrganicMatter.Remove(DiagnosticResidueRemovedMassKgHa);
                    DiagnosticResidueRemovedNKgHa = Math.Max(
                        0.0, nitrogenBeforeRemoval - SurfaceOrganicMatter.N);
                    DiagnosticResidueRemovalCount += 1.0;
                }
            }'''

CEREAL_HELPER_ANCHOR = '''        private void SowGroup(int index, string reason)'''
CEREAL_HELPER = '''        private bool IsCerealToOatsTransition(int index)
        {
            int next = index + 1;
            return next < RotationCrops.Length &&
                Parts(RotationCrops[index], '+').Any(crop =>
                    crop.Equals("Wheat", StringComparison.OrdinalIgnoreCase)) &&
                Parts(RotationCrops[next], '+').Any(crop =>
                    crop.Equals("Oats", StringComparison.OrdinalIgnoreCase));
        }

'''


def add_full_residue_removal(model: dict[str, Any], simulation_name: str,
                             fields: tuple[str, ...]) -> list[dict[str, Any]]:
    """Add a disposable manager to remove all surface OM on the known harvest date."""
    simulations = [node for _, node in walk(model)
                   if kind(node) == "Models.Core.Simulation"
                   and node.get("Name") == simulation_name
                   and node.get("Enabled", True)]
    if len(simulations) != 1:
        raise ValueError(f"Expected one simulation {simulation_name}")
    simulation = simulations[0]
    changes = []
    for field in fields:
        zone = child(simulation, field)
        if any(node.get("Name") == MANAGER_NAME for node in zone.get("Children", [])):
            raise ValueError(f"{field}: residue diagnostic manager already exists")
        manager = {
            "$type": "Models.Manager, Models",
            "CodeArray": MANAGER_CODE.splitlines(),
            "Parameters": [],
            "Name": MANAGER_NAME,
            "ResourceName": None,
            "Children": [],
            "Enabled": True,
            "ReadOnly": False,
        }
        zone.setdefault("Children", []).append(manager)
        report = child(zone, f"Report{field.removeprefix('Field')}")
        variables = report.setdefault("VariableNames", [])
        for expression in REPORT_EXPRESSIONS:
            if expression not in variables:
                variables.append(expression)
        changes.append({
            "field": field,
            "date": REMOVAL_DATE,
            "action": "remove all SurfaceOrganicMatter.Wt",
            "manager": MANAGER_NAME,
        })
    return changes


def add_pre_sowing_residue_cap(
    model: dict[str, Any], simulation_name: str, targets_kg_ha: dict[str, float],
    date: str = CAP_DATE,
) -> list[dict[str, Any]]:
    """Cap surface residue at supplied field targets on a diagnostic date.

    The target is not inferred by this function.  Callers must supply an
    observed or otherwise documented value in kg/ha.
    """
    year, month, day = (int(value) for value in date.split("-"))
    simulations = [node for _, node in walk(model)
                   if kind(node) == "Models.Core.Simulation"
                   and node.get("Name") == simulation_name
                   and node.get("Enabled", True)]
    if len(simulations) != 1:
        raise ValueError(f"Expected one simulation {simulation_name}")
    simulation = simulations[0]
    changes = []
    for field, raw_target in targets_kg_ha.items():
        target = float(raw_target)
        if target < 0:
            raise ValueError(f"{field}: residue target must be non-negative")
        zone = child(simulation, field)
        if any(node.get("Name") == CAP_MANAGER_NAME for node in zone.get("Children", [])):
            raise ValueError(f"{field}: residue cap manager already exists")
        target_literal = format(target, ".15g")
        code = f'''using Models.Core;
using Models.Surface;
using System;

namespace Models
{{
    [Serializable]
    public class Script : Model
    {{
        [Link] private Clock Clock;
        [Link] private SurfaceOrganicMatter SurfaceOrganicMatter;

        public double Triggered {{ get; private set; }}
        public double TargetMassKgHa {{ get; private set; }} = {target_literal};
        public double BeforeMassKgHa {{ get; private set; }}
        public double AfterMassKgHa {{ get; private set; }}
        public double RemovedMassKgHa {{ get; private set; }}
        public double RemovedNKgHa {{ get; private set; }}

        [EventSubscribe("DoManagement")]
        private void OnDoManagement(object sender, EventArgs e)
        {{
            if (Clock.Today.Date != new DateTime({year}, {month}, {day}))
                return;
            BeforeMassKgHa = SurfaceOrganicMatter.Wt;
            double beforeN = SurfaceOrganicMatter.N;
            RemovedMassKgHa = Math.Max(0.0, BeforeMassKgHa - TargetMassKgHa);
            if (RemovedMassKgHa > 0.0)
                SurfaceOrganicMatter.Remove(RemovedMassKgHa);
            AfterMassKgHa = SurfaceOrganicMatter.Wt;
            RemovedNKgHa = Math.Max(0.0, beforeN - SurfaceOrganicMatter.N);
            Triggered = 1.0;
        }}
    }}
}}'''
        manager = {
            "$type": "Models.Manager, Models",
            "CodeArray": code.splitlines(),
            "Parameters": [],
            "Name": CAP_MANAGER_NAME,
            "ResourceName": None,
            "Children": [],
            "Enabled": True,
            "ReadOnly": False,
        }
        zone.setdefault("Children", []).append(manager)
        report = child(zone, f"Report{field.removeprefix('Field')}")
        variables = report.setdefault("VariableNames", [])
        for expression in CAP_REPORT_EXPRESSIONS:
            if expression not in variables:
                variables.append(expression)
        changes.append({
            "field": field,
            "date": date,
            "action": "cap SurfaceOrganicMatter.Wt at supplied target",
            "target_mass_kg_ha": target,
            "manager": CAP_MANAGER_NAME,
        })
    return changes


def _parts(value: Any, separator: str = ",") -> list[str]:
    return [item.strip() for item in str(value or "").split(separator)]


def cereal_to_oats_targets(model: dict[str, Any], start_year: int) -> list[dict[str, Any]]:
    """List all modeled cereal harvests immediately preceding an oat year."""
    targets = []
    for path, node in walk(model):
        if node.get("Name") != "Sow using a variable rule":
            continue
        values = parameters(node)
        crops = _parts(values.get("RotationCrops"))
        harvests = _parts(values.get("RotationHarvestDates"))
        field = path.rsplit("/", 2)[-2]
        for index in range(len(crops) - 1):
            current = set(_parts(crops[index], "+"))
            following = set(_parts(crops[index + 1], "+"))
            if "Wheat" in current and "Oats" in following:
                day, month = harvests[index].split("-", 1)
                month_numbers = {
                    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5,
                    "Jun": 6, "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10,
                    "Nov": 11, "Dec": 12,
                }
                targets.append({
                    "field": field,
                    "oat_year": start_year + index + 1,
                    "cereal_harvest_year": start_year + index,
                    "cereal_harvest_date": (
                        f"{start_year + index:04d}-{month_numbers[month[:3]]:02d}-{int(day):02d}"
                    ),
                    "previous_rotation": crops[index],
                    "oat_rotation": crops[index + 1],
                })
    return targets


def patch_remove_all_residue_at_cereal_to_oats_harvest(
    model: dict[str, Any], oat_fields: tuple[str, ...]
) -> list[str]:
    """Patch disposable rotation managers for the cross-year extreme endpoint."""
    changed = []
    for path, node in walk(model):
        if node.get("Name") != "Sow using a variable rule":
            continue
        source = "\n".join(node.get("CodeArray") or [])
        replacements = (
            ("using Models.PMF;", "using Models.PMF;\nusing Models.Surface;"),
            ("        [Link] private Summary Summary;",
             "        [Link] private Summary Summary;\n"
             "        [Link] private SurfaceOrganicMatter SurfaceOrganicMatter;\n\n"
             "        public double DiagnosticResidueRemovalCount { get; private set; }\n"
             "        public double DiagnosticResidueRemovedMassKgHa { get; private set; }\n"
             "        public double DiagnosticResidueRemovedNKgHa { get; private set; }"),
            (CEREAL_HARVEST_OLD, CEREAL_HARVEST_REMOVAL),
            (CEREAL_HELPER_ANCHOR, CEREAL_HELPER + CEREAL_HELPER_ANCHOR),
        )
        for old, new in replacements:
            if source.count(old) != 1:
                raise ValueError(f"{path}: unrecognized rotation manager fragment")
            source = source.replace(old, new, 1)
        node["CodeArray"] = source.splitlines()
        changed.append(path)

    simulations = [node for _, node in walk(model)
                   if kind(node) == "Models.Core.Simulation" and node.get("Enabled", True)]
    if len(simulations) != 1:
        raise ValueError("Expected one enabled simulation")
    for field in oat_fields:
        zone = child(simulations[0], field)
        report = child(zone, f"Report{field.removeprefix('Field')}")
        variables = report.setdefault("VariableNames", [])
        for expression in CEREAL_REPORT_EXPRESSIONS:
            if expression not in variables:
                variables.append(expression)
    return changed


def patch_export_new_residue_at_cereal_to_oats_harvest(
    model: dict[str, Any], oat_fields: tuple[str, ...],
    target_years_by_field: dict[str, set[int]] | None = None,
) -> list[str]:
    """Export only the positive surface-residue increment created by harvest.

    This is a diagnostic endpoint for cereal straw export.  Surface organic
    matter already present before the harvest event is retained.
    """
    changed = []
    for path, node in walk(model):
        if node.get("Name") != "Sow using a variable rule":
            continue
        field = path.rsplit("/", 2)[-2]
        if target_years_by_field is not None and field not in target_years_by_field:
            continue
        source = "\n".join(node.get("CodeArray") or [])
        harvest_replacement = CEREAL_HARVEST_NEW_RESIDUE_EXPORT
        if target_years_by_field is not None:
            years = sorted(target_years_by_field[field])
            if not years:
                continue
            year_expression = " || ".join(
                f"Clock.Today.Year == {year}" for year in years
            )
            harvest_replacement = harvest_replacement.replace(
                "if (IsCerealToOatsTransition(index))",
                f"if (IsCerealToOatsTransition(index) && ({year_expression}))",
                1,
            )
        replacements = (
            ("using Models.PMF;", "using Models.PMF;\nusing Models.Surface;"),
            ("        [Link] private Summary Summary;",
             "        [Link] private Summary Summary;\n"
             "        [Link] private SurfaceOrganicMatter SurfaceOrganicMatter;\n\n"
             "        public double DiagnosticResidueRemovalCount { get; private set; }\n"
             "        public double DiagnosticResidueRemovedMassKgHa { get; private set; }\n"
             "        public double DiagnosticResidueRemovedNKgHa { get; private set; }"),
            (CEREAL_HARVEST_OLD, harvest_replacement),
            (CEREAL_HELPER_ANCHOR, CEREAL_HELPER + CEREAL_HELPER_ANCHOR),
        )
        for old, new in replacements:
            if source.count(old) != 1:
                raise ValueError(f"{path}: unrecognized rotation manager fragment")
            source = source.replace(old, new, 1)
        node["CodeArray"] = source.splitlines()
        changed.append(path)

    simulations = [node for _, node in walk(model)
                   if kind(node) == "Models.Core.Simulation" and node.get("Enabled", True)]
    if len(simulations) != 1:
        raise ValueError("Expected one enabled simulation")
    for field in oat_fields:
        zone = child(simulations[0], field)
        report = child(zone, f"Report{field.removeprefix('Field')}")
        variables = report.setdefault("VariableNames", [])
        for expression in CEREAL_REPORT_EXPRESSIONS:
            if expression not in variables:
                variables.append(expression)
    return changed


def defer_wheat_auto_harvest_for_field_years(
    model: dict[str, Any], field_years: dict[str, set[int]]
) -> list[dict[str, Any]]:
    """Let the rotation manager handle documented wheat harvest years.

    The generic readiness-based Wheat manager remains active in all other
    years.  This keeps the diagnostic limited to supplied field-year dates.
    """
    simulations = [node for _, node in walk(model)
                   if kind(node) == "Models.Core.Simulation" and node.get("Enabled", True)]
    if len(simulations) != 1:
        raise ValueError("Expected one enabled simulation")
    simulation = simulations[0]
    changes = []
    for field, years in field_years.items():
        if not years:
            continue
        zone = child(simulation, field)
        managers = [
            node for node in zone.get("Children", [])
            if kind(node) == "Models.Manager" and node.get("Name") == "Harvest"
            and parameters(node).get("Crop") == "[Wheat]"
        ]
        if len(managers) != 1:
            raise ValueError(f"{field}: expected one automatic Wheat Harvest manager")
        manager = managers[0]
        source = "\n".join(manager.get("CodeArray") or [])
        link_anchor = '''    public class Script : Model
    {
        [Description("Crop")]'''
        linked = '''    public class Script : Model
    {
        [Link] private Clock Clock;

        [Description("Crop")]'''
        if source.count(link_anchor) != 1:
            raise ValueError(f"{field}: unrecognized automatic harvest link fragment")
        source = source.replace(link_anchor, linked, 1)
        condition = "            if (Crop.IsReadyForHarvesting)"
        year_expression = " || ".join(
            f"Clock.Today.Year == {year}" for year in sorted(years)
        )
        replacement = (
            "            if (Crop.IsReadyForHarvesting && "
            f"!({year_expression}))"
        )
        if source.count(condition) != 1:
            raise ValueError(f"{field}: unrecognized automatic harvest condition")
        source = source.replace(condition, replacement, 1)
        manager["CodeArray"] = source.splitlines()
        changes.append({
            "field": field,
            "years_deferred_to_rotation_manager": sorted(years),
            "manager": "Harvest",
        })
    return changes
