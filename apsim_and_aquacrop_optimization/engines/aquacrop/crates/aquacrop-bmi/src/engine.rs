//! BMI lifecycle, override bookkeeping, and variable dispatch.
//!
//! [`BmiModel`] wraps [`aquacrop_core::engine::Simulation`] and reproduces the reference BMI
//! contract: canonical names and aliases, units,
//! grids, mutability, and the persistent-override semantics of the weather/infiltration inputs.
//! It touches no filesystem, network, or temporary directory at runtime; [`crate::config`] parses
//! everything from in-memory strings.

use aquacrop_core::engine::{DailyResult, Groundwater, Simulation};

use crate::config::{self, ConfigSnapshot};
use crate::error::BmiError;
use crate::registry;

const TIME_UNITS: &str = "days";

/// Persistent weather overrides, mirroring the reference's `BMI_has_*_override` bookkeeping
/// (`bmi_aquacrop.f90:963-1005`). Each is applied to every subsequent day's climate record until
/// `bmi__clear_weather_overrides` is invoked with a nonzero value, exactly like the reference.
#[derive(Debug, Default)]
struct WeatherOverrides {
    rain: Option<f64>,
    minimum_temperature: Option<f64>,
    maximum_temperature: Option<f64>,
    reference_et: Option<f64>,
}

/// Season-to-date running-mean stress accumulators, replicating the reference's `StressTot.Sto`,
/// `StressTot.Temp`, and `StressTot.Salt` incremental-mean formula
/// (`run.f90:7050-7057,7078-7093,7285-7299`) from Rust's already-computed per-day coefficients.
/// There is no equivalent accumulator in `aquacrop-core`; this state exists only in the wrapper.
#[derive(Debug, Default)]
struct StressAccumulators {
    /// `StressTot.NrD` equivalent: `None` until the first day with canopy cover, matching the
    /// reference's `undef_int` sentinel before `SetStressTot_NrD(1)` fires.
    day_count: Option<i32>,
    water_mean: f64,
    temperature_mean: f64,
    salinity_mean: f64,
}

impl StressAccumulators {
    /// Advances all three running means by one day, gated on `canopy_cover > 0` exactly as the
    /// reference gates its `NrD` increment (`run.f90:7051`). `stomatal_stress` may be `None` on a
    /// day with no meaningful potential transpiration, matching the reference's own
    /// `StressStomata > -0.000001` guard by simply skipping the water-stress fold-in that day
    /// while still advancing temperature/salinity, since the reference's temperature/salinity
    /// updates are not re-gated by the water-stress guard.
    /// for the exact citations and the documented canopy-zero-but-rooting-positive edge case this
    /// simplifies).
    fn advance(
        &mut self,
        canopy_cover: f64,
        stomatal_stress_percent: Option<f64>,
        temperature_stress_coefficient: f64,
        salinity_stress_coefficient: f64,
    ) {
        if canopy_cover <= 0.0 {
            return;
        }
        let day_count = match self.day_count {
            None => {
                self.day_count = Some(1);
                1
            }
            Some(previous) => {
                let next = previous + 1;
                self.day_count = Some(next);
                next
            }
        };
        let denominator = f64::from(day_count);
        let previous_weight = f64::from(day_count - 1);
        if let Some(today) = stomatal_stress_percent {
            self.water_mean = previous_weight.mul_add(self.water_mean, today) / denominator;
        }
        let today_temperature = 100.0 * (1.0 - temperature_stress_coefficient);
        self.temperature_mean =
            previous_weight.mul_add(self.temperature_mean, today_temperature) / denominator;
        let today_salinity = 100.0 * (1.0 - salinity_stress_coefficient);
        self.salinity_mean =
            previous_weight.mul_add(self.salinity_mean, today_salinity) / denominator;
    }
}

/// Wrapper-side cumulative transpiration/evapotranspiration, computed as a plain ungated running
/// sum of the daily values `aquacrop-core` already reports, avoiding the inconsistent gating
/// between core's own unexposed `cumulative_transpiration` and exposed `cumulative_crop_evaporation`
/// accumulators.
#[derive(Debug, Default)]
struct CumulativeWaterUse {
    transpiration: f64,
    evapotranspiration: f64,
}

impl CumulativeWaterUse {
    fn advance(&mut self, daily: &DailyResult) {
        self.transpiration += daily.transpiration;
        self.evapotranspiration += daily.transpiration + daily.evaporation;
    }
}

struct RunState {
    simulation: Simulation,
    current_time: f64,
    /// The simulation's first absolute AquaCrop day number. Safe to cache: unlike
    /// `Simulation::last_day_number`, nothing in `aquacrop-core` ever changes this after
    /// construction. Combined with the live `last_day_number()` in [`BmiModel::end_time`].
    first_day_number: i32,
    snapshot: ConfigSnapshot,
    weather_overrides: WeatherOverrides,
    /// Persisted infiltration override cleared by `bmi__clear_weather_overrides`.
    infiltration_override: Option<f64>,
    /// CO2 has no override-flag in the reference (direct, permanent `SetCO2i` write); mirrored
    /// here as a plain optional override reapplied every day once set.
    co2_override: Option<f64>,
    groundwater_depth: Option<f64>,
    groundwater_ec: Option<f64>,
    /// Values set through `set_value`. They do not affect the running `aquacrop-core` simulation
    /// after `initialize()`.
    fertility_stress: i32,
    mulch_cover: i32,
    bund_height: f64,
    irrigation_method: Option<f64>,
    stress: StressAccumulators,
    cumulative_water_use: CumulativeWaterUse,
    last_result: Option<DailyResult>,
}

enum Phase {
    NotInitialized,
    Running(Box<RunState>),
    Finalized,
}

/// A BMI-conformant AquaCrop model instance, independent of any language binding.
///
/// See the module documentation for scope. One `BmiModel` corresponds to one independent
/// simulation instance; multiple instances share no mutable state (each owns its own
/// `Simulation`, override bookkeeping, and accumulators).
pub struct BmiModel {
    phase: Phase,
}

impl Default for BmiModel {
    fn default() -> Self {
        Self::new()
    }
}

impl BmiModel {
    /// Creates an uninitialized model, matching the reference's pre-`initialize()` state.
    #[must_use]
    pub const fn new() -> Self {
        Self {
            phase: Phase::NotInitialized,
        }
    }

    /// Component name, matching the reference's `component_name` (`bmi_aquacrop.f90:150`).
    #[must_use]
    pub const fn component_name() -> &'static str {
        "AquaCrop"
    }

    /// Builds a `SimulationConfig` from `scenario_json` (see [`crate::config::ScenarioConfig`])
    /// and starts the simulation. Fails with [`BmiError::AlreadyInitialized`] if already
    /// initialized. This wrapper rejects repeated initialization.
    ///
    /// # Errors
    ///
    /// Returns [`BmiError::AlreadyInitialized`], or errors from [`crate::config::build_from_json`]
    /// or `aquacrop-core`'s own validation.
    pub fn initialize(&mut self, scenario_json: &str) -> Result<(), BmiError> {
        if !matches!(self.phase, Phase::NotInitialized) {
            return Err(BmiError::AlreadyInitialized);
        }
        let (simulation_config, initial, snapshot) = config::build_from_json(scenario_json)?;
        // `first_day_number` is fixed after construction.
        let first_day_number = simulation_config.simulation_start.day_number();
        let fertility_stress = simulation_config.management.fertility_stress;
        let mulch_cover = i32::from(simulation_config.management.mulch_cover);
        let bund_height = simulation_config.management.bund_height;
        let irrigation_method = snapshot.irrigation_method_ordinal;
        let simulation = simulation_config.prepare()?.initialize(initial)?;
        self.phase = Phase::Running(Box::new(RunState {
            simulation,
            current_time: 0.0,
            first_day_number,
            snapshot,
            weather_overrides: WeatherOverrides::default(),
            infiltration_override: None,
            co2_override: None,
            groundwater_depth: None,
            groundwater_ec: None,
            fertility_stress,
            mulch_cover,
            bund_height,
            irrigation_method,
            stress: StressAccumulators::default(),
            cumulative_water_use: CumulativeWaterUse::default(),
            last_result: None,
        }));
        Ok(())
    }

    fn running(&self) -> Result<&RunState, BmiError> {
        match &self.phase {
            Phase::Running(state) => Ok(state),
            Phase::NotInitialized => Err(BmiError::NotInitialized),
            Phase::Finalized => Err(BmiError::AlreadyFinalized),
        }
    }

    fn running_mut(&mut self) -> Result<&mut RunState, BmiError> {
        match &mut self.phase {
            Phase::Running(state) => Ok(state),
            Phase::NotInitialized => Err(BmiError::NotInitialized),
            Phase::Finalized => Err(BmiError::AlreadyFinalized),
        }
    }

    /// Advances the simulation by one day, applying any active persistent overrides to the day's
    /// climate record first (mirroring the reference's `ReadClimateNextDay` re-application,
    /// `run.f90:7402-7415`).
    ///
    /// Matching the reference exactly: calling `update()` once the run has logically ended
    /// (`day_number > last_day_number`) does not error and still advances `current_time`.
    ///
    /// # Errors
    ///
    /// Returns [`BmiError::NotInitialized`]/[`BmiError::AlreadyFinalized`] outside the running
    /// phase, or [`BmiError::Simulation`] if `aquacrop-core` itself reports an error.
    pub fn update(&mut self) -> Result<(), BmiError> {
        let state = self.running_mut()?;
        if let Some(climate) = state.simulation.next_day_climate_mut() {
            if let Some(value) = state.weather_overrides.rain {
                climate.weather.rain = value;
            }
            if let Some(value) = state.weather_overrides.minimum_temperature {
                climate.weather.minimum_temperature = value;
            }
            if let Some(value) = state.weather_overrides.maximum_temperature {
                climate.weather.maximum_temperature = value;
            }
            if let Some(value) = state.weather_overrides.reference_et {
                climate.weather.reference_et = value;
            }
            if let Some(value) = state.co2_override {
                climate.weather.carbon_dioxide = value;
            }
            if let Some(depth) = state.groundwater_depth {
                climate.groundwater = Some(Groundwater {
                    depth,
                    conductivity: state.groundwater_ec.unwrap_or(0.0),
                });
            }
            climate.infiltration_override = state.infiltration_override;
        }
        if let Some(daily) = state.simulation.step()? {
            state.stress.advance(
                daily.canopy_cover,
                daily.stomatal_stress,
                daily.temperature_stress_coefficient,
                daily
                    .root_zone_salinity
                    .as_ref()
                    .map_or(1.0, |salinity| salinity.stress_coefficient),
            );
            state.cumulative_water_use.advance(&daily);
            state.last_result = Some(daily);
        }
        state.current_time += 1.0;
        Ok(())
    }

    /// Calls [`Self::update`] until `current_time >= time`, matching the reference's
    /// `aquacrop_update_until` (`bmi_aquacrop.f90:365-376`), including its no-op behavior when
    /// `time` is already at or before the current time.
    ///
    /// # Errors
    ///
    /// See [`Self::update`].
    pub fn update_until(&mut self, time: f64) -> Result<(), BmiError> {
        while self.running()?.current_time < time {
            self.update()?;
        }
        Ok(())
    }

    /// Finalizes the model. Matching the reference, this always succeeds once initialized; unlike
    /// the reference, a second `finalize()` call is rejected rather than silently re-running
    /// cleanup on no state (see [`Self::initialize`]'s note on disclosed lifecycle corrections).
    ///
    /// # Errors
    ///
    /// Returns [`BmiError::NotInitialized`]/[`BmiError::AlreadyFinalized`] outside the running
    /// phase.
    pub fn finalize(&mut self) -> Result<(), BmiError> {
        match std::mem::replace(&mut self.phase, Phase::Finalized) {
            Phase::Running(state) => {
                let _ = state.simulation.finalize();
                Ok(())
            }
            Phase::NotInitialized => {
                self.phase = Phase::NotInitialized;
                Err(BmiError::NotInitialized)
            }
            Phase::Finalized => {
                self.phase = Phase::Finalized;
                Err(BmiError::AlreadyFinalized)
            }
        }
    }

    /// Start time, always `0.0`, matching the reference (`bmi_aquacrop.f90:451`).
    #[must_use]
    pub const fn start_time() -> f64 {
        0.0
    }

    /// Actual simulation duration in days.
    ///
    /// The value is read from the engine because initialization and delayed germination can change
    /// the final day.
    ///
    /// # Errors
    ///
    /// Returns [`BmiError::NotInitialized`]/[`BmiError::AlreadyFinalized`] outside the running
    /// phase.
    pub fn end_time(&self) -> Result<f64, BmiError> {
        let state = self.running()?;
        Ok(f64::from(
            state.simulation.last_day_number() - state.first_day_number,
        ))
    }

    /// Current time in days since [`Self::start_time`].
    ///
    /// # Errors
    ///
    /// Returns [`BmiError::NotInitialized`]/[`BmiError::AlreadyFinalized`] outside the running
    /// phase.
    pub fn current_time(&self) -> Result<f64, BmiError> {
        Ok(self.running()?.current_time)
    }

    /// Fixed daily time step, always `1.0`, matching the reference (`bmi_aquacrop.f90:312,484`).
    #[must_use]
    pub const fn time_step() -> f64 {
        1.0
    }

    /// Time units, always `"days"`, matching the reference (`bmi_aquacrop.f90:495`).
    #[must_use]
    pub const fn time_units() -> &'static str {
        TIME_UNITS
    }

    /// Canonical input variable names, in the reference's declared order.
    #[must_use]
    pub const fn input_var_names() -> &'static [&'static str] {
        &registry::INPUT_NAMES
    }

    /// Canonical output variable names, in the reference's declared order.
    #[must_use]
    pub const fn output_var_names() -> &'static [&'static str] {
        &registry::OUTPUT_NAMES
    }

    /// Resolves `name` (alias or canonical) to its static [`registry::VariableMeta`].
    ///
    /// # Errors
    ///
    /// Returns [`BmiError::UnknownVariable`] if `name` does not resolve to any canonical
    /// variable, matching the reference's `case default -> BMI_FAILURE`.
    pub fn variable(name: &str) -> Result<&'static registry::VariableMeta, BmiError> {
        let canonical = registry::resolve_alias(name);
        registry::metadata(canonical).ok_or_else(|| BmiError::UnknownVariable(name.to_owned()))
    }

    /// Current grid size for `name`'s grid (1 for scalars; the soil-layer or compartment count for
    /// grids 1/2), dynamically re-queried exactly like the reference's `get_grid_size`
    /// (`bmi_aquacrop.f90:1143-1162`, which calls `GetSoil_NrSoilLayers()`/`GetNrCompartments()`
    /// live rather than caching a size at initialize time).
    ///
    /// # Errors
    ///
    /// Returns [`BmiError::UnknownVariable`] for an unresolvable name, plus the lifecycle errors
    /// from [`Self::running`].
    pub fn grid_size(&self, name: &str) -> Result<usize, BmiError> {
        let meta = Self::variable(name)?;
        self.grid_size_for_grid_id(meta.grid)
    }

    /// Current size of grid `grid` (0 = scalar, 1 = soil layers, 2 = compartments), matching the
    /// reference's `get_grid_size(grid: int)` (`bmi_aquacrop.f90:1143-1162`), which is keyed by
    /// grid id directly rather than by variable name. Multiple variables can share one grid.
    ///
    /// # Errors
    ///
    /// Returns the lifecycle errors from [`Self::running`].
    pub fn grid_size_for_grid_id(&self, grid: u8) -> Result<usize, BmiError> {
        let state = self.running()?;
        Ok(match grid {
            1 => state.snapshot.soil_layer_thicknesses.len(),
            2 => state
                .last_result
                .as_ref()
                .map_or(state.snapshot.initial_compartment_count, |daily| {
                    daily.compartments.len()
                }),
            _ => 1,
        })
    }
}

include!("engine_values.rs");
