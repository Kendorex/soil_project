# aquacrop-core

Low-level, numerically compatible implementation of the AquaCrop computational model.

The crate exposes reference-unit domain types, compatibility primitives, individual water, salt,
crop, stress, evapotranspiration, biomass, harvest, cutting, and perennial processes, plus the
integrated daily `Simulation` state machine. A simulation is initialized once, advanced with
`Simulation::step`, and consumed with `Simulation::finalize`.

Public numeric values use the units documented by AquaCrop and represent absence with `Option`.
The optional file readers and writers translate AquaCrop sentinels at the I/O boundary. Fallible APIs
return the crate-level `Error`, which preserves calendar, soil, water-balance, version, and optional
file-I/O causes as nested variants.

Direct consumers keep reusable crop definitions in `Crop`. Calling `Crop::for_season` with
`CropSeason` produces a serializable `SeasonCrop` whose calendar is complete for the supplied
seasonal input. `CropSeason::default()` is sufficient for calendar-day crops, `CropSeason::new`
selects the GDD method for a complete GDD calendar, and `CropSeason::new_with_temperatures`
additionally supplies temperatures for an incomplete GDD calendar. `SimulationConfig` owns that
`SeasonCrop` together with `SoilProfile`;
`SimulationConfig::initialize` consumes the configuration and `InitialConditions`, validates the
complete input, derives private working values, and returns a ready `Simulation`. Further
calculated crop and soil fields live in `PreparedCrop` and `PreparedSoilProfile`; the input types
remain free of transient calculation fields. The three crop states store their non-cycle values
in `CropParameters`; serde flattens that field in `Crop` and `SeasonCrop`, so their JSON shape
remains unchanged.

Daily atmospheric input is supplied as consecutive `DailyWeather` values beginning on the first
simulation day. Each value contains temperature, reference ET, rainfall, and CO2. Irrigation uses
`IrrigationSchedule` or a separate `DailyIrrigation` sequence, while an optional groundwater series
is aligned with the weather sequence. Calibrated fertility and salinity calculations additionally
require `CalibrationClimate`; `prepare_calibration_climate` derives it from a dated daily
temperature record and an `AtmosphericCo2` series without enabling `file-io`.

`Simulation::finalize` returns a `CompletedSimulation`: reporting values are in `FinalResult`, and
the separate `SimulationCheckpoint` contains only persistent state needed to continue a linked
simulation. Transient daily calculation fields are not exposed through the checkpoint.

`SimulationParameters::default()` provides the supported AquaCrop program-parameter set and
`Management::default()` provides AquaCrop's neutral field-management state. These defaults require
no filesystem feature. Crop and soil parameters, daily weather including atmospheric CO2,
groundwater, and irrigation remain explicit configuration because they are not global model
defaults.

## Serde

Enable the `serde` feature to implement `serde::Serialize` and `serde::Deserialize` for public
simulation inputs, climate, daily, final, aggregated, seasonal, and evaluation results, and
continuation checkpoints:

```toml
[dependencies]
aquacrop-core = { version = "0.1", features = ["serde"] }
```

Prepared and transient numerical structures and AquaCrop file-compatibility types are excluded.
Deserialized inputs still pass through `SimulationConfig::initialize` before they can run.
Serialized field names use `camelCase`, enum values and discriminators use `snake_case`, and enums
with data use an internally tagged representation with a `type` discriminator.

## File I/O

The optional `file-io` feature provides readers for supported AquaCrop text formats, project
runners, and compatibility result writers. Format versions, descriptions, paths, and sentinels
stay in this layer rather than becoming fields of computational configuration:

```toml
[dependencies]
aquacrop-core = { version = "0.1", features = ["file-io"] }
```

`aquacrop_core::io::run_project` executes one project and returns its results.
`run_project_with_outputs` additionally writes requested reports, while `run_project_directory`
loads `LIST/ListProjects.txt` or discovers `.PRO`/`.PRM` projects and runs the directory in the same
order as the compatibility CLI.

The runners orchestrate file resolution, simulation execution, and result writing. Climate
preparation, report derivation, aggregation, and evaluation statistics are implemented in
filesystem-independent core modules rather than in `io`.

`SimulationResult::daily_reports`, `SimulationResult::seasonal_report`, and
`SimulationResult::evaluate` expose the values used by compatibility reports without enabling
`file-io`. Daily engine results also retain compartment state, balance residuals, cutting records,
and other values beyond the currently supported AquaCrop text reports.

Result reports are streamed through buffered file writers. The currently vendored official
testcase verifies seasonal, daily, harvest, evaluation, and status output. Aggregated climate
expansion, external initial-condition files, and dedicated intermediate/irrigation reports remain
outside the implemented file-compatibility subset.

The supported AquaCrop version and upstream revision are determined by the version of the reference implementation in `third-party/KUL-RSDA/AquaCrop` and are exposed as `AQUACROP_COMPATIBLE_VERSION`, `AQUACROP_REFERENCE_TAG`, and `AQUACROP_REFERENCE_COMMIT`.
