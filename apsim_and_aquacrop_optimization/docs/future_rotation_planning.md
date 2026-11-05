# Future crop-rotation planning

The planner was imported from `soil_project/ApsimOptimization`. Its pure planning
and visualization modules live in `agro/planning`; the CLI is exposed through
`scripts/plan_crop_rotation.py` and `python -m agro.planning.cli`. Original source
hashes are recorded in `docs/rotation_planner_import.json`.

From the project root:

```powershell
python scripts/plan_crop_rotation.py
```

The launcher supplies local dependencies and works independently of its caller's
working directory. With the project dependencies installed, the equivalent is:

```powershell
python scripts/plan_crop_rotation.py
python scripts/plan_crop_rotation.py --config models/apsim/configs/rotation_planning_2025_2029.yaml
```

## Which configuration is included?

`models/apsim/configs/rotation_planning_2025_2029.yaml` is the original reference
scenario. It retains the supplied 2024 field states and plans 2025-2029. These
dates have not been silently advanced to the current calendar year.

- `initial_state_year: 2024`: year represented by the supplied initial field states.
- `start_year: 2025`, `years: 5`: planning horizon.
- `rotation_rules`: path to the transition matrix, relative to the configuration.
- `minimum_transition_score: 0.5`: permit recommended and permitted matrix transitions.
- `initial_states`: preceding crop or grass age on each of ten fields.
- `state_model`: allowed crops and compulsory/optional perennial continuation.
- `farm_crop_mix`: historical annual field-count ranges and target composition.
- `candidate_pool`, `farm_selection_beam_width`: retained sequence candidates and
  width of the whole-farm beam search; `top_k`: reported alternatives per field.

The matrix already present in this project has the same SHA-256 as the source
planner's matrix. Source workbook paths in the YAML and matrix are provenance
labels; the runtime reads the supplied JSON rules and YAML values, not a workbook.

For a new actual future horizon, copy the YAML, update `start_year`, set
`initial_state_year` to `start_year - 1`, and supply the actual preceding field
states, including grass ages. For example, a 2027-2031 plan requires 2026 field
states. Changing year numbers alone does not provide those observations.

## Algorithm and outputs

The planner enumerates allowed field sequences, distinguishes existing-stand
continuation from new sowing, ranks sequences by matrix transition quality, and
uses a beam search to select a farm-wide plan among equally ranked sequences.
Historical crop-count ranges are tie-breaking targets; any remaining violations
are reported, not hidden. Crop counts are field counts, not hectares.

Every default run creates `runs/rotation_planning/plan_<timestamp>` and a sibling
`.tar.gz` archive. Use `--output PATH` to choose a new output directory.

Outputs include `crop_map.csv`, `crop_rotation_map.png`, `crop_rotation_report.txt`,
`alternatives.csv`, `selected_transitions.csv`, `farm_crop_counts.csv`,
`plan_quality.json`, `summary.json`, plus snapshots and hashes of the inputs.

This original workflow does not launch APSIM or NSGA-II and does not predict yield,
profit or Humic. The separately adapted APSIM scenario optimizer is now available;
see [future crop-map objectives](future_rotation_objectives.md) for the four
manual entry points, crop-state adapter and explicit historical-climate assumptions.
The planner's crop catalogue can include crops, such as potato, for which the
current calibrated APSIM scenario has no matching component. The provided crop
mix sets the potato annual range to zero; compatibility planning and simulator
coverage must still be treated separately.
