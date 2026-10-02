#!/usr/bin/env python3
"""Validate and compactly package one real multi-field optimizer run."""

from __future__ import annotations

import argparse
import csv
import json
import math
import tarfile
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--validation-dir", type=Path, required=True)
    parser.add_argument("--minimum-evaluated", type=int, default=8)
    parser.add_argument("--expected-population", type=int, default=8)
    parser.add_argument("--expected-generations", type=int, default=2)
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    validation_dir = args.validation_dir.resolve()
    if not run_dir.is_relative_to(ROOT) or not validation_dir.is_relative_to(ROOT):
        raise SystemExit("Run and validation directories must be inside the project")

    required = [
        "config_snapshot.json",
        "model_inventory.json",
        "pareto.json",
        "evaluations.json",
        "history.json",
        "evaluations_summary.csv",
        "pareto_summary.csv",
    ]
    checks: list[dict] = []

    def check(name: str, ok: bool, detail=None) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    missing = [name for name in required if not (run_dir / name).is_file()]
    check("required result files", not missing, missing)
    if missing:
        payload = {"status": "failed", "run_dir": str(run_dir), "checks": checks}
        (validation_dir / "optimizer_check.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return 1

    config = read_json(run_dir / "config_snapshot.json")
    pareto = read_json(run_dir / "pareto.json")
    evaluations = read_json(run_dir / "evaluations.json")
    history = read_json(run_dir / "history.json")

    check("optimizer completed", pareto.get("status") == "ok", pareto.get("status"))
    check(
        "requested search size",
        config.get("nsga2", {}).get("population") == args.expected_population
        and config.get("nsga2", {}).get("generations") == args.expected_generations,
        config.get("nsga2"),
    )
    check(
        "diagnostic scope is explicit",
        config.get("scenario", {}).get("kind") == "diagnostic"
        and config.get("scenario", {}).get("recommendation_status") == "prohibited",
        config.get("scenario"),
    )
    check(
        "enough unique candidates evaluated",
        len(evaluations) >= args.minimum_evaluated,
        len(evaluations),
    )
    check(
        "all APSIM candidates successful",
        pareto.get("failed") == 0
        and evaluations
        and all(row.get("status") == "ok" for row in evaluations),
        {"evaluated": len(evaluations), "failed": pareto.get("failed")},
    )
    finite_objectives = all(
        len(row.get("F", [])) == 3
        and all(math.isfinite(float(value)) for value in row["F"])
        and all(math.isfinite(float(value)) for value in row.get("G", []))
        for row in evaluations
    )
    check("three finite objectives and finite constraints", finite_objectives)

    front = pareto.get("pareto") or []
    check("nonempty Pareto front", bool(front), len(front))
    reference_id = pareto.get("reference_candidate_id")
    first = history[0] if history else {}
    check(
        "reference decision evaluated first",
        bool(reference_id)
        and first.get("source") == "reference_decision"
        and first.get("candidate_id") == reference_id,
        first,
    )
    check(
        "reference decision present in evaluations",
        any(row.get("candidate_id") == reference_id for row in evaluations),
        reference_id,
    )

    decisions = {
        json.dumps(row.get("decisions"), sort_keys=True, ensure_ascii=False)
        for row in evaluations
    }
    objectives = {
        tuple(round(float(value), 8) for value in row.get("F", []))
        for row in evaluations
    }
    check("decision diversity", len(decisions) >= 2, len(decisions))
    check("objective diversity", len(objectives) >= 2, len(objectives))
    sowing_fields = {
        field for field, settings in (config.get("fields") or {}).items()
        if settings.get("sowing")
    }
    check(
        "multi-crop sowing search configured",
        sowing_fields == {"Field2", "Field3", "Field5", "Field7", "Field8", "Field9"},
        sorted(sowing_fields),
    )
    sampled_sowing = [
        field_decision
        for row in evaluations
        for field, field_decision in (row.get("decisions") or {}).items()
        if field in sowing_fields and "sowing_date" in field_decision
    ]
    check(
        "sowing variables reached evaluated candidates",
        bool(sampled_sowing)
        and any("population_m2" in decision for decision in sampled_sowing)
        and any("depth_mm" in decision for decision in sampled_sowing),
        len(sampled_sowing),
    )
    cultivar_fields = {
        field for field, settings in (config.get("fields") or {}).items()
        if (settings.get("sowing") or {}).get("cultivar_options")
    }
    check(
        "cultivar choice limited to spring-wheat fields",
        cultivar_fields == {"Field3", "Field8"},
        sorted(cultivar_fields),
    )
    expected_n_options = {
        "UreaN_old_price_proxy",
        "NH4NO3N_old_price_proxy",
        "UAN_N_old_price_proxy",
        "NO3N_old_price_proxy",
    }
    expected_n_doses = {0.0, 30.0, 60.0, 90.0, 120.0, 150.0, 180.0, 210.0}
    annual_fields = {"Field2", "Field3", "Field5", "Field7", "Field8", "Field9"}
    configured_n_search = all(
        set(config["fields"][field].get("fertilizer_options") or []) == expected_n_options
        and set(config["fields"][field].get("dose_kg_product_ha", {}).get("values") or [])
        == expected_n_doses
        and 1 + len(expected_n_options) * (len(expected_n_doses) - 1) == 29
        for field in annual_fields
    )
    check(
        "29 fertilizer schemes: control plus four N forms at seven doses",
        configured_n_search,
    )
    sampled_n = [
        decision
        for row in evaluations
        for field, decision in (row.get("decisions") or {}).items()
        if field in annual_fields
    ]
    check(
        "evaluated fertilizer decisions respect the discrete N search",
        bool(sampled_n)
        and all(decision.get("option") in expected_n_options for decision in sampled_n)
        and all(float(decision.get("dose_kg_product_ha")) in expected_n_doses
                for decision in sampled_n),
        len(sampled_n),
    )

    with (run_dir / "evaluations_summary.csv").open(
        encoding="utf-8-sig", newline=""
    ) as stream:
        evaluation_csv = list(csv.DictReader(stream))
    with (run_dir / "pareto_summary.csv").open(
        encoding="utf-8-sig", newline=""
    ) as stream:
        pareto_csv = list(csv.DictReader(stream))
    check(
        "CSV and JSON candidate sets agree",
        {row["candidate_id"] for row in evaluation_csv}
        == {row["candidate_id"] for row in evaluations}
        and {row["candidate_id"] for row in pareto_csv}
        == {row["candidate_id"] for row in front},
        {"evaluations_csv": len(evaluation_csv), "pareto_csv": len(pareto_csv)},
    )

    status = "passed" if all(row["ok"] for row in checks) else "failed"
    payload = {
        "status": status,
        "run_dir": run_dir.relative_to(ROOT).as_posix(),
        "evaluated": len(evaluations),
        "pareto_solutions": len(front),
        "checks": checks,
    }
    result_path = validation_dir / "optimizer_check.json"
    result_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    archive = ROOT / f"optimizer_20min_check_{datetime.now():%Y%m%d_%H%M%S}.tar.gz"
    selected = [run_dir / name for name in required]
    selected.extend(
        path
        for path in run_dir.rglob("*")
        if path.is_file()
        and path.name
        in {"result.json", "apsim.log", "preparation.json", "candidate_provenance.json"}
    )
    selected.extend(path for path in validation_dir.iterdir() if path.is_file())
    with tarfile.open(archive, "w:gz") as bundle:
        for path in sorted(set(selected)):
            bundle.add(path, arcname=path.relative_to(ROOT).as_posix(), recursive=False)

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"RESULT ARCHIVE: {archive}")
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
