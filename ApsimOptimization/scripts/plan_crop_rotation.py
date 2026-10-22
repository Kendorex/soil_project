#!/usr/bin/env python3
"""Build a source-compatible multi-year crop map without running APSIM."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
import tarfile
from collections import Counter
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.rotation_planner import (
    RotationRules,
    plan_fields,
    plan_fields_stateful,
    plan_fields_stateful_open_start,
    select_farm_plan,
)
from src.rotation_visualization import plot_rotation_map, write_rotation_text_report


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "configs" / "rotation_planning_2025_2029.yaml"


def resolve_path(config_path: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (config_path.parent / path).resolve()


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def create_archive(path: Path, output: Path, config_path: Path, rules_path: Path) -> None:
    files = [config_path, rules_path]
    files.extend(file for file in output.iterdir() if file.is_file())
    with tarfile.open(path, "w:gz") as archive:
        for file in sorted(set(item.resolve() for item in files)):
            if file.is_relative_to(ROOT):
                name = file.relative_to(ROOT).as_posix()
            else:
                name = file.name
            archive.add(file, arcname=name, recursive=False)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args(argv)
    config_path = args.config.resolve()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8-sig"))
    rules_path = resolve_path(config_path, config["rotation_rules"])
    rules = RotationRules.from_file(rules_path)

    start_year = int(config["start_year"])
    years = int(config["years"])
    state_model = config.get("state_model")
    if state_model:
        report_top_k = int(config.get("top_k", 5))
        candidate_pool = int(config.get("candidate_pool", 20000))
        if config.get("first_year_unconstrained", False):
            result = plan_fields_stateful_open_start(
                rules,
                config["fields"],
                state_model,
                years,
                start_states=config.get("first_year_states"),
                top_k=candidate_pool,
                minimum_score=float(config.get("minimum_transition_score", 0.5)),
            )
            status = "historical_open_start_transition_compatible_map_not_apsim_evaluated"
        else:
            result = plan_fields_stateful(
                rules,
                config["initial_states"],
                state_model,
                years,
                top_k=candidate_pool,
                minimum_score=float(config.get("minimum_transition_score", 0.5)),
            )
            status = "stateful_transition_compatible_candidate_map_not_apsim_evaluated"
        if config.get("farm_crop_mix"):
            farm_selection = select_farm_plan(
                result["fields"], config["farm_crop_mix"], years,
                beam_width=int(config.get("farm_selection_beam_width", 5000)),
            )
            farm_selection["annual_counts"] = [
                {"year": start_year + year_index, **counts}
                for year_index, counts in enumerate(farm_selection["annual_counts"])
            ]
            result["farm_selection"] = farm_selection
            for field, field_result in result["fields"].items():
                selected_rank = farm_selection["selected_ranks"][field]
                selected = next(
                    item for item in field_result["alternatives"]
                    if item["rank"] == selected_rank
                )
                reported = [selected]
                reported.extend(
                    item for item in field_result["alternatives"]
                    if item["rank"] != selected_rank
                )
                field_result["selected_alternative"] = selected
                field_result["alternatives"] = reported[:report_top_k]
    else:
        result = plan_fields(
            rules,
            config["initial_crops"],
            config["candidate_crops"],
            years,
            top_k=int(config.get("top_k", 5)),
            minimum_score=float(config.get("minimum_transition_score", 0.5)),
        )
        status = "transition_compatible_candidate_map_not_apsim_evaluated"
    result.update({
        "schema_version": 1,
        "status": status,
        "start_year": start_year,
        "end_year": start_year + years - 1,
        "source": rules.payload["source"],
        "config": str(config_path),
        "limitations": config.get("limitations", [
            "The matrix scores only predecessor-to-successor compatibility.",
            "Future crop demand, prices, and required production volumes are not supplied.",
            "Tied sequences are not ranked by yield, profit, or soil simulation.",
            "Future weather and an APSIM model extending beyond 2023 are not available.",
            "Mustard and rape are excluded because the supplied matrix does not score them.",
            "Perennial-grass continuation states are derived from the documented farm rotation, not scored as new sowings by the matrix.",
        ]),
        **result,
    })

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output = ROOT / "outputs" / "rotation_planning" / f"plan_{stamp}"
    output.mkdir(parents=True, exist_ok=False)
    map_rows = []
    alternative_rows = []
    selected_transition_rows = []
    year_columns = [start_year + index for index in range(years)]
    for field, field_result in sorted(
        result["fields"].items(), key=lambda item: int(item[0].replace("Field", ""))
    ):
        best = field_result.get("selected_alternative", field_result["alternatives"][0])
        row = {
            "field": field,
            "previous_crop": best["initial_crop_name"],
            **{
                str(year): crop
                for year, crop in zip(year_columns, best["sequence_names"])
            },
            "total_transition_score": best["total_score"],
            "mean_transition_score": best["mean_score"],
            "matrix_transition_count": best.get("matrix_transition_count", years),
            "continuation_years": best.get("continuation_years", 0),
            "retained_best_ties": field_result["retained_best_ties"],
            "farm_selected_original_rank": best["rank"],
        }
        map_rows.append(row)
        previous_state = best.get("initial_crop_id")
        previous_name = best["initial_crop_name"]
        for year, state_id, state_name, code in zip(
            year_columns,
            best["sequence_ids"],
            best["sequence_names"],
            best["transition_codes"],
        ):
            selected_transition_rows.append({
                "field": field,
                "year": year,
                "previous_state": previous_state or "unknown",
                "previous_crop": previous_name,
                "selected_state": state_id,
                "selected_crop": state_name,
                "transition_code": code,
                "transition_basis": {
                    "Ц": "recommended_by_matrix",
                    "Д": "permitted_by_matrix",
                    "Нд": "prohibited_by_matrix",
                    "П": "continuing_existing_grass_stand",
                    "И": "observed_in_farm_history_not_matrix_scored",
                    "Ф": "fixed_from_farm_history_not_matrix_scored",
                    "С": "open_start_not_scored",
                }.get(code, "unknown"),
            })
            previous_state = state_id
            previous_name = state_name
        for alternative in field_result["alternatives"]:
            alternative_rows.append({
                "field": field,
                "rank": alternative["rank"],
                "farm_selected": alternative["rank"] == best["rank"],
                "previous_crop": alternative["initial_crop_name"],
                "sequence": " -> ".join(alternative["sequence_names"]),
                "transition_codes": ",".join(alternative["transition_codes"]),
                "total_transition_score": alternative["total_score"],
                "mean_transition_score": alternative["mean_score"],
                "matrix_transition_count": alternative.get("matrix_transition_count", years),
                "continuation_years": alternative.get("continuation_years", 0),
            })

    write_csv(output / "crop_map.csv", map_rows)
    write_csv(output / "alternatives.csv", alternative_rows)
    write_csv(output / "selected_transitions.csv", selected_transition_rows)
    code_counts = Counter(row["transition_code"] for row in selected_transition_rows)
    matrix_transition_count = sum(code_counts[code] for code in ("Ц", "Д", "Нд"))
    requested_categories = list(
        result.get("farm_selection", {}).get("categories", {})
    )
    state_to_category = {
        state_id: category
        for category, spec in result.get("farm_selection", {}).get(
            "categories", {}
        ).items()
        for state_id in spec.get("states", [])
    }
    present_categories = sorted({
        state_to_category.get(row["selected_state"], row["selected_state"])
        for row in selected_transition_rows
    })
    quality = {
        "field_count": len(result["fields"]),
        "years": years,
        "selected_field_year_slots": len(selected_transition_rows),
        "transition_code_counts": dict(sorted(code_counts.items())),
        "prohibited_transition_count": code_counts["Нд"],
        "all_selected_transitions_feasible": code_counts["Нд"] == 0,
        "matrix_transition_count": matrix_transition_count,
        "matrix_recommended_fraction": (
            code_counts["Ц"] / matrix_transition_count
            if matrix_transition_count else None
        ),
        "source_history_transition_count": code_counts["И"] + code_counts["Ф"],
        "requested_categories": requested_categories,
        "present_categories": present_categories,
        "missing_requested_categories": sorted(
            set(requested_categories) - set(present_categories)
        ),
        "all_requested_categories_present": (
            set(requested_categories) <= set(present_categories)
        ),
        "farm_mix_within_configured_bounds": result.get(
            "farm_selection", {}
        ).get("within_historical_ranges"),
        "farm_mix_violation_units": result.get(
            "farm_selection", {}
        ).get("range_violation_units"),
        "warning": (
            "Compatibility only: yield, profit and soil state were not evaluated."
        ),
    }
    (output / "plan_quality.json").write_text(
        json.dumps(quality, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if result.get("farm_selection"):
        write_csv(
            output / "farm_crop_counts.csv",
            result["farm_selection"]["annual_counts"],
        )
    (output / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_rotation_text_report(output / "crop_rotation_report.txt", result)
    plot_rotation_map(output / "crop_rotation_map.png", result)
    archive = ROOT / f"rotation_plan_{start_year}_{start_year + years - 1}_{stamp}.tar.gz"
    create_archive(archive, output, config_path, rules_path)
    print(f"ROTATION MAP: {output / 'crop_map.csv'}")
    print(f"VISUAL MAP: {output / 'crop_rotation_map.png'}")
    print(f"TEXT REPORT: {output / 'crop_rotation_report.txt'}")
    print(f"ALTERNATIVES: {output / 'alternatives.csv'}")
    print(f"QUALITY CHECK: {output / 'plan_quality.json'}")
    print(f"SEND THIS ARCHIVE: {archive}")
    print("APSIM was not run; the map is constrained only by the supplied transition matrix.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
