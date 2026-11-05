# APSIM: 0.1 ha plots and extended search

The active pure-APSIM baseline, native historical optimizer, and future configs
now set `field_area_ha: 0.1` (ten fields, total 1 ha). The original calibration
export remains byte-identical. The override changes actual zone `Area` in the
executed `.apsimx`, not merely displayed totals. Source and executed model hashes
are recorded separately by the full native baseline. Completed runs and frozen
queue snapshots retain their original area.

Future pure-APSIM defaults set `optimization.crop_map_repair: "milp"`.
Before simulation and during population repair, a small integer program chooses
the closest combination of existing legal field sequences satisfying the same
annual and whole-horizon crop-count limits. Management variables are retained.
Nothing relaxes the hard constraints. Legal maps remain unchanged. Repair cannot
guarantee higher yields or a converged optimum. `crop_map_repair.json` records
solver counts and timing. The AquaCrop configs have not been changed.

Observation and repair audit:

```powershell
python scripts/check_apsim_readiness.py --run-probes
```

This runs two short historical APSIM replays at 0.1 ha plus an original 1 ha
control, checks per-hectare harvests, and tests multiplying only weather `sp`
by 100. Small per-hectare area effects are reported, not assumed to be zero. It also
analyzes the calibration bundle's frozen chronological pre-update forecasts
separately from final-fit results. Real-weather and operation inputs were known;
this is conditional rolling validation, not a fresh independent field trial.
Local observation copies are kept in `models/apsim/data/observations/new_calibr`.
No biological parameters or economic prices are refitted by this audit.

Extended search, MANUAL launch:

```powershell
python -m agro.night_queue --config launchers/configs/apsim_extended.json
```

A queue is already prepared, with no attempts started. To run that exact queue:

```powershell
python -m agro.night_queue --config launchers/configs/apsim_extended.json --resume runs\night_optimization\apsim_extended_0p1ha_ready
```

Three sequential pure APSIM joint NSGA-II jobs, same three objectives
`yield profit humic`, same 20-variable domain, seeds 42, 123 and 2026.
Population 20, generations 10: up to 200 evaluations/job, up to 600 altogether,
plus each job's baseline. Duplicate combinations are cached, so unique engine
calls can be fewer. Humic remains objective-only; economics remains diagnostic.
This does not compare NSGA-II with NSGA-III. Runtime around a day is only an
estimate from earlier runs, not a deadline or a convergence guarantee.

Prepare the frozen queue without running simulations:

```powershell
python -m agro.night_queue --config launchers/configs/apsim_extended.json --prepare-only
```

Resume using the explicit batch folder printed by the command:

```powershell
python -m agro.night_queue --config launchers/configs/apsim_extended.json --resume runs\night_optimization\batch_<timestamp>
```

`--resume` without a path uses the most recently published night queue, which
could belong to a different scenario. Frozen older queues are not upgraded.
The existing OS queue lock prevents concurrent queues. A command-line `--seed`
override intentionally replaces all per-job seeds; omit it for independent runs.
