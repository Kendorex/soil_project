> Update 2026-11-03: new APSIM and coupled future searches use `humic_constraint: "objective_only"`. Humic is always included as an objective (automatically appended if omitted), not a feasibility constraint. Absolute per-field losses remain reported; feasible does not imply carbon conservation. NSGA-II night jobs now use yield/profit/humic; NSGA-III uses yield/profit/mineral_n/humic. Standalone AquaCrop is unchanged. Earlier strict-conservation descriptions below document the prior setup. Existing runs and frozen night batches retain their original rules. Start a NEW batch with `python -m agro.night_queue`, rather than resuming the completed old batch, to use the new policy.

# Future AquaCrop maps on the new calibrated APSIM source

The future planning workflow supports `--engine aquacrop` with the same criterion
selection, legality rules, crop-count constraints, per-field Humic constraints,
NSGA-II/III routing, candidate caching, Pareto exports and representative-map
selection as the APSIM workflow.

## Manual commands

From the project root:

```powershell
# Prepare only: no APSIM or AquaCrop simulation.
python run.py coupled

# Optimize one, two, three, or four criteria.
python run.py coupled --objectives yield --run
python run.py coupled --objectives yield profit --run
python run.py coupled --objectives yield profit mineral_n --run
python run.py coupled --objectives yield profit mineral_n humic --run

# Six criteria: additionally minimize actual N input and fertilizer cost.
python run.py coupled --objectives yield profit mineral_n humic n_input n_cost --run

# One actual candidate, without an optimization search.
python run.py coupled --verify-model
```

Without `--run`, the launcher prepares files only. The default configuration is
`models/apsim/configs/future_rotation_aquacrop_2025_2029.json`: four selected goals,
automatic algorithm selection, fixed `yield` management preset, and definitions
for optional `n_input` / `n_cost` goals. CLI options override the configuration.
`--algorithm auto` chooses NSGA-II for 1-3 and NSGA-III for 4 or more goals.
Use `--population`, `--generations`, `--reference-partitions` as needed. The trial
default of 10 individuals / 4 generations does not demonstrate convergence.

The same engine is available through `python run.py apsim --engine aquacrop`;
use the AquaCrop configuration above to make its additional criteria available.
`--management-preset` chooses the fixed management source independently.

Existing saved 2019-2023 baseline/parameter-demo launchers remain legacy replays.
In particular, `python -m agro --config launchers/configs/aquacrop_search.json` is the old single-field water-productivity
demo. Use `python run.py coupled` for the updated future-map workflow.

## What is calculated by each model

| Quantity | Source |
| --- | --- |
| Harvested dry matter | Actual native AquaCrop cuts / grain harvests, kg/ha |
| Economic result | AquaCrop harvests and configured prices, minus costs based on APSIM application events and configured fixed costs |
| Mineral-N shortfall | APSIM on the identical new candidate map and weather, relative to the fixed reference |
| Humic and per-field conservation | APSIM on the identical candidate map and weather |
| Applied N and fertilizer cost | Actual future-period APSIM application events, including winter-crop harvest-year attribution |

The engine provenance is `aquacrop_with_apsim_soil`. No additive APSIM yield
correction from the older management-response study is applied. AquaCrop biomass
does not feed back into the APSIM carbon/N balance. Thus Humic/N are explicitly
external APSIM indicators, not native AquaCrop soil predictions.

The selected management preset stays fixed within a crop-map search. Population
and crop/cultivar profile identifiers are transferred into the effective crop
preparation. The existing AquaCrop profiles remain uncalibrated effective proxies;
APSIM cultivar calibration, exact winter survival, species competition,
fertilizer/depth responses and soil-organic-matter processes are not silently
invented in AquaCrop. Fertilizer changes affect diagnostic costs and APSIM soil
calculations; no new AquaCrop N-dose yield-response function is claimed.

Only Wheat/Oats products currently have sale prices. Forage revenue is zero
unless a complete confirmed economics override is supplied. This remains the
2025-2029 example using historical climate analogues, not a future-weather forecast.

## New source and state initialization

For every proposed map, the workflow first extends
`models/apsim/simulation/calibrated_history/simulation.apsimx`, preserving its
2004-2023 history, appending the supplied 2024 bridge and the proposed 2025-2029
sequences, then applying the selected management policy. The new weather is
shared by both engines. AquaCrop reference ET is computed by the existing FAO-56
Hargreaves adapter from temperature and latitude; it is not APSIM internal
potential evaporation. Old `rotation_fixed.apsimx` and old management-event
JSON files are never used to define the future AquaCrop schedule.

APSIM runs continuously from 2004 through 2029. Optional daily layered
`[Swim3].SW` reporting supplies volumetric water on 2023-12-31. AquaCrop receives
those same physical layers and water values and runs continuously over the
2024 bridge and 2025-2029 horizon, keeping its own water/salt state between
effective crop windows. Existing perennial biomass and reserves are approximated
by the effective stand profiles, not transferred from APSIM.

Preparation-only inputs have explicitly marked placeholder water values. They
are for inspection; the full workflow regenerates the evaluated candidate with
the actual APSIM historical-end water before AquaCrop runs. The import manifest
records `initial_water_ready` and the source date. No engine runs in prepare mode.

The new planned-rotation adapter checks named crop profiles, perennial carry-over,
undersown crops, autumn sowing, harvest windows, pasture termination, and continuous
segment coverage. Unsupported profiles or overlapping crop windows fail rather
than silently using an unrelated crop or stale historical event schedule.

## Outputs

Runs are isolated under `runs/future_rotation/aquacrop/`. Each run has its own
`index.html`; automatic report copies and overview pages in `for_me` are disabled.

- `prepared_aquacrop_model/`: new source model, weather, field definitions, crop
  catalog and import manifest; historical event schedules are absent.
- `prepared_aquacrop_inputs/`: generated CRO, MAN, weather, soil and plan files.
- `verification/`, or each `baseline/` / `candidates/candidate_*` folder: separate
  `apsim/`, `aquacrop_model/`, executed `aquacrop/`, `initial_water.json`, combined
  `simulation.json`, audit and `aquacrop_manifest.json`.
- Common optimization outputs: evaluations/Pareto CSV and JSON, selected crop map,
  HTML and summary, with engine and metric-source labels. `selected_aquacrop.json`
  points to the actual executed AquaCrop inputs/results of the selected candidate.

Both executables must be installed/built. The existing AquaCrop field runner is
used unchanged. Each new candidate requires an APSIM history and an AquaCrop
bridge/horizon simulation; the full search can therefore take substantial time.
