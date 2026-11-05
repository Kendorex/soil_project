# Standalone AquaCrop model and optimizer

`models/aquacrop_standalone` is a separate input model. Runtime requires only
Python dependencies and the native AquaCrop field runner. No APSIM executable,
APSIM results, management presets, soil indicators or `.apsimx` file is read.
The previous coupled workflows remain available separately.

## Commands

From the project root:

```powershell
# Prepare and validate inputs; no simulation.
python run.py aquacrop

# Calculate one starting crop map; no optimization search.
python run.py aquacrop --verify-model

# One / two / three criteria use NSGA-II automatically.
python run.py aquacrop --objectives yield --run
python run.py aquacrop --objectives yield profit --run
python run.py aquacrop --objectives yield profit water_use --run

# Four or more criteria use NSGA-III automatically.
python run.py aquacrop --objectives yield profit water_use drainage --run
python run.py aquacrop --objectives yield profit water_use water_productivity drainage runoff --run
```

Without `--run` or `--verify-model`, the default is preparation only. Configuration
is `models/aquacrop_standalone/optimization.json`; optional overrides are
`--config`, `--objectives`, `--algorithm auto|nsga2|nsga3`, `--population`,
`--generations`, `--reference-partitions`, and `--output` (a new directory).
Default search budget: 10 individuals, 4 generations, seed 42. A small test is:

```powershell
python run.py aquacrop --objectives yield profit water_use drainage --population 4 --generations 2 --run
```

The integer decision vector contains one complete legal 2025-2029 crop-sequence
index for each of ten fields. Each unique map gets its own AquaCrop input files
and real native simulation. Duplicate maps are cached within the run. The crop
physiology profiles are fixed inputs, not arbitrary optimized yield multipliers.

## Native criteria

| Name | Goal and units |
| --- | --- |
| `yield` | Maximize total native harvested dry matter, kg |
| `profit` | Maximize revenue minus explicit crop-state operating costs, diagnostic RUB |
| `water_use` | Minimize total soil evaporation plus crop transpiration, m3 |
| `water_productivity` | Maximize harvested dry matter / evapotranspiration, kg/m3 |
| `drainage` | Minimize total drainage, m3 |
| `runoff` | Minimize total surface runoff, m3 |

Water fluxes come from native annual AquaCrop outputs, weighted by field area:
1 mm over 1 ha = 10 m3. This is a rainfed example; `water_use` is not irrigation
water supplied. Only the evaluated 2025-2029 period contributes to objectives.
Minimizing a flux alone can favor less productive plans, so select criteria
according to the intended tradeoff. Several water criteria can be correlated.

Humic, mineral N and N-dose goals are unsupported rather than replaced by zero.
No Humic conservation constraint is imposed. Constraints concern the stated
annual/horizon crop counts; transition rules and stand ages determine legal
field sequences. Water-balance error exceeding the configured tolerance fails
the evaluation as an invalid calculation.

All objectives are passed separately to the optimizer. Its F vector minimizes
negative yield, negative profit, positive ET, negative water productivity,
positive drainage and positive runoff, in the selected order. The output is a
feasible Pareto set. A displayed representative uses equal-weight distance to
the ideal point after min-max normalization within that set; this display rule
does not scalarize the optimization objectives. If no feasible plan is found,
the displayed minimum-violation plan is explicitly diagnostic.

## Independent data and assumptions

- `project.json` uses standalone schema 2 and lists field JSON files.
- `configs/fixed_field_*.json` contain native soil layers, field areas, crop
  profiles, calendars, initial water and six-year sequence data.
- `planning.json` supplies the 2024 field states, 2025-2029 horizon, possible
  states and explicit farm crop-count bounds.
- `rotation_rules.json` is a local copy of the crop-transition rules.
- `configs/weather.met` contains the model's own 2024-2029 climate analogue;
  ETo is calculated by the existing FAO-56 Hargreaves adapter.
- `DATA` contains the local native crop files, CO2, management and run templates.
- `optimization.json` supplies search settings, prices and crop-state costs.

Soil constants and calendar/profile labels were imported once from supplied
project inputs. `input_provenance.json` records that import and source hashes.
This is input reuse, not a runtime model link or a transfer of calibration.
The standalone loader does not open the original source files.

Initial volumetric water equals each layer's field capacity on 2024-01-01.
The complete 2024 bridge is simulated before evaluating 2025-2029. This is an
explicit initialization assumption, not a measured 2024 state or an externally
simulated state. Edit the native water arrays when observed values are available.
Mature perennial biomass/reserves are approximated by the effective crop windows.

The supplied effective AquaCrop profiles are uncalibrated approximations. The
weather after 2023 is a historical analogue, not a forecast: target 2024-2029
replays source 2018-2023. Winter and mixed stands retain documented effective-crop
limitations. The new model is not an independent validation against field data.

Default prices are 14.3 diagnostic RUB/kg dry matter for Wheat/Oats and zero for
Mix. Each crop state has an explicit 23000 RUB/ha/year aggregate operating-cost
assumption. No prior APSIM fertilizer bill is imported. Costs are not itemized
by N dose or seed rate; replace these assumptions with confirmed standalone
crop-state budgets before comparing commercial profitability. Profits are not
directly comparable to the coupled workflow with its different cost accounting.

## Artifacts

Runs are isolated under `runs/aquacrop_standalone`. Each run has its own
`index.html`; no report or pointer is automatically written to `for_me`.

- `prepared_model` / `prepared_inputs`: native JSON and generated executable inputs.
- `scenario_inputs.json`: objectives, search settings and hashes of all source inputs.
- Candidate folders: `model`, executed `simulation`, native daily/annual/harvest
  outputs, `simulation.json` and scored `result.json`.
- `evaluations.json`, `evaluations.csv`, `pareto.json`, `selected_result.json`,
  `optimization_summary.json`, crop-map CSV and local HTML report.
- `verification.json` only for one-model verification mode.

No process is started implicitly when importing the optimizer module. Start a
full search manually with `--run`.
