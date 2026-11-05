## 3. Architecture of the Reference Implementation

### 3.1 Source Module Map

| Fortran file | Responsibility | Suggested port module |
|---|---|---|
| `aquacrop.F90` | Executable entry point | `cli` |
| `startunit.F90` | Run lifecycle and library interface | `engine` / public API |
| `run.f90` | Project loading, initialization, daily stepping, output | `runner`, `io`, `calendar` |
| `simul.f90` | Water/salt budget, canopy, E, Tr, B, and HI | `processes/*` |
| `global.f90` | Types, global state, constants, calculation functions | `domain`, `parameters`, `math` |
| `rootunit.f90` | Root-growth adjustments | `processes/rooting` |
| `tempprocessing.f90` | Temperature series, GDD, stage conversion | `climate/temperature` |
| `climprocessing.f90` | Climate-input processing | `climate` |
| `preparefertilitysalinity.f90` | Fertility and salinity calibration relationships | `stress/calibration` |
| `defaultcropsoil.f90` | Default values | `defaults` |
| `project_input.f90` | Project records | `io/project` |
| `inforesults.f90` | Result generation | `output` |
| `utils.f90` | Dates, rounding, strings | `compat` |
| `kinds.f90` | Numeric kinds | Target-language numeric types |

The Fortran implementation stores most state globally and mutates it through
`Get*`/`Set*` procedures. A port should preferably separate:

```text
ModelConfig      Immutable soil, crop, management, and algorithm parameters
ClimateInput     Daily or aggregated climate inputs
SimulationState  Mutable state at the current day boundary
DailyFluxes      Fluxes belonging only to the current time step
DailyResult      Snapshot used for output and regression tests
```

Conceptually, the step interface should be:

```text
step(config, climate_day, state) -> (next_state, daily_result)
```

This functional boundary does not change the model, but exposes hidden dependencies
and makes individual processes testable. The process order must still match Section 6.

### 3.2 Principal State Objects

**Soil compartment** (`CompartmentIndividual`):

| Field | Unit | Meaning |
|---|---:|---|
| `Thickness` | m | Compartment thickness |
| `theta` | m³/m³ | Volumetric water content |
| `fluxout` | mm/day | Flux through the lower boundary |
| `Layer` | index | Soil-layer index |
| `Smax` | m³/m³/day | Maximum root extraction rate |
| `FCadj` | vol % | Field capacity adjusted for the water table |
| `DayAnaero` | day | Duration of anaerobic conditions |
| `WFactor` | 0…1 | Fraction of the compartment inside the active depth |
| `SinkMajor` | mm/day | 7.3: locally possible extraction after stresses |
| `SinkMinor` | mm/day | 7.3: target extraction from the root profile |
| `Salt[11]` | g/m² | Dissolved salt by cell |
| `Depo[11]` | g/m² | Deposited salt by cell |

**Soil layer** (`SoilLayerIndividual`) stores `SAT`, `FC`, and `WP` in
volumetric percent; `tau` as the drainage characteristic; `InfRate` in mm/day;
`Penetrability` and `GravelVol` in percent; salt-cell parameters `Macro`,
`SaltMobility[11]`, `SC`, `SCP1`, `UL`, and `Dx`; and capillary-rise parameters
`SoilClass`, `CRa`, and `CRb`.

**Crop** (`rep_Crop`) contains:

- the time mode, thresholds, and shapes of water-stress responses;
- salinity thresholds and the `ECsw` response;
- `SmaxTopQuarter`, `SmaxBotQuarter`, and derived `SmaxTop`, `SmaxBot`;
- `KcTop` and, in 7.3, `KcDeclineCumul`;
- `CCo`, `CCini`, `CCx`, `CGC`, `CDC`, and their GDD equivalents;
- root depths and root-function shape;
- calendar-day and GDD timing for all crop stages;
- `WP`, `WPy`, `HI`, and HI-adjustment parameters;
- in 7.3, `PrematureEnd` and `LastDayNr`.

**Simulation state** (`rep_sim`) contains the initial `theta/ECe`, germination
delay, accumulated GDD, evaporation-stage state, current stress effects,
anaerobiosis, perennial assimilate storage, and, in 7.3,
`DayNrPrematureEnd`.

Do not serialize Fortran derived types byte-for-byte. Their layout depends on the
compiler ABI, and some fields are derived values. Define a versioned schema for the
port instead.
