"""Multi-year economics and fertility scoring for the existing APSIM policy model."""
from agro.apsim.fertility_constraints import evaluate as evaluate_fertility_constraints
from agro.apsim.fertility_constraints import evaluate_humic_period, humic_enabled

ANNUAL_STATES = {"spring_wheat", "winter_rye", "oats_with_underseed"}


def summarize_period(
    results: dict[int, dict], patch_metadata: dict, config: dict,
    policies: dict | None, baseline_n: dict | None,
    baseline_fertilizer: dict | None = None,
    humic_period: dict | None = None,
) -> dict:
    economics = config["economics"]
    form_prices = {values["apsim_type"]: float(values["price_per_kg_n"])
                   for values in config["fertilizer"]["forms"].values()}
    total_yield = total_profit = 0.0
    annual = []
    fields_output = []
    for year in config["evaluation_years"]:
        annual_yield = annual_profit = 0.0
        for field, values in results[year]["fields"].items():
            state = patch_metadata["fields"][field][str(year)]
            state_id = state["state_id"]
            area = float(values["area_ha"])
            yield_kg_ha = sum(float(values["yield_dm_kg_ha"][crop])
                              for crop in state["primary_yield_components"])
            price = float(economics["crop_price_per_kg_dm"].get(state_id, 0.0))
            revenue_ha = yield_kg_ha * price
            dose = fertiliser_cost_ha = application_cost_ha = 0.0
            fertiliser_name = "none"
            if state_id in ANNUAL_STATES:
                if policies is None:
                    fertilizer = baseline_fertilizer[field]
                    dose = fertilizer["dose_kg_n_ha"]
                    fertiliser_name = fertilizer["apsim_type"]
                    unit_price = form_prices[fertilizer["apsim_type"]]
                else:
                    fertilizer = policies[state_id]["fertilizer"]
                    dose = float(fertilizer["dose_kg_n_ha"])
                    fertiliser_name = fertilizer["name"]
                    unit_price = float(fertilizer["price_per_kg_n"])
                fertiliser_cost_ha = dose * unit_price
                application_cost_ha = float(economics["application_cost_per_ha"]) if dose > 0 else 0.0
            net_ha = (revenue_ha - fertiliser_cost_ha - application_cost_ha
                      - float(economics["other_cost_per_ha_year"]))
            production = yield_kg_ha * area
            profit = net_ha * area
            annual_yield += production
            annual_profit += profit
            fields_output.append({
                "year": year, "field": field, "state_id": state_id,
                "yield_dm_kg_ha": yield_kg_ha,
                "mineral_n_end_kg_ha": float(values["mineral_n_end_kg_ha"]),
                "fertilizer": fertiliser_name, "dose_kg_n_ha": dose,
                "revenue_per_ha": revenue_ha, "net_profit_per_ha": net_ha,
            })
        total_yield += annual_yield
        total_profit += annual_profit
        annual.append({"year": year, "yield_dm_kg": annual_yield,
                       "net_profit_proxy": annual_profit})
    if baseline_n is None:
        fertility = {
            "mode": "reference_run",
            "status": "not_applicable_to_reference",
            "is_feasible": None,
            "normalized_violation": 0.0,
            "aggregate_g": 0.0,
            "checks": [],
            "missing": [],
            "unassessed_indicators": list(
                (config.get("fertility_constraints") or {}).get("unassessed_indicators") or []
            ),
        }
    else:
        fertility = evaluate_fertility_constraints(
            fields_output, baseline_n, config.get("fertility_constraints")
        )
    soil_deviation = float(fertility["normalized_violation"])
    settings = config.get("fertility_constraints") or {}
    constraints = [float(fertility["aggregate_g"])]
    humic = evaluate_humic_period(
        humic_period, settings, patch_metadata["fields"], config["evaluation_years"])
    fertility["humic_carbon"] = humic
    if humic_enabled(settings):
        constraints.append(float(humic["aggregate_g"]))
        fertility["aggregate_g"] = sum(constraints)
        fertility["is_feasible"] = all(g <= 0 for g in constraints)
        fertility["hard_constraints_status"] = "feasible" if fertility["is_feasible"] else "infeasible"
    return {
        "yield_dm_kg": total_yield,
        "net_profit_proxy": total_profit,
        "soil_deviation": soil_deviation,
        "F": [-total_yield, -total_profit, soil_deviation],
        "G": constraints,
        "fertility_constraints": fertility,
        "annual": annual,
        "fields": fields_output,
    }
