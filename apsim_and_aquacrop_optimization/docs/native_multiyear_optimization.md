# Multi-year optimization of the new calibrated history

Run from the project root:

```powershell
python -m agro.apsim.workflows.run_multiyear_rotation_optimizer --run --config models/apsim/configs/multiyear_rotation_native_2020_2023.json
```

The launcher supplies the local dependencies and invokes the existing workflow
with `models/apsim/configs/multiyear_rotation_native_2020_2023.json`. The
`native_history_rotation` adapter selects `agro.apsim.native_rotation_optimizer`
instead of the legacy source-model replay. The prior optimization remains
available with its prior configuration.

The new source is the byte-preserved `new_calibr/result` export under
`models/apsim/simulation/calibrated_history`. Model and weather hashes are recorded
for each search. Candidate models, databases and reports are independent copies.

## Search scope

- Simulate the full 2004-2023 history; score harvest years 2020-2023.
- Keep crop sequence, sowing/harvest dates and calibrated crop physiology.
- Select spring wheat cultivar, population, depth and row spacing; oat population
  and depth; winter rye population; and nitrogen form/dose for each crop policy.
- Preserve historical oat and rye cultivars, perennial mixture components and all
  other crop settings. Policies are shared across occurrences of each crop.
- Harvest-year policies also affect autumn sowing in the preceding calendar year.
  In particular, the policy for a 2020 winter crop can affect autumn 2019.
- Use NSGA-II with population 10, generations 4 and seed 42. This is approximately
  40 unique candidate simulations plus the baseline, not an exhaustive search.

## Objectives and constraints

Minimize `F = [-dry_harvest_kg, -diagnostic_profit, mineral_N_shortfall]`.
Harvest masses come from native `HarvestReport_Field*` events, converted from
t/ha to kg/ha and multiplied by the calibrated 1 ha field areas.

Profit uses the inherited diagnostic prices for harvested Wheat/Oats grain,
actual native fertilizer application events, application passes and annual fixed
costs. Products without a supplied price contribute zero revenue; their harvested
dry mass still contributes to the first objective. Autumn rye application events
are assigned to the next harvest year. These are model comparison prices.

The third objective sums normalized end-of-year mineral-N shortfalls relative to
a newly calculated native baseline. It is a soft objective, not a hard threshold.
Ten separate hard constraints require whole-profile Humic C on 2023-12-31 to be
at least its own candidate's stock on 2019-12-31 on each field. Cross-field gains
cannot compensate for a loss. The report expression is
`sum([Nutrient].Humic.C)` in kg C/ha. Missing soil output fails evaluation.

The prepared model retains native harvest reports and adds daily `OptSoil_Field*`
reports for mineral N and Humic C. General daily and phenology reports are omitted
to reduce output; the management and numerical crop model remain in place.

## Tracking and results

Every search creates `runs/apsim/native_multiyear/optimize_<timestamp>`:

- `status.json`: stage, PID, current candidate and completed counts.
- `config_snapshot.json`, `sources.json`, `variable_space.json`: exact inputs.
- `baseline`: native reference, including soil and actual applications.
- `candidates/candidate_*/`: model, weather, database, log, audit and evaluation.
- `evaluations.json`, `evaluation_history.json`: incrementally saved scores.
- `pareto.json`: incrementally saved feasible nondominated candidates.
- `optimization_summary.json`: final completion or no-feasible-solution result.

The all-zero vector is checked before NSGA-II; it selects the first option of
each list, including zero nitrogen. Successful repeated decisions use an in-memory
cache. Candidate failures receive large objective/constraint penalties and retain
their artifacts; baseline or preflight failure stops the search. A hard constraint
can produce an empty feasible front, which is reported explicitly.

Existing calibration errors, zero barley grain, species proxies and phenology
limitations remain. This workflow does not recalibrate or repair those processes.
