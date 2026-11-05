# Active full-history APSIM simulation

Imported from `new_calibr/result` on 2026-10-29. `simulation.apsimx` and
`weather.met` are byte-for-byte copies of the exported calibration artifacts.
`import.json` records their hashes; `provenance.json` is the original calibration
provenance. The source model covers 2004-01-01 through 2023-12-31, with ten 1 ha fields.
Active pure-APSIM configs now set `field_area_ha: 0.1`: the executed copies use
ten 0.1 ha plots (1 ha total). The original export and its hashes are retained.

From the project root, run:

```powershell
python -m agro --config launchers/configs/apsim_baseline.json --simulate-only
```

The launcher creates a new folder under `runs/apsim_baseline_<timestamp>`.
`simulation_results.db` contains the native harvest, phenology and daily reports;
`apsim.log` contains process output; `simulation.json` lists report row counts;
`execution.json` records input hashes, period, fields and completion status.
The native simulation preserves all exported management, dates and reports.
It does not perform optimization or recalibration.

Both APSIM baseline launch profiles use this model. Legacy optimization and
AquaCrop profiles retain their previous inputs: their preparation expects the
old `Sow using a variable rule` manager, whereas this export uses
`RotationManager`. The new adapted multi-year optimization is available separately:

```powershell
python -m agro.apsim.workflows.run_multiyear_rotation_optimizer --run --config models/apsim/configs/multiyear_rotation_native_2020_2023.json
```

Its configuration is `models/apsim/configs/multiyear_rotation_native_2020_2023.json`.
See `docs/native_multiyear_optimization.md` for objectives and output files.
Prior baseline launcher configurations are saved under
`models/apsim/configs/legacy_launchers` as historical snapshots.
