# Development Status and Roadmap

The initial AquaCrop numerical port is complete for the currently supported official testcase.
This document records the implemented baseline and remaining product work; it no longer serves as
a checklist for porting every equation from scratch.

## Implemented baseline

### Repository and compatibility infrastructure

- The workspace is split into `aquacrop-core`, `aquacrop`, `aquacrop-cli`, and the private
  `aquacrop-conformance` crate.
- Compatibility is pinned by the version of the reference implementation in `third-party/KUL-RSDA/AquaCrop`.
- One current testcase is vendored under `third-party/KUL-RSDA/AquaCrop/testcase` and is updated
  via `just pull-kul-rsda-aquacrop`.
- The core builds without filesystem support; AquaCrop formats are gated by `file-io`.
- Public simulation input and output contracts support opt-in serialization through the `serde`
  feature without serializing prepared, transient, or file-compatibility structures.

### Numerical core

- AquaCrop dates, leap-year behavior, clipping, `roundc`, and growing-degree-day methods, with
  file sentinels normalized to Rust domain values at the I/O boundary.
- Soil layers and compartments, water storage, drainage, runoff, infiltration, capillary rise, and
  daily balance checks.
- Dissolved and deposited salt state, transport, concentration, groundwater interaction, and mass
  conservation.
- Canopy and root development, germination, water/temperature/fertility/salinity stress, weeds, and
  perennial behavior.
- Potential and actual evaporation and transpiration, root extraction, CO2 response, biomass,
  harvest index, pollination, yield, cutting, and assimilate transfer.
- An integrated `Simulation` state machine with explicit `initialize`, `step`, and `finalize`
  phases.
- A consuming `SimulationConfig::initialize` transition validates user configuration and initial
  conditions before constructing private working configuration and state.
- A serializable `Crop` to `SeasonCrop` transition resolves incomplete GDD calendars from an
  explicit seasonal temperature sequence before the crop enters `SimulationConfig`.
- Filesystem-independent daily weather input contains only temperature, reference ET, rainfall,
  and CO2; irrigation, groundwater, and calibration reference climate have separate contracts.
- Separate daily/final reporting structures and persistent linked-simulation checkpoints, without
  transient calculation fields in public input or continuation state.
- Filesystem-independent standard program parameters and neutral field-management configuration
  for direct `SimulationConfig` consumers.
- Daily, ten-day, and monthly in-memory aggregation primitives.

### AquaCrop project compatibility

- Readers for the standard input formats required by the official testcase.
- Linked `.PRO` and `.PRM` project execution, including retained soil water and assimilates between
  runs.
- Seasonal, daily, harvest, evaluation, and run-status output files with reference-compatible
  names and report bodies for the current fixture.
- Buffered direct file writing for result reports.
- A `clap`-based `aquacrop` executable with current-directory operation, `--root`/`-C`, project-list
  loading, and `.PRO`/`.PRM` discovery.
- End-to-end conformance tests that run from temporary copies and compare generated files with the
  official testcase in `third-party/KUL-RSDA/AquaCrop/testcase`.

## Known limitations

- `aquacrop` currently re-exports the core but does not yet provide the planned builders,
  type-safe units, higher-level validation ergonomics, or iterator adapter.
- The project runner accepts daily climate records; expansion of aggregated climate inputs is not
  implemented.
- External initial-condition files are not loaded. The supported no-file and linked `KeepSWC`
  paths are implemented.
- Output selection parsing recognizes intermediate aggregation and irrigation-event requests, but
  dedicated compatibility files for those reports are not yet written.
- The official testcase exercises a substantial but finite combination of crop, soil, climate, and
  management behavior. Other file versions and uncommon project combinations need additional
  specification-backed fixtures before compatibility can be claimed.
- Rust BMI and PyO3 Python binding crates are present. C bindings and area simulation have not been implemented.

## Next milestones

### 1. Rust facade

- Define type-safe units without changing the core's reference-unit API.
- Add builders and validation conveniences around `SimulationConfig` and initial conditions.
- Provide an iterator adapter over the daily `step` lifecycle.

### 2. Broader file compatibility

- Implement specification-backed aggregated climate expansion and initial-condition file loading.
- Complete intermediate aggregation and irrigation-event output reports.
- Add focused fixtures for `.PRO`, alternate output selections, file-version gates, and failure
  behavior.

### 3. Public integration surfaces

- Stabilize data structures intended for bindings.
- Stabilize the existing separate Python BMI binding and add C ABI support after those schemas are stable.

### 4. Area simulation

- Classify immutable shared inputs and per-location mutable state.
- Benchmark independent, batched, and vectorized execution.
- Prove point-versus-area equivalence and deterministic parallel results.

## Completion rule for future compatibility work

New compatibility paths are complete only when their equations and ordering follow the tracked
reference, focused tests cover the relevant branches, and an end-to-end test verifies behavior in a
temporary project directory. Expected testcase values must never be used to compensate for an
incorrect implementation.

## Local application integration

The surrounding workspace now separates the AquaCrop field model and Python
preparation from this engine. `field-runner` executes linked field scenarios
without changing core equations. An external Python adapter normalizes harvest
outputs and supplies them to the same NSGA-II implementation used by APSIM.
This integration does not imply equivalence between the two crop models or
add APSIM mineral-N and Humic processes to AquaCrop.
