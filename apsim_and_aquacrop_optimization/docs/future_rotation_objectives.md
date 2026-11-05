> Update 2026-11-03: new APSIM and coupled future searches use `humic_constraint: "objective_only"`. Humic is always included as an objective (automatically appended if omitted), not a feasibility constraint. Absolute per-field losses remain reported; feasible does not imply carbon conservation. NSGA-II night jobs now use yield/profit/humic; NSGA-III uses yield/profit/mineral_n/humic. Standalone AquaCrop is unchanged. Earlier strict-conservation descriptions below document the prior setup. Existing runs and frozen night batches retain their original rules. Start a NEW batch with `python -m agro.night_queue`, rather than resuming the completed old batch, to use the new policy.

# Future crop maps with selectable APSIM objectives and NSGA-II/III

The original `python scripts/plan_crop_rotation.py` still builds a matrix-compatible map without
APSIM. The new scenario engine in `agro/planning/future_scenarios.py` can optimize
the actual field-by-year crop sequences through the newly calibrated model.
The same selection/search workflow also supports native AquaCrop harvests with
APSIM soil indicators on the identical new crop map. Use
`python run.py coupled`; see
[AquaCrop future-map instructions](future_rotation_aquacrop.md) for initialization,
metric sources and model boundaries.

## Choose one or several criteria

Use the general launcher from the project root. These examples start the search:

```powershell
python run.py apsim --objectives yield --run
python run.py apsim --objectives yield profit --run
python run.py apsim --objectives yield profit mineral_n --run
python run.py apsim --objectives yield profit mineral_n humic --run
```

Omit `--run` to prepare inputs without running APSIM. `--algorithm auto` (default)
chooses NSGA-II for 1-3 selected criteria and NSGA-III for 4 or more. Algorithm
selection depends on the number of objective functions, not the number of decision
variables (still one sequence index per field). Explicit `--algorithm nsga3`
also supports 2-3 criteria; NSGA-III with one criterion is rejected. This planner
requires NSGA-III for 4 or more criteria. Duplicate/unknown/empty criteria are rejected.

Available names: `yield`, `profit`, `mineral_n`, `humic`. Goals are evaluated
simultaneously as separate F components, in the supplied order. All original
crop-count and per-field Humic constraints remain active, even if Humic is not a
selected objective. The existing historical management workflow is unchanged;
criterion selection in this section applies to the future crop-map workflow.

Select the fixed management policy independently with e.g.
`--management-preset profit`. Without this option or a configured preset, the
first selected criterion supplies the policy. Thus changing criterion order can
change the management preset; specify it explicitly to compare searches at the
same management. Historical scores never substitute for future APSIM results.

Defaults can also be set in the scenario JSON:

```json
"optimization": {
  "objectives": ["yield", "profit", "mineral_n", "humic"],
  "algorithm": "auto",
  "management_preset": "yield",
  "settings": {"population": 10, "generations": 4, "reference_partitions": 2}
}
```

CLI options override configuration. The legacy `nsga2` section still supplies the
population/generations/seed defaults for both algorithms. `--population`,
`--generations`, `--reference-partitions` can override these values.

NSGA-III uses Das-Dennis reference directions, as described in the
[official pymoo documentation](https://pymoo.org/algorithms/moo/nsga3.html).
The direction count is C(M+p-1,p), where M is the number of criteria and p the
partition count. If p is omitted, the largest partition count fitting the
population is chosen. For four criteria and population 10, p=2 gives 10 directions.
Insufficient population is rejected before APSIM starts; the budget is never
silently increased. The default 10 individuals and 4 generations are a small
trial budget, not evidence of convergence.

`pareto.json` / `pareto.csv` retain all feasible nondominated evaluated candidates;
the HTML report lists their objective vectors and links to their APSIM models.
`evaluations.csv` adds a separate `F_<criterion>` column per selected criterion.
All F components are minimized: negative yield, negative economic result,
mineral-N shortfall, and maximum relative Humic loss over fields.
There is no unique best multi-objective result. The displayed map is a
representative of the Pareto set: minimum equal-weight squared distance to its
ideal point after per-criterion min-max normalization **within that set**.
This rule affects only display selection, not the optimizer. Ties use candidate
ID. If no feasible solution exists, display selection first restricts to minimum
total positive constraint violation; this remains explicitly diagnostic.

Run settings, objective order and policy source are recorded in
`scenario_inputs.json`, `status.json`, and `optimization_summary.json`.

### Five, six or more criteria

There is no upper limit of four in the optimizer, objective vector, exports or
HTML report. Four built-in criteria are available without configuration. Define
additional criteria explicitly in `optimization.additional_objectives`:

```json
"additional_objectives": {
  "n_input": {"label": "Minimum applied nitrogen", "metric": "fertilizer_n_kg", "direction": "min"},
  "n_cost": {"label": "Minimum fertilizer cost", "metric": "fertilizer_cost_proxy", "direction": "min"}
}
```

These are optional definitions, not automatically enabled goals. Choose them
alongside the built-in goals with `--objectives`. The supplied example JSON
`models/apsim/configs/future_rotation_many_objectives_example.json` selects all
six, keeps management preset `yield`, and uses automatic NSGA-III:

```powershell
python run.py apsim --config models/apsim/configs/future_rotation_many_objectives_example.json --objectives yield profit mineral_n humic n_input --run
python run.py apsim --config models/apsim/configs/future_rotation_many_objectives_example.json --run
```

Available additional scalar metrics are `yield_dm_kg`, `net_profit_proxy`,
`mineral_n_shortfall`, `fertilizer_n_kg`, `fertilizer_cost_proxy`, and
`humic_total_change_kg`. Applied N and fertilizer cost use actual APSIM application
events attributed to harvest years within the evaluated future period, weighted
by field area. Carbon change sums end minus start, also weighted by area. A total
carbon-gain criterion never relaxes the existing per-field Humic constraints.
`direction: max` negates the metric in F; `min` uses it unchanged. Expressions
and unsupported metrics are rejected. Metrics requiring new APSIM outputs must
first be implemented and validated rather than invented in configuration.

Additional names can be used in any order/subset. If no management preset is
specified, the first selected built-in criterion supplies it, or `yield` if only
additional criteria were selected. `--all` retains its previous meaning of four
separate built-in single-objective preparations. As the number of goals grows,
the population must still fit the NSGA-III reference-direction count; invalid
budgets fail before APSIM starts. Extra goals can be correlated or redundant;
enabling more is a modeling choice, not a guarantee of a better plan.

## Existing single-criterion launchers

From the project root:

```powershell
python run.py apsim --objectives yield --run
python run.py apsim --objectives profit --run
python run.py apsim --objectives mineral_n --run
python run.py apsim --objectives humic --run
```

Choose one command. Each runs an independent, single-objective search. Without
`--run`, each command only prepares files and a starting map. It does not claim
that this starting map has maximized the objective.

To prepare all four cases without APSIM:

```powershell
python scripts/plan_future_rotation.py --all --prepare-only
```

The public `python run.py apsim` entry point supports the local `.deps`
directory. The CLI also supports `--config`,
`--output` (a new directory), and `--verify-model` (one simulation, no search).

## Configuration and scope

The common configuration is
`models/apsim/configs/future_rotation_2025_2029.json`. It references:

- `rotation_planning_2025_2029.yaml`: supplied 2024 field states, grass ages,
  matrix threshold, candidate states and historical crop-count ranges.
- `simulation/calibrated_history`: the new model and weather files.
- The completed management optimization run: the four management presets and
  inherited diagnostic economics. Source model/weather hashes must match.

The retained example plans 2025-2029, preceded by the supplied 2024 crop states.
The historical 2004-2023 rotation arrays remain unchanged. A 2024 bridge and the
five proposed years are appended to `RotationManager` and the fertilizer arrays.
Any longer gap between calibration and planning requires explicit intermediate
field states; missing years are not invented.

Future cultivar, density, depth, row spacing, sowing/harvest dates and grass-age
profiles are cloned from the latest compatible calibrated occurrence on the
same field, falling back to another calibrated field only if necessary. These
are explicit example assumptions, not observed target-year technology.
`scenario_inputs.json` records every source field/year used for a future profile.

The scenarios keep these prior management policies fixed while optimizing maps:

| Scenario | Prior candidate | Single objective |
| --- | --- | --- |
| `yield` | `candidate_028` | Maximize total harvested dry matter, kg |
| `profit` | `candidate_014` | Maximize the diagnostic economic result |
| `mineral_n` | `candidate_018` | Minimize summed normalized mineral-N shortfall |
| `humic` | `candidate_032` | Maximize the minimum relative Humic change across fields |

These IDs correspond to the current source run. They are reselected from its
actual successful evaluations, not hard-coded as future performance. None passed
the historical Humic constraint. Their management settings are starting presets,
not approved recommendations. The Humic preset was selected by the historical
sum of positive violations; the new future objective uses the worst field.

This is crop-map optimization at fixed management per scenario. It is not a joint
search over every future crop, fertilizer rate and sowing parameter. All future
scores are calculated by APSIM; no historical per-crop score table is substituted
for simulation of the new sequence.

## Weather is an explicit scenario

The supplied weather ends at 2024-01-01. The example keeps actual file records
through 2023-12-31 and replays archived years for the remaining period:

| Target year | Archived weather year |
| --- | --- |
| 2024 bridge | 2018 |
| 2025 | 2019 |
| 2026 | 2020 |
| 2027 | 2021 |
| 2028 | 2022 |
| 2029 | 2023 |

Dates match by month and day, not day-of-year index. A missing source February 29
is interpolated from February 28 and March 1; unnecessary source leap days are
omitted. The isolated actual source record for 2024-01-01 is not mixed into the
analogue year. These assumptions and input hashes are retained with each result.
This is a climate-analogue experiment, not a weather or agricultural forecast.

## Decisions and constraints

Each integer variable chooses one complete legal sequence for a field. The
existing planner enumerates sequences at the configured matrix threshold and
retains compulsory/optional continuation of existing perennial stands. Potato
and the separate legume-cereal-mixture state are excluded because matching
calibrated APSIM components/profiles are not supplied. The supported underseeded
oat state includes the calibrated Oats + RedClover + PlantainForage group.

The initial map is the planner's farm-wide selection among the highest-scoring
matrix sequences. The APSIM search can explore other permitted sequences with
transition score at least 0.5, rather than requiring every transition to be 1.0.

Hard constraints apply in every objective scenario:

- Humic C on 2029-12-31 must be at least its own stock on 2024-12-31 on each field.
  A gain on another field cannot compensate for a loss.
- Annual and horizon crop-count bounds must be met. This is an explicit change
  from the original matrix planner, which used these ranges as tie-breakers.
  Counts refer to fields, not hectares; calibrated fields currently have 1 ha each.

For mineral N, a native reference is calculated using the initial map and
calibrated management profiles, under the same extended history and weather.
The objective sums positive normalized end-of-year deficits relative to that
reference. It does not treat increased mineral N as unlimited agronomic benefit.

Profit inherits the diagnostic price/cost block of the completed source search.
Only harvested Wheat/Oats grain currently has a price; other products have zero
revenue. Supply a complete `economics` block in the future JSON to override this
explicitly when confirmed prices are available. Seed-cost differences, P, K and
unavailable indicators are not invented.

Default search settings remain population 10, generations 4, seed 42. The legacy
four launchers use one objective; the general launcher supports selected subsets.
Every new map requires a continuous 2004-2029 APSIM run.
This can take substantial time. Searches cache duplicate maps within a run,
retain failed candidates with penalties, and can finish with no feasible solution.
The finite search does not establish a global optimum or infeasibility proof.

The native terminal behavior is retained: no unobserved 2030 planting is appended
to alter the end-of-2029 result. Calibration and species-proxy limitations remain.

## Artifacts and review

Every invocation creates `runs/future_rotation/<objective>_<timestamp>` with:

- `prepared_future.apsimx`, `weather.met`: explicit prepared seed model.
- `scenario_inputs.json`, `sequence_options.json`: assumptions, hashes, fixed
  management preset and exact sequence options for each integer variable.
- `crop_map.csv`, `crop_plan.json`, PNG, text and HTML: viewable map artifacts.
- `status.json`: mode and current stage; preparation is clearly distinguished
  from completed optimization.
- With `--run`: baseline/candidate models, native databases, audits,
  `evaluations.json`, `evaluations.csv`, `pareto.json`, `selected_result.json`,
  `selected_future.apsimx` matching the displayed selected map, and
  `optimization_summary.json`. `prepared_future.apsimx` remains the original seed.
- With `--verify-model`: one native simulation and `verification.json`, without
  claiming that the seed map is optimized or validated against future observations.

Each run has its own `runs/future_rotation/<run>/index.html`. Initially the
four maps may be identical because they are shared legal seeds. Objectives can
produce different maps only after the corresponding searches are executed.
If constraints cannot be met, the displayed map is explicitly diagnostic.
