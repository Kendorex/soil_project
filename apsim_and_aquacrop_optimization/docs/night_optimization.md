> Update 2026-11-03: new APSIM and coupled future searches use `humic_constraint: "objective_only"`. Humic is always included as an objective (automatically appended if omitted), not a feasibility constraint. Absolute per-field losses remain reported; feasible does not imply carbon conservation. NSGA-II night jobs now use yield/profit/humic; NSGA-III uses yield/profit/mineral_n/humic. Standalone AquaCrop is unchanged. Earlier strict-conservation descriptions below document the prior setup. Existing runs and frozen night batches retain their original rules. Start a NEW batch with `python -m agro.night_queue`, rather than resuming the completed old batch, to use the new policy.

# Sequential overnight optimization queue

Start from the project root, or double-click `python -m agro.night_queue`:

```powershell
python -m agro.night_queue
```

This is an actual full search at the configured budget, not a model-verification
run. Six jobs run one after another, with no inter-job pauses or confirmations:

| Order | Model | Algorithm | Objectives |
| --- | --- | --- | --- |
| 1 | New-model APSIM future map | NSGA-II | yield, profit |
| 2 | New-model APSIM future map | NSGA-III | yield, profit, mineral_n, humic |
| 3 | Native AquaCrop harvests + APSIM soil | NSGA-II | yield, profit |
| 4 | Native AquaCrop harvests + APSIM soil | NSGA-III | yield, profit, mineral_n, humic |
| 5 | Standalone AquaCrop | NSGA-II | yield, profit |
| 6 | Standalone AquaCrop | NSGA-III | yield, profit, water_use, drainage |

Every job uses population 10, generations 4 and seed 42 by default, up to about
40 requested evaluations per job (duplicates can reduce unique simulations).
APSIM/coupled jobs also calculate their reference scenario. All horizons retain
the current 2025-2029 example and its explicit weather analogues. Management
preset `yield` is held fixed for both APSIM/coupled algorithms. The standalone
model uses its own native inputs and economics, with no APSIM runtime.

The two algorithms have different objective sets here; this is not a controlled
algorithm-performance comparison. Standalone costs differ from the coupled
fertilizer accounting, and its constraints exclude Humic/N. Cross-model profit
or feasibility counts must therefore be interpreted in that context.

At the previously observed speeds, the default queue is roughly a 6-9 hour task;
actual time depends on the machine and scenarios. Completion within one night
is not guaranteed. The small default budget does not establish convergence or
global optimality. Change the budget if needed:

```powershell
python -m agro.night_queue --population 10 --generations 4
```

Queue configuration, order and objective sets are in
`launchers/configs/night_optimization.json`. The launcher freezes resolved
configuration snapshots for all jobs in the new batch directory, avoiding
relative-path changes when snapshots are moved. Original model inputs and
previous results are preserved.

## Preparation, inspection and restart

```powershell
# Show exact commands, without creating outputs or running engines.
python -m agro.night_queue --dry-run

# Check Python/executables/input paths and freeze the queue without simulations.
python -m agro.night_queue --prepare-only

# Run the last prepared batch, or resume unfinished jobs after an interruption.
python -m agro.night_queue --resume
# Equivalent double-click launcher:
python -m agro.night_queue --resume

# Resume a particular batch.
python -m agro.night_queue --resume "C:\path\to\runs\night_optimization\batch_..."
```

`--resume` preserves the original budget/snapshots; omit population/generation/
seed/output overrides. Completed searches are skipped, including searches whose
result is `no_feasible_solution`. Failed or interrupted jobs start in a fresh
`attempt_002`, etc.; their earlier logs and partial models are retained.
Individual interrupted optimizations restart from their beginning, not from an
intermediate evolutionary generation. If a child finished but its parent queue
was lost, completed artifacts are recovered. A still-running prior child blocks
restart rather than allowing overlapping engine calculations.

By default, a technical failure is logged and the next job starts. Missing
feasible solutions are an ordinary completed outcome. `--fail-fast` optionally
stops the queue at the first technical failure. Missing executables/dependencies
are checked before a new batch starts, instead of failing after hours of work.

Ctrl+C stops the current owned process tree and marks the queue interrupted;
pending jobs do not start. The workspace lock prevents two night queues from
running simultaneously. It does not manage independent jobs launched elsewhere.

## Overnight behavior and results

The queue prevents automatic Windows idle sleep while active and restores its
thread execution request on exit. It does not change system power settings,
disable updates, schedule shutdown or override an explicit sleep/lid-close
action. Keep the machine on and powered; the display may turn off normally.
`--allow-sleep` disables this temporary idle-sleep prevention.

Each batch is stored under `runs/night_optimization/batch_<timestamp>`:

- `configs/`: frozen resolved configuration per job.
- `logs/`: one UTF-8 console log per attempt.
- `outputs/<job>/attempt_001/`: ordinary complete optimization artifacts,
  candidate simulations, maps, Pareto tables and the model's own HTML report.
- `status.json`: overall stage, current child PID/status and per-job outcomes.
- `results.csv`: all six outcomes, durations, feasibility counts and selected
  diagnostic/feasible yield and economic metrics.
- `index.html`: overview with links to each result/log/status;
  refreshes every 15 seconds while the queue runs.

Open `runs/night_optimization/index.html` to inspect the latest batch. No Codex
session is needed for the local Python queue to continue. No engine is launched
by `--dry-run` or `--prepare-only`.
