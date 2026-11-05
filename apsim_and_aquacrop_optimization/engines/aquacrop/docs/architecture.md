# Architecture

## Compatibility boundaries

The project maintains two distinct compatibility contracts.

The `file-io` feature is the AquaCrop file compatibility boundary. Readers accept the supported
Fortran implementation's input conventions, including missing-value sentinels and version-gated
fields. They normalize those conventions into Rust domain types before returning. Result writers
perform the inverse conversion and preserve the reference file names, layouts, rounding, and
missing-value encodings.

`aquacrop-core` is the numerical compatibility boundary. Given equivalent model parameters,
climate input, initial state, and management input, it preserves the supported AquaCrop version's
daily process order, rounding, clipping, units, version-gated behavior, and numerical results.
The computational API does not preserve Fortran storage conventions: absence is represented by
`Option`, mutually exclusive states by enums, and invalid inputs by `Result`.

The supported version and upstream revision are determined by the version of the reference implementation in `third-party/KUL-RSDA/AquaCrop`. The tracked reference under `spec/` describes the equations and
compatibility rules. The subtree testcase proves the implemented end-to-end path for one official
project; it is regression evidence, not a substitute for the specification.

## Crate responsibilities

### `aquacrop-core`

The core owns:

- reference-unit domain types and compatibility primitives;
- soil-water and salt state;
- crop development, stress, evapotranspiration, biomass, harvest, cutting, and perennial processes;
- the explicit daily simulation state machine;
- daily and seasonal aggregation;
- the crate-level `Error` returned by fallible core APIs;
- optional AquaCrop readers, project runners, and result writers under `file-io`.

The computational modules build without `file-io` and do not depend on filesystem orchestration.
Fortran sentinels are decoded inside `io` and must not be used as computational state.
Public numeric APIs document their AquaCrop units. The crate forbids unsafe code and favors
concrete data structures that can later be adapted by language bindings.

Daily, aggregated, seasonal, cutting, and evaluation results are public core data. Their derivation
is always available without `file-io`; the feature only adds AquaCrop readers, filesystem
orchestration, and conversion of those results to reference-compatible text files. A direct Rust
consumer must be able to obtain at least every meaningful value emitted by the supported result
writers without creating or parsing a file.

The `io` module owns only AquaCrop file-format behavior: readers, writers, paths, directory
discovery, format metadata, sentinel conversion, and the high-level workflows that resolve a
project directory, execute the simulation engine, and write requested files. Numerical
preparation, report derivation, aggregation, interpolation, and evaluation statistics remain in
filesystem-independent core modules. An `io` workflow may call those operations but must not
implement their formulas.

The public data model has four explicit roles:

- reusable user input is represented by `Crop`, while `SeasonCrop` contains a complete
  crop calendar selected for one temperature sequence and is the crop input accepted by
  `SimulationConfig`; `SoilProfile` and `InitialConditions` contain their supplied values;
- `PreparedCrop` and `PreparedSoilProfile` contain validated invariant derivations, while the
  private `PreparedConfig` contains season- and climate-dependent working values behind the
  public, non-serializable `PreparedSimulation` reusable-run template;
- `DailyResult`, `FinalResult`, `AggregatedResult`, and `SimulationResult` are
  filesystem-independent reporting structures, while `SimulationCheckpoint` contains only
  persistent continuation state;
- AquaCrop descriptions, format versions, paths, sentinels, and other compatibility metadata
  remain in `io` structures and parsers. This includes `io::ProjectSimulation`, which groups the
  runs loaded from an AquaCrop project file and is available only with `file-io`.

`DailyWeather` is the public daily atmospheric input. It contains minimum and maximum temperature,
reference evapotranspiration, rainfall, and atmospheric CO2 only. Its position in
`SimulationConfig::weather` determines the date: the first value belongs to the first simulation
day, and subsequent values are consecutive daily observations. AquaCrop day numbers are attached
only while constructing the private daily working input.

Irrigation schedules remain `IrrigationSchedule`; externally supplied daily irrigation uses a
separate `DailyIrrigation` sequence. Daily groundwater values are likewise supplied separately
from weather. `CalibrationClimate` groups the validated 365-day reference-temperature climatology
and reference-year CO2 required only by calibrated fertility and salinity calculations. Building
that reference climate is a filesystem-independent core calculation. Aligning separate `.TMP`,
`.ETo`, and `.PLU` records and repeating AquaCrop's period CO2 value are file-format orchestration
owned by `io`.

`CropCycle` owns the reusable time basis together with its development calendars and canopy growth
and decline coefficients. Calendar-day cycles contain one complete `DevelopmentCalendar` and no
GDD calendar. A complete GDD cycle contains both calendar-day and GDD calendars. An
`UnresolvedGrowingDegreeDays` cycle contains a complete driving GDD calendar and a
`PartialDevelopmentCalendar`; AquaCrop file sentinels decode to `None` only in that unresolved
representation.

`CropParameters` groups the non-cycle parameters shared by `Crop`, `SeasonCrop`, and
`PreparedCrop`. Its fertility-stress, rooting, and canopy types are generic because preparation
replaces those three user inputs with derived representations. `Crop` and `SeasonCrop` flatten
their `parameters` field in the serde representation, preserving the public top-level JSON shape;
`PreparedCrop` has no serde contract.

`CropSeason` always carries a GDD method and optionally carries daily temperatures.
`CropSeason::default()` selects AquaCrop's standard `ClampUpperTemperature` method without
temperatures; `new` selects another method, and `new_with_temperatures` additionally supplies the
sequence required to resolve an incomplete GDD calendar. `Crop::for_season(CropSeason)` returns a
serializable `SeasonCrop`. `SeasonCropCycle` contains only complete
calendar representations; its GDD variant also retains the `gdd_method` used to create and run
that seasonal calendar. `SimulationConfig` accepts only `SeasonCrop`, so unresolved crop input
cannot reach simulation initialization. The AquaCrop project runner performs this transition
automatically after reading the crop, temperature, and program-parameter files.

`PreparedCropCycle` contains validated complete calendars for numerical process APIs and retains
the seasonal GDD method. `PreparedCrop` adds invariant numeric derivations but no filesystem
metadata. `SimulationParameters::gdd_method` remains the fallback for
calendar-driven crops because AquaCrop still calculates daily GDD for temperature stress and
reporting in calendar mode; a GDD-driven crop always uses the method retained by its cycle.

`SimulationConfig::initialize(self, initial_conditions)` consumes the seasonal crop and remaining input, validates it,
derives the private working configuration and state, and returns a ready `Simulation`.
`SimulationConfig::prepare(self)` instead returns a `PreparedSimulation` template that can be
cloned explicitly before each additional run with distinct `InitialConditions`. Its shared
prepared inputs are reference counted internally, while each run owns its mutable timing and
simulation state. Crop and soil preparation follows the same consuming input-to-`Prepared*`
convention internally. Prepared structures must not acquire filesystem metadata, and input or
reporting structures must not acquire transient numerical scratch fields.

The optional `serde` feature implements `Serialize` and `Deserialize` for public user-input,
reporting, aggregation, and continuation-state contracts and for the value types nested inside
them. It deliberately excludes `Prepared*` structures, `Simulation`, private working state,
low-level process structures, and `io` compatibility types. Deserializing a contract reconstructs
input data only; the normal `SimulationConfig::initialize` validation boundary remains mandatory.
Serialized field names use `camelCase`, and enum values and discriminators use `snake_case`.
Enums with data use an internally tagged representation with `type` as the discriminator.

### `aquacrop`

The facade is currently thin: it re-exports `aquacrop-core` as `aquacrop::core`. This preserves the
intended crate boundary without pretending that the planned typed-unit, builder, and iterator APIs
already exist. Those ergonomics belong here when implemented; conversions to core units must
remain explicit and tested.

### `aquacrop-cli`

The `aquacrop-cli` package produces the `aquacrop` executable and enables
`aquacrop-core/file-io`. With no arguments it operates on the current directory; `--root`/`-C`
selects another directory. Project selection follows `LIST/ListProjects.txt` or sorted `.PRO` and
`.PRM` discovery when the list is absent.

CLI compatibility is tested at the executable boundary by copying the vendored inputs to a
temporary directory, running the binary there, and comparing the generated output set with the
reference output. Compatibility claims should stay limited to paths covered by implementation and
tests.

### `aquacrop-conformance`

The private conformance crate owns the single testcase for the supported AquaCrop version and the
cross-crate checks for parsers, process boundaries, daily results, linked runs, and output files. It
is not published.

## Simulation lifecycle

The direct engine is an explicit state machine:

```text
SimulationConfig::initialize(initial_conditions) -> Result<Simulation, Error>
SimulationConfig::prepare() -> Result<PreparedSimulation, Error>
PreparedSimulation::clone().initialize(initial_conditions) -> Result<Simulation, Error>
simulation.step() -> Result<Option<DailyResult>, Error>
simulation.finalize() -> CompletedSimulation { result: FinalResult, checkpoint: SimulationCheckpoint }
```

Initialization builds a private `PreparedConfig` for validated and derived model values and a
private `SimulationState` for mutable process state. Neither intermediate type is part of the
public input or output contract.

`step` advances exactly one model day. `None` means the configured period has ended. A direct
consumer can process each `DailyResult` without retaining the complete season.
`SimulationResult::from_completed` combines retained daily values with `FinalResult` when a
consumer needs the complete in-memory reporting dataset.

The file-based project runner is a higher-level compatibility path:

```text
project files -> io parsing -> user configuration -> preparation -> daily engine
              -> io::ProjectSimulation -> compatibility result writers
```

`io::ProjectSimulation` retains a `SimulationResult` for each file-defined run because the
compatibility writers need them for several report types and linked-run summaries. It is an
AquaCrop file-workflow type, not part of the filesystem-independent simulation vocabulary. The
`io` runner orchestrates this lifecycle. File-series alignment remains in `io`; reference-climate
derivation and report calculations remain in their filesystem-independent core modules.

## Error boundary

Public fallible core operations return `aquacrop_core::Error`. Calendar, soil, water-balance,
version, parsing, and filesystem failures are retained as nested variants so callers can process a
single crate error while preserving useful cause information. New modules should extend this
hierarchy instead of introducing an unrelated public error surface.

## API and implementation conventions

Core numeric APIs use AquaCrop reference-model units. Every public numeric argument, return value,
field, and method documents its units. Identifiers include units only when multiple representations
of the same quantity coexist in one scope.

Derives are added only for behavior that currently requires them. `Debug` is the normal baseline;
cloning, copying, equality, and ordering are not added speculatively. Serialization derives remain
gated by `serde` and limited to the documented contract boundary. The CLI uses `clap` derive for
its argument interface.

Potentially large reports are streamed through buffered file writers. File creation, writing, and
flushing failures propagate through the crate error; bounded status text may be assembled in memory
when that keeps the implementation clearer.

## Test architecture

Unit tests stay in the Rust module containing the behavior under test. Cross-crate and official
numerical checks belong to `aquacrop-conformance`; executable-boundary tests belong to
`aquacrop-cli`. Shared fixtures perform setup only and contain no assertions.

Tests prefer one behavior each. `assertables` is used when it materially improves floating-point or
relational failure output; standard assertions remain appropriate elsewhere. `expect` is permitted
in tests but not in production code.

Project and CLI conformance tests copy fixture inputs to a temporary directory, run the simulation
there, and compare the generated file set and contents with the reference outputs. Tests never
mutate the vendored testcase, and compatibility work never derives production equations from its
expected values.

## File compatibility scope

The `file-io` feature currently parses standard calendar, climate, CO2, crop, irrigation,
management, parameter, project, and soil files. It runs linked `.PRO`/`.PRM` projects with daily
climate inputs and writes seasonal, daily, harvest, evaluation, and status reports used by the
official testcase.

The current runner does not expand aggregated climate inputs or load external initial-condition
files. Dedicated intermediate aggregation reports and the irrigation-event report are also not yet
emitted by the compatibility writer. These are explicit extension points, not behavior to infer
from the testcase.

## Extended soil discretization

The AquaCrop reference implementation uses fixed arrays for at most five soil layers and twelve
calculation compartments. These are compatibility limits, not physical limits in `aquacrop-core`.
The soil reader accepts more than five layers, and the core process APIs operate on dynamically
sized compartment slices.

`PreparedSoilProfile::compartments_at_field_capacity_for_depth` creates as many 0.10 m compartments as the
requested depth requires. This extended grid is not numerically compatible with AquaCrop when it
contains more than twelve compartments because it changes the vertical discretization. The project
runner and `SimulationConfig::initialize` without caller-provided compartments therefore continue to use
the twelve-compartment reference grid, increasing lower-compartment thickness for deep root zones
as AquaCrop does. Direct simulations opt into a larger grid by supplying the extended grid through
`InitialConditions::compartments` after converting each working compartment to
`SoilCompartmentState`.

## Future boundaries

The remaining architectural work is additive:

- build the ergonomic `aquacrop` facade over the proven core;
- broaden file-format and output compatibility with specification-backed tests;
- stabilize the existing BMI/Python bindings and add C ABI support without moving ABI concerns into the core;
- design area simulation by sharing immutable inputs while keeping per-location state and process
  ordering deterministic.

None of these extensions may change the point-model results established by `aquacrop-core`.

## Workspace integration

The engine is hosted under `engines/aquacrop`. Application model inputs and
Python preparation live separately in `models/aquacrop`. The standalone
`field-runner` workspace orchestrates linked core runs and writes JSON traces;
it does not own scientific equations. Its dependency on `aquacrop-core` remains
a local path dependency with `file-io` and `serde` enabled. The top-level `agro`
package contains optimization, evaluation and engine adapters. None of those
application concerns are dependencies of the numerical core.
