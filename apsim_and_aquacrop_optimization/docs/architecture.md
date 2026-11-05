# Component boundaries

A model is a concrete set of configurations and input data. APSIM models consist
of `.apsimx`, YAML and data files. AquaCrop models contain native crop, soil,
weather, management and project files, plus declarative preparation metadata.
Preparation and launch code lives in `agro.apsim` and `agro.aquacrop`, outside
the model directories. Engine equations and bindings belong to
`engines/aquacrop`; APSIM remains an external installation.

```text
NSGA-II -> decision decoder -> SimulationEvaluator -> SimulationBackend
                                                     | APSIM adapter
                                                     | AquaCrop adapter
        <- F/G             <- objective function <- SimulationResult
```

`agro.optimization` imports no model code. It accepts candidate evaluation callbacks
and validates objective/constraint dimensions and finite values. Objective values
are minimized; inequalities use `G <= 0`. Integer and mixed-variable searches keep
the previous algorithms, sampling, crossover and mutation settings. Nondominated
selection includes feasible successful candidates from all evaluated generations.

`SimulationBackend` binds a model and evaluation years. Its `simulate(decisions,
workdir)` method returns a `SimulationResult`, or raises an execution/validation
error. `Capabilities.require` rejects incompatible metric/decision requirements
before calculations start. The evaluator caches successful results within one run
and saves the common result as `simulation.json` beside the native artifacts.
Create a new evaluator whenever model data, engine settings or objectives change.
The general runner stops on an invalid candidate; the preserved APSIM workflows
retain their existing failure penalties and diagnostic artifacts.

The contracts use harvested **dry matter**, kg/ha; area, ha; mineral nitrogen,
kg/ha. APSIM harvest values already use kg/ha. AquaCrop native harvest events use
t/ha and are multiplied by 1000. Repeated forage cuts are summed; standing biomass
is never counted as removed harvest. Missing nitrogen or carbon output is absent,
not zero. AquaCrop does not supply APSIM's mineral-N or Humic constraints. The
current common APSIM adapter exposes mineral N and urea N; the preserved
multi-year workflow additionally reads and evaluates Humic reports.

Common results retain crop labels supplied by each model. For comparisons across
different scenarios, field identity, crop/product identity, area, dates, weather,
initial state and harvest interpretation must be explicitly aligned. Effective
crop mixtures are not mechanistic species competition. The common result schema
provides an execution interface, not a claim of numerical interchangeability.

## Model adapters

`ApsimBackend` reuses the existing model preparation and strict SQLite readers.
When management decisions are supplied, every field must have an explicit
fertilizer option and dose. The example search uses the model's configured
`reference_decision` as its base. A baseline with no decisions preserves source
management. `source_reports: optimizer_only` is an explicit option that removes
legacy diagnostic reports **from the prepared copy** and retains `OptSoil_*` and
`OptHarvest_*`. This avoids an ambiguous `[NFlow].Natm` source report in the locally
installed APSIM version. The original model is never patched in place. Executable
failure, timeout, missing output or a stale pre-existing database fails closed.

`AquaCropBackend` runs `agro.aquacrop.runner` in a child Python process. That
process copies inputs, prepares selected fields, applies named crop-parameter
overrides to generated crop files, and calls the Rust field runner. The process
owns all mutable preparation state, so different candidates do not modify a
shared model. Input file hashes, engine command and native output files are
retained. Fixed-rotation preparation also uses the reviewed management events
and harvest dates/product identities supplied as model inputs under `management`.
Simulated yields and SQLite result databases are not needed for preparation.
The model supplies its own `DATA/Simulation.PPn` and complete crop catalogue;
missing crop inputs are errors rather than an invitation to invent defaults.

Crop overrides are addressed by field, segment and parameter name, e.g.
`fixed_field_01/crop_parameters/2020_ForageMix_Local/water_productivity`.
Unknown fields, segments, parameters, non-finite values and format-metadata
changes are rejected. Numerical validation remains the engine's responsibility.
APSIM fertilizer doses and AquaCrop fertility stress are different quantities.

## Existing workflows and extensions

Model preparation and historical replay live in `agro.apsim`.
The retained multi-year command is
`python -m agro.apsim.workflows.run_multiyear_rotation_optimizer --run`.
Native result parsing lives in `agro.adapters`, scoring in `agro.evaluation`,
and NSGA-II in `agro.optimization`. Python modules are no longer imported from
`models`. Results and prepared candidate copies are written under `runs`.

To add a model, supply an adapter with explicit capabilities and common results.
To add objectives, pass a callable `(SimulationResult, decisions) -> {F, G, ...}`
to `SimulationEvaluator`; declare its required metrics and decisions. Then call
the same integer or mixed-variable search. The generic CLI examples intentionally
use only harvested dry matter; the existing three-objective APSIM task remains
available through its original workflow.

Rust numerical preparation, daily process order and checkpoint continuation are
unchanged. The field runner now lives at `engines/aquacrop/field-runner` and is a
separate Cargo workspace depending on `../crates/aquacrop-core`. Test lint
annotations were adjusted for the installed Clippy version; equations and
assertion semantics were preserved.
