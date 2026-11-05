"""Crop-policy helpers for the 2020-2023 selected-rotation NSGA-II run."""
from __future__ import annotations

import json
from pathlib import Path

from .multifield_model import child, kind, patch_sowing_decision, parameters, read_json, walk, write_json


from agro.evaluation.multiyear import ANNUAL_STATES, summarize_period


def fertilizer_schemes(config: dict) -> list[dict]:
    """One zero-N control plus every configured form at every positive N dose."""
    doses = [float(value) for value in config["fertilizer"]["doses_kg_n_ha"]]
    forms = config["fertilizer"]["forms"]
    control_type = next(iter(forms.values()))["apsim_type"]
    schemes = [{"name": "N0", "apsim_type": control_type, "dose_kg_n_ha": 0.0,
                "price_per_kg_n": 0.0}]
    for dose in doses:
        if dose <= 0:
            continue
        for name, values in forms.items():
            schemes.append({
                "name": f"{name}_{dose:g}",
                "apsim_type": values["apsim_type"],
                "dose_kg_n_ha": dose,
                "price_per_kg_n": float(values["price_per_kg_n"]),
            })
    return schemes


def variable_spec(config: dict) -> list[tuple[str, str, list]]:
    result = []
    keys = {
        "cultivar_options": "cultivar",
        "population_options_m2": "population",
        "depth_options_mm": "depth",
        "row_spacing_options_mm": "row_spacing",
    }
    for policy_name, policy in config["management_policies"].items():
        for key, decision_key in keys.items():
            values = policy.get(key)
            if values and len(values) > 1:
                result.append((policy_name, decision_key, list(values)))
        result.append((policy_name, "fertilizer_scheme", fertilizer_schemes(config)))
    return result


def decode_vector(config: dict, vector) -> dict:
    policies = {}
    for policy_name, policy in config["management_policies"].items():
        policies[policy_name] = {
            "cultivar": policy.get("cultivar_options", [None])[0],
            "population": float(policy["population_options_m2"][0]),
            "depth": float(policy["depth_options_mm"][0]),
            "row_spacing": float(policy["row_spacing_options_mm"][0]),
            "fertilizer": fertilizer_schemes(config)[0],
        }
    for value, (policy_name, key, options) in zip(vector, variable_spec(config)):
        selected = options[int(round(float(value)))]
        if key == "fertilizer_scheme":
            policies[policy_name]["fertilizer"] = selected
        elif key == "cultivar":
            policies[policy_name]["cultivar"] = selected
        else:
            policies[policy_name][key] = float(selected)
    return policies


def dynamic_fertilizer_code(policies: dict) -> list[str]:
    wheat = policies["spring_wheat"]["fertilizer"]
    oats = policies["oats_with_underseed"]["fertilizer"]
    rye = policies["winter_rye"]["fertilizer"]
    return [
        "using System;",
        "using Models.Core;",
        "using Models.PMF;",
        "using Models.Soils;",
        "namespace Models",
        "{",
        "    [Serializable]",
        "    public class Script : Model",
        "    {",
        "        [Link] private Clock Clock;",
        "        [Link] private Fertiliser Fertiliser;",
        "        [Link(ByName = true)] private Plant Wheat;",
        "        [Link(ByName = true)] private Plant Oats;",
        "        private DateTime lastApplication = DateTime.MinValue;",
        "        [EventSubscribe(\"Sowing\")]",
        "        private void OnSowing(object sender, EventArgs e)",
        "        {",
        "            if (Clock.Today.Date == Clock.StartDate.Date || Clock.Today.Date == lastApplication.Date)",
        "                return;",
        "            double amount;",
        "            string type;",
        f"            if (Clock.Today.Month >= 8) {{ amount = {rye['dose_kg_n_ha']:.17g}; type = {json.dumps(rye['apsim_type'])}; }}",
        f"            else if (Oats.IsAlive) {{ amount = {oats['dose_kg_n_ha']:.17g}; type = {json.dumps(oats['apsim_type'])}; }}",
        f"            else if (Wheat.IsAlive) {{ amount = {wheat['dose_kg_n_ha']:.17g}; type = {json.dumps(wheat['apsim_type'])}; }}",
        "            else return;",
        "            if (amount > 0)",
        "                Fertiliser.Apply(amount: amount, type: type);",
        "            lastApplication = Clock.Today.Date;",
        "        }",
        "    }",
        "}",
    ]


def patch_candidate_model(
    base_model: str | Path, patch_metadata: dict, config: dict,
    policies: dict, destination: str | Path,
) -> list[dict]:
    data = read_json(base_model)
    simulations = [node for _, node in walk(data)
                   if kind(node) == "Models.Core.Simulation" and node.get("Enabled", True)]
    if len(simulations) != 1:
        raise ValueError("Expected one enabled APSIM simulation")
    sim = simulations[0]
    start_year = int(str(child(sim, "Clock")["Start"])[:4])
    zones = {node["Name"]: node for node in sim.get("Children", [])
             if kind(node) == "Models.Core.Zone" and node.get("Enabled", True)}
    changes = []
    for field, zone in zones.items():
        manager = child(zone, "Sow using a variable rule")
        for year_text, state in patch_metadata["fields"][field].items():
            year = int(year_text)
            if year not in range(2019, 2024):
                continue
            state_id = state["state_id"]
            if state_id not in policies:
                continue
            policy = policies[state_id]
            settings = config["management_policies"][state_id]
            decision = {
                "population_m2": policy["population"],
                "depth_mm": policy["depth"],
                "row_spacing_mm": policy["row_spacing"],
            }
            if state_id == "spring_wheat" and policy.get("cultivar") != "historical":
                decision["cultivar"] = policy["cultivar"]
            changes.append({
                "field": field, "year": year, "state_id": state_id,
                **patch_sowing_decision(manager, year, start_year, settings, decision),
            })
        fertiliser = child(zone, "Fertilise at sowing")
        fertiliser["CodeArray"] = dynamic_fertilizer_code(policies)
        fertiliser["Parameters"] = []
    write_json(destination, data)
    return changes


def baseline_fertilizer_by_field(model: str | Path) -> dict:
    data = read_json(model)
    values = {}
    for _, node in walk(data):
        if kind(node) != "Models.Core.Zone" or not node.get("Enabled", True):
            continue
        manager = child(node, "Fertilise at sowing")
        params = parameters(manager)
        values[node["Name"]] = {
            "apsim_type": params["FertiliserType"],
            "dose_kg_n_ha": float(params["Amount"]),
        }
    return values


