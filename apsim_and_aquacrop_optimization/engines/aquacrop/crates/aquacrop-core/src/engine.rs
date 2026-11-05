//! Explicit point-simulation lifecycle and daily process orchestration.

use std::sync::Arc;

use crate::Error;
use crate::balance::{
    DailyBalanceFluxes, DailyBalanceSnapshot, begin_daily_balance, end_daily_balance,
};
use crate::biomass::{
    BiomassInputs, BiomassState, DailyWaterProductivity, SalinityCalibration,
    SalinityCanopyEffects, advance_biomass, calibrate_salinity_stress,
    prepared_seasonal_potential_transpiration_sum, salinity_canopy_effects,
};
use crate::calendar::{CalendarDate, premature_end_day_number};
use crate::canopy::{
    CanopyTiming, FertilityCanopyTiming, adjusted_canopy_day_with_timing,
    adjusted_canopy_gdd_with_timing, canopy_at_time, derive_canopy_timing, effective_cycle_length,
    fertility_adjusted_potential_canopy_cover,
    fertility_adjusted_potential_canopy_cover_with_timing, fertility_canopy_timing_with_limits,
    perennial_initial_cover_multiplier, perennial_maximum_cover_multiplier,
    perennial_weed_adjustment, potential_canopy_cover_with_timing,
    potential_canopy_cover_with_timing_and_limits, relative_weed_cover, weed_canopy_multiplier,
};
use crate::climate::{
    CalibrationClimate, DailyWeather, GrowingDegreeDayMethod, ReferenceTemperature,
    growing_degree_days,
};
use crate::compat::{roundc_i8, roundc_i32};
use crate::crop::{
    CropKind, CropTemperature, DepletionAdjustment, DevelopmentCalendar, PlantingMethod,
    PreparedCrop, SeasonCrop,
};
use crate::cutting::{
    CuttingInputs, CuttingIntervalState, advance_cutting_interval, fixed_cutting_due,
    generated_cutting_due, record_cutting,
};
use crate::evaporation::{SoilEvaporation, SoilEvaporationState, evaporate_soil};
use crate::harvest::{
    DailyHarvestIndex, HarvestIndexState, PostYieldStressInputs, PostYieldStressState,
    actual_yield_formation_switch, advance_post_yield_stress, dry_yield, flowering_fraction,
    harvest_index_rate, pollination_stress_coefficient, post_yield_harvest_index_multiplier,
    pre_yield_biomass_range, pre_yield_harvest_index_multiplier, reference_harvest_index_for_day,
    total_harvest_index_multiplier, unrestricted_yield_formation_switch,
};
use crate::irrigation::{
    DailyIrrigation, IrrigationDepthCriterion, IrrigationMode, IrrigationRecord,
    IrrigationSchedule, IrrigationTimeCriterion,
};
use crate::management::Management;
use crate::parameters::SimulationParameters;
use crate::perennial::{AssimilateTransferInputs, AssimilateTransferState, transfer_assimilates};
use crate::rooting::{RootingResult, RootingStress, RootingTime, advance_rooting_depth};
use crate::salinity::{
    GroundwaterInflow, RootZoneSalinity, SalinityResponse, SaltTransport, SaltTransportParameters,
    adjust_stomatal_for_soil_water_salinity, concentrate_salts, determine_root_zone_salinity,
    field_capacity_water_conductivity, horizontal_groundwater_inflow,
    initialize_groundwater_profile, salinity_stress_coefficient, saturated_extract_conductivity,
    soil_water_conductivity, total_salt, transport_salts,
};
use crate::soil::{PreparedSoilProfile, SoilCompartment, SoilCompartmentState, SoilProfile};
use crate::stress::{
    DepletionThresholds, RootZoneWater, adjust_depletion_threshold_for_eto,
    advance_local_anaerobic_stress, advance_root_zone_anaerobic_stress, determine_root_zone_water,
    germination_is_possible, local_root_water_stress, stress_coefficient,
};
use crate::transpiration::{
    ExtractionGravelMode, RootExtraction, RootExtractionParameters, extract_root_water,
};
use crate::water_balance::{
    CapillaryRise, InfiltrationParameters, InfiltrationState, RainfallAggregation,
    adjust_field_capacity_for_groundwater, apply_capillary_rise, corrected_curve_number,
    curve_number_for_wetness, drain_profile, infiltrate, relative_wetness_topsoil, runoff,
};
use crate::{
    et::PotentialEt, et::PotentialEtParameters, et::potential_et,
    et::temperature_stress_coefficient,
};

const INITIAL_ABSTRACTION: i8 = 5;

fn final_canopy_growth_gdd(crop: &PreparedCrop, timing: &CanopyTiming) -> i32 {
    let calendar = crop.cycle.driving_calendar();
    if crop.parameters.kind != CropKind::Grain
        || !crop.parameters.development.determinacy_linked_to_flowering
    {
        return calendar.senescence;
    }
    let flowering_midpoint =
        calendar.flowering + roundc_i32(f64::from(calendar.flowering_length) / 2.0);
    if crop.parameters.planting_method != PlantingMethod::Regrowth {
        return flowering_midpoint.min(calendar.senescence);
    }

    let adjustment = timing
        .gdd_to_initial_cover
        .unwrap_or(timing.gdd_to_full_canopy - calendar.emergence);
    let remaining = calendar.senescence - timing.gdd_to_full_canopy;
    let fraction = f64::from(remaining) / f64::from(adjustment + calendar.emergence + remaining);
    (timing.gdd_to_full_canopy
        + roundc_i32(
            fraction
                * f64::from(
                    flowering_midpoint + adjustment + calendar.emergence
                        - timing.gdd_to_full_canopy,
                ),
        ))
    .min(calendar.senescence)
}

/// Returns the final canopy-growth day for calendar-day crops.
fn final_canopy_growth_days(crop: &PreparedCrop, timing: &CanopyTiming) -> i32 {
    let calendar = crop.cycle.calendar_days();
    if crop.parameters.kind != CropKind::Grain
        || !crop.parameters.development.determinacy_linked_to_flowering
    {
        return calendar.senescence;
    }
    let flowering_midpoint =
        calendar.flowering + roundc_i32(f64::from(calendar.flowering_length) / 2.0);
    if crop.parameters.planting_method != PlantingMethod::Regrowth {
        return flowering_midpoint.min(calendar.senescence);
    }

    let adjustment = timing
        .days_to_initial_cover
        .unwrap_or(timing.days_to_full_canopy - calendar.emergence);
    let remaining = calendar.senescence - timing.days_to_full_canopy;
    let fraction = f64::from(remaining) / f64::from(adjustment + calendar.emergence + remaining);
    (timing.days_to_full_canopy
        + roundc_i32(
            fraction
                * f64::from(
                    flowering_midpoint + adjustment + calendar.emergence
                        - timing.days_to_full_canopy,
                ),
        ))
    .min(calendar.senescence)
}

fn required_gdd(
    canopy_cover: f64,
    initial_cover: f64,
    maximum_cover: f64,
    adjusted_growth_per_gdd: f64,
    previous_sum_gdd: f64,
) -> f64 {
    if adjusted_growth_per_gdd <= crate::compat::ZERO_THRESHOLD || previous_sum_gdd <= 0.0 {
        return -1.0;
    }
    let growth = if canopy_cover <= maximum_cover / 2.0 {
        (canopy_cover / initial_cover).ln() / previous_sum_gdd
    } else {
        ((0.25 * maximum_cover * maximum_cover / initial_cover) / (maximum_cover - canopy_cover))
            .ln()
            / previous_sum_gdd
    };
    previous_sum_gdd * growth / adjusted_growth_per_gdd
}

fn canopy_at_gdd(
    elapsed_gdd: f64,
    initial_cover: f64,
    growth_per_gdd: f64,
    maximum_cover: f64,
) -> f64 {
    canopy_at_time(elapsed_gdd, initial_cover, growth_per_gdd, maximum_cover)
}

/// Returns late-season canopy cover from the achieved peak.
fn senescence_decline_from_achieved(
    achieved_cover: f64,
    maximum_cover: f64,
    decline_coefficient: f64,
    elapsed_since_senescence: f64,
) -> f64 {
    if achieved_cover < 0.001 {
        return 0.0;
    }
    let adjusted_decline = decline_coefficient * (achieved_cover + 2.29) / (maximum_cover + 2.29);
    (achieved_cover
        * (1.0
            - 0.05
                * (((elapsed_since_senescence * 3.33 * adjusted_decline)
                    / (achieved_cover + 2.29))
                    .exp()
                    - 1.0)))
        .max(0.0)
}

fn adjusted_senescence_threshold(
    crop: &PreparedCrop,
    parameters: &SimulationParameters,
    reference_et: f64,
    early_senescence_time: f64,
    with_beta: bool,
) -> f64 {
    let mut threshold = crop.parameters.water_stress.senescence_upper;
    if crop.parameters.depletion_adjustment == DepletionAdjustment::Fao {
        threshold += parameters.depletion_et_adjustment_factor
            * 0.04
            * (5.0 - reference_et)
            * (10.0 - 9.0 * crop.parameters.water_stress.senescence_upper).log10();
        if with_beta && early_senescence_time > 0.0001 {
            threshold *= 1.0 - f64::from(parameters.early_senescence_threshold_reduction) / 100.0;
        }
        if threshold < 0.0 {
            threshold = 0.0;
        } else if threshold >= 1.0 {
            threshold = 0.98;
        }
    }
    threshold
}

/// Groundwater depth and quality for one simulation day.
#[derive(Clone, Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct Groundwater {
    /// Depth below the soil surface, in metres.
    pub depth: f64,
    /// Electrical conductivity, in dS/m.
    pub conductivity: f64,
}

/// A boundary of a simulation or crop period.
///
/// [`Self::Date`] is a validated AquaCrop calendar date. [`Self::Day`] is an
/// absolute, one-based AquaCrop day number whose epoch is 1 January 1901.
///
/// With the `serde` feature, boundaries are represented as calendar-date
/// strings. Deserialization therefore creates [`Self::Date`], including when
/// the value was originally constructed as [`Self::Day`].
#[derive(Clone, Copy, Debug)]
pub enum SimulationBoundary {
    /// A validated AquaCrop calendar date.
    Date(CalendarDate),
    /// An absolute, one-based AquaCrop day number.
    Day(i32),
}

impl From<CalendarDate> for SimulationBoundary {
    fn from(value: CalendarDate) -> Self {
        Self::Date(value)
    }
}

impl From<i32> for SimulationBoundary {
    fn from(value: i32) -> Self {
        Self::Day(value)
    }
}

#[cfg(feature = "serde")]
impl serde::Serialize for SimulationBoundary {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: serde::Serializer,
    {
        use serde::ser::Error;

        let date = (*self).calendar_date().map_err(S::Error::custom)?;
        date.serialize(serializer)
    }
}

#[cfg(feature = "serde")]
impl<'de> serde::Deserialize<'de> for SimulationBoundary {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        CalendarDate::deserialize(deserializer).map(Self::Date)
    }
}

impl SimulationBoundary {
    /// Returns the absolute, one-based AquaCrop day number.
    #[must_use]
    pub fn day_number(self) -> i32 {
        match self {
            Self::Date(date) => date.day_number(),
            Self::Day(day) => day,
        }
    }

    /// Returns the boundary as a validated AquaCrop calendar date.
    ///
    /// # Errors
    ///
    /// Returns [`Error`] when a [`Self::Day`] value is before the AquaCrop
    /// calendar epoch.
    pub fn calendar_date(self) -> Result<CalendarDate, Error> {
        match self {
            Self::Date(date) => Ok(date),
            Self::Day(day) => CalendarDate::from_day_number(day),
        }
    }
}

/// User-supplied inputs for one point simulation.
///
/// Each period boundary accepts either a [`CalendarDate`] or an absolute,
/// one-based AquaCrop day number through [`SimulationBoundary`]. With the
/// `serde` feature, the public wire format uses calendar-date strings.
#[derive(Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct SimulationConfig {
    /// One-based cultivation-season count used by perennial carry-over rules.
    pub season_year: i32,
    /// Crop with complete calendars selected for this season.
    pub crop: SeasonCrop,
    /// Soil profile.
    pub soil: SoilProfile,
    /// Field-management configuration.
    pub management: Management,
    /// Irrigation schedule owned by the simulation, or `None` when irrigation is absent.
    pub irrigation: Option<IrrigationSchedule>,
    /// Program-level numerical controls.
    pub parameters: SimulationParameters,
    /// Consecutive daily weather values beginning on the first simulation day.
    pub weather: Vec<DailyWeather>,
    /// Externally supplied daily irrigation aligned with `weather`, or `None` when absent.
    ///
    /// A present series must contain exactly one value for every weather day.
    pub external_irrigation: Option<Vec<DailyIrrigation>>,
    /// Consecutive groundwater values aligned with `weather`, or `None` when absent.
    ///
    /// A present series must contain exactly one value for every weather day.
    pub groundwater: Option<Vec<Groundwater>>,
    /// Reference climate used only by calibrated fertility and salinity calculations.
    pub calibration_climate: Option<CalibrationClimate>,
    /// First simulation day.
    pub simulation_start: SimulationBoundary,
    /// Last simulation day, inclusive.
    pub simulation_stop: SimulationBoundary,
    /// First crop-cycle day.
    pub crop_start: SimulationBoundary,
    /// Last crop-cycle day, inclusive.
    pub crop_stop: SimulationBoundary,
}

#[derive(Clone, Debug)]
struct PreparedInputs {
    season_year: i32,
    soil: PreparedSoilProfile,
    management: Management,
    irrigation: Option<IrrigationSchedule>,
    parameters: SimulationParameters,
    daily_inputs: Vec<PreparedDailyInput>,
    calibration_climate: Option<CalibrationClimate>,
}

#[derive(Clone, Debug)]
struct RunConfig {
    crop: PreparedCrop,
    first_day_number: i32,
    last_day_number: i32,
    first_crop_day_number: i32,
    last_crop_day_number: i32,
    weed_cover_delta: i32,
    adjusted_days_to_full_canopy: Option<i32>,
}

/// Validated simulation inputs that can start one or more independent runs.
///
/// Cloning this value explicitly retains the prepared shared inputs while
/// allowing the clone to be consumed by another [`Simulation`]. The shared
/// inputs are reference counted internally; each run receives independent crop
/// timing and mutable simulation state.
#[derive(Clone, Debug)]
pub struct PreparedSimulation {
    inputs: Arc<PreparedInputs>,
    run: RunConfig,
}

/// One prepared day's forcing, aligned to [`SimulationConfig::weather`]'s day range.
///
/// Exposed (via [`Simulation::next_day_climate_mut`]) so a caller can override the day about to
/// be consumed by [`Simulation::step`]. For example, a BMI-style interface injecting live
/// forcing mid-simulation rather than supplying the complete series upfront.
#[derive(Clone, Debug)]
pub struct PreparedDailyInput {
    /// Absolute AquaCrop day number.
    pub day_number: i32,
    /// Weather forcing for this day.
    pub weather: DailyWeather,
    /// Externally supplied irrigation for this day, when configured.
    pub external_irrigation: Option<DailyIrrigation>,
    /// Groundwater table state for this day, when configured.
    pub groundwater: Option<Groundwater>,
    /// External infiltration override, in millimetres, or `None` for the model's own
    /// rainfall/irrigation partitioning. When set, replaces the day's entire infiltration input
    /// (rain, irrigation, and surface storage alike) exactly as the reference's
    /// `BMI_has_Infil_override` does: the curve-number runoff calculation and irrigation
    /// triggering are both skipped, initial runoff for the day is forced to zero, and this value
    /// alone is handed to the same infiltration-storage routine used every other day. Any of the
    /// day's actual rain or irrigation that isn't captured by this value is not separately
    /// accounted for -- this is a replacement, not an addition, matching the reference exactly.
    pub infiltration_override: Option<f64>,
}

impl SimulationConfig {
    /// Validates and prepares inputs for one or more independent simulations.
    ///
    /// The returned [`PreparedSimulation`] is consumed to start a run. Clone it
    /// explicitly before starting every additional run.
    ///
    /// # Errors
    ///
    /// Returns [`Error`] when crop, soil, period, climate, or derived values
    /// cannot form a valid simulation.
    pub fn prepare(self) -> Result<PreparedSimulation, Error> {
        let Self {
            season_year,
            crop,
            soil,
            management,
            irrigation,
            parameters,
            weather,
            external_irrigation,
            groundwater,
            calibration_climate,
            simulation_start,
            simulation_stop,
            crop_start,
            crop_stop,
        } = self;

        let first_day_number = simulation_start.day_number();

        let daily_inputs =
            prepare_daily_inputs(first_day_number, weather, external_irrigation, groundwater)?;

        let inputs = PreparedInputs {
            season_year,
            soil: soil.prepare()?,
            management,
            irrigation,
            parameters,
            daily_inputs,
            calibration_climate,
        };

        let mut run = RunConfig {
            crop: crop.prepare()?,
            first_day_number,
            last_day_number: simulation_stop.day_number(),
            first_crop_day_number: crop_start.day_number(),
            last_crop_day_number: crop_stop.day_number(),
            weed_cover_delta: inputs.management.weed_cover_delta,
            adjusted_days_to_full_canopy: None,
        };

        adjust_premature_end(&mut run)?;
        validate_config(&inputs, &run)?;
        adjust_perennial_for_season(&inputs, &mut run);
        adjust_crop_period(&inputs, &mut run)?;
        run.adjusted_days_to_full_canopy = adjust_gdd_crop_calendar(&inputs, &mut run)?;

        Ok(PreparedSimulation {
            inputs: inputs.into(),
            run,
        })
    }
}

impl PreparedSimulation {
    /// Initializes one independent simulation from these prepared inputs.
    ///
    /// Consume this value for the final run, or call [`Clone::clone`] explicitly
    /// before each additional run.
    ///
    /// # Errors
    ///
    /// Returns [`Error`] when the supplied initial state cannot form a valid
    /// simulation with these prepared inputs.
    #[expect(
        clippy::too_many_lines,
        reason = "initialization keeps the reference model's state preparation order visible"
    )]
    pub fn initialize(self, initial: InitialConditions) -> Result<Simulation, Error> {
        let PreparedSimulation { inputs, mut run } = self;

        let adjusted_days_to_full_canopy = run.adjusted_days_to_full_canopy.take();
        if !initial.surface_water.is_finite() || initial.surface_water < 0.0 {
            return Err(Error::InvalidSimulationConfiguration(
                "initial surface water must be finite and non-negative",
            ));
        }
        if !initial.surface_conductivity.is_finite() || initial.surface_conductivity < 0.0 {
            return Err(Error::InvalidSimulationConfiguration(
                "initial surface-water conductivity must be finite and non-negative",
            ));
        }
        if !initial.stored_assimilates.is_finite() || initial.stored_assimilates < 0.0 {
            return Err(Error::InvalidSimulationConfiguration(
                "initial stored assimilates must be finite and non-negative",
            ));
        }
        let compartments = if let Some(compartments) = initial.compartments {
            compartments
                .into_iter()
                .map(|compartment| compartment.into_working(&inputs.soil))
                .collect::<Result<Vec<_>, _>>()?
        } else {
            // AquaCrop starts with twelve 0.10 m compartments and only grows the grid.
            let mut compartments = inputs
                .soil
                .reference_compartments_at_field_capacity_for_depth(
                    run.crop.parameters.rooting.maximum_depth,
                );
            // Matching the reference's `ResetSWCToFC`: a fresh at-field-capacity profile is
            // wetted up to the groundwater-adjusted field capacity on the first simulated day,
            // not just the plain layer field capacity, whenever a shallow water table is
            // already present at season start.
            if let Some(depth) = inputs
                .daily_inputs
                .first()
                .and_then(|daily| daily.groundwater.as_ref())
                .map(|groundwater| groundwater.depth)
            {
                adjust_field_capacity_for_groundwater(&inputs.soil, &mut compartments, depth)?;
                for compartment in &mut compartments {
                    compartment.theta = compartment.adjusted_field_capacity / 100.0;
                }
            }
            compartments
        };
        if compartments.is_empty() {
            return Err(Error::InvalidSimulationConfiguration(
                "initial soil compartments cannot be empty",
            ));
        }
        let mut canopy_timing = derive_canopy_timing(&run.crop);
        if let Some(days) = adjusted_days_to_full_canopy {
            canopy_timing.days_to_full_canopy = days;
        }
        let effective_cycle_length_value =
            effective_cycle_length(&run.crop, canopy_timing.days_to_full_canopy);
        let germinated = run.crop.parameters.planting_method != PlantingMethod::Seed
            || run.first_day_number > run.first_crop_day_number;
        let protected_seedling = false;
        let harvest_index = HarvestIndexState::new(&run.crop);
        let fertility_effects =
            FertilityEffects::try_new(&inputs, &run, canopy_timing.days_to_full_canopy)?;
        let salinity_effects = SalinityEffects::try_new(&inputs, &run, &compartments)?;
        let unrestricted_initial_canopy_cover = run.crop.parameters.canopy.initial_seedling_cover;
        let unrestricted_maximum_canopy_cover = run.crop.parameters.canopy.maximum_cover;
        let (initial_regrowth_canopy_cover, weed_relative_cover) =
            apply_perennial_and_weed_cover(&inputs, &mut run, &canopy_timing, &fertility_effects);
        let adjusted_initial_canopy_cover = run.crop.parameters.canopy.initial_seedling_cover;
        let adjusted_maximum_canopy_cover = run.crop.parameters.canopy.maximum_cover;
        let cutting_record_index = first_cutting_record_index(&inputs, &run);
        let assimilate_transfer = AssimilateTransferState::for_linked_season(
            &run.crop.parameters.assimilate_transfer,
            inputs.season_year,
            initial.stored_assimilates,
        );
        Ok(Simulation {
            inputs,
            run,
            canopy_timing,
            effective_cycle_length: effective_cycle_length_value,
            state: SimulationState {
                daily_input_index: 0,
                previous_sum_gdd: 0.0,
                sum_gdd: 0.0,
                germinated,
                delayed_days: 0,
                root_depth: None,
                root_density_correction: 1.0,
                frozen_water_stress_thresholds: None,
                potential_canopy_cover: 0.0,
                canopy_cover: 0.0,
                previous_reported_canopy_cover: 0.0,
                withered_canopy_cover: 0.0,
                unrestricted_withered_canopy_cover: 0.0,
                unrestricted_initial_canopy_cover,
                unrestricted_maximum_canopy_cover,
                adjusted_initial_canopy_cover,
                initial_regrowth_canopy_cover,
                adjusted_maximum_canopy_cover,
                protected_seedling,
                weed_relative_cover,
                early_senescence_time: 0.0,
                early_senescence_peak_cover: 0.0,
                cumulative_senescence_reference_et: 0.0,
                evaporation_limit_enabled: false,
                previous_potential_transpiration: 0.0,
                previous_potential_evaporation: 0.0,
                previous_transpiration: 0.0,
                cumulative_potential_transpiration: 0.0,
                cumulative_transpiration: 0.0,
                cumulative_crop_evaporation: 0.0,
                irrigation_interval: 1,
                surface_water: initial.surface_water,
                surface_conductivity: initial.surface_conductivity,
                evaporation: SoilEvaporationState::without_previous_day(),
                biomass: BiomassState::new(),
                harvest_index,
                pre_yield_harvest_index_multiplier: None,
                post_yield_stress: PostYieldStressState::new(),
                pollination_harvest_index: 0.0,
                anaerobic_exposure: 0,
                submerged_exposure: 0,
                anaerobic_stress_coefficient: 1.0,
                leaf_expansion_stress: None,
                senescence_stress: None,
                fertility_effects,
                salinity_effects,
                assimilate_transfer,
                cutting: CuttingIntervalState::new(),
                cutting_record_index,
                dry_yield: 0.0,
                compartments,
            },
        })
    }
}

/// Soil and surface-water state supplied at initialization.
#[derive(Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct InitialConditions {
    /// Explicit compartments, or `None` to initialize the crop-sized profile at field capacity.
    pub compartments: Option<Vec<SoilCompartmentState>>,
    /// Water initially retained on the soil surface, in millimetres.
    pub surface_water: f64,
    /// Electrical conductivity of initially retained surface water, in dS/m.
    pub surface_conductivity: f64,
    /// Assimilate biomass stored by the immediately preceding linked season, in tonnes per hectare.
    pub stored_assimilates: f64,
}

impl InitialConditions {
    /// Selects field capacity throughout the crop-sized compartment profile.
    #[must_use]
    pub const fn at_field_capacity() -> Self {
        Self {
            compartments: None,
            surface_water: 0.0,
            surface_conductivity: 0.0,
            stored_assimilates: 0.0,
        }
    }
}

impl Default for InitialConditions {
    fn default() -> Self {
        Self::at_field_capacity()
    }
}

/// Crop-development stage reported after a completed daily step.
#[derive(Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "snake_case"))]
pub enum CropStage {
    /// Day lies before or after the active crop cycle.
    OutsideCropCycle,
    /// Sown crop is germinating or a transplant is recovering.
    Establishment,
    /// Vegetative crop development.
    Vegetative,
    /// Grain-crop flowering.
    Flowering,
    /// Grain or tuber yield formation.
    YieldFormation,
    /// Crop-cycle day has no remaining green canopy.
    NoGrowth,
}

/// End-of-day state for one soil compartment.
#[derive(Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct DailyCompartmentResult {
    /// Compartment thickness, in metres.
    pub thickness: f64,
    /// Volumetric water content as an m3/m3 fraction.
    pub theta: f64,
    /// Electrical conductivity of the saturated-paste extract, in dS/m.
    pub extract_conductivity: f64,
    /// Drainage through the lower compartment boundary, in millimetres.
    pub drainage: f64,
    /// Field capacity adjusted for a water table, in volume percent.
    pub adjusted_field_capacity: f64,
    /// Zero-based index of the soil layer this compartment belongs to.
    ///
    /// Exposed so a caller can aggregate compartment state per soil layer (for example, a
    /// BMI-style interface reporting per-layer water content) without duplicating the
    /// depth-boundary bookkeeping `compartments_at_field_capacity_for_depth` already performs.
    pub layer_index: usize,
}

/// Production recorded when a forage cutting occurs on a simulation day.
#[derive(Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct CuttingResult {
    /// One-based cutting number in the current linked simulation sequence.
    pub number: i32,
    /// Calendar days accumulated since the preceding cutting record.
    pub interval: i32,
    /// Dry biomass produced since the preceding cutting, in tonnes per hectare.
    pub biomass: f64,
    /// Cumulative dry biomass at this cutting, in tonnes per hectare.
    pub cumulative_biomass: f64,
    /// Dry yield produced since the preceding cutting, in tonnes per hectare.
    pub dry_yield: f64,
    /// Cumulative dry yield at this cutting, in tonnes per hectare.
    pub cumulative_dry_yield: f64,
}

/// Result emitted after exactly one daily model step.
#[derive(Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct DailyResult {
    /// Absolute AquaCrop day number.
    pub day_number: i32,
    /// Day after planting, starting at one on the crop's first day, or `None` outside the cycle.
    pub day_after_planting: Option<i32>,
    /// Whether germination has occurred by the end of this day.
    pub germinated: bool,
    /// Crop-development stage at day end.
    pub stage: CropStage,
    /// Crop-development delay accumulated before germination, in calendar days.
    pub delayed_days: i32,
    /// Growing degree-days accumulated on this day.
    pub growing_degree_days: f64,
    /// Reference evapotranspiration for this day, in millimetres.
    pub reference_et: f64,
    /// Growing degree-days accumulated in the crop cycle.
    pub cumulative_growing_degree_days: f64,
    /// Actual rooting depth, in metres.
    pub rooting_depth: f64,
    /// Green canopy cover as a soil-cover fraction.
    pub canopy_cover: f64,
    /// Green canopy attributable to the crop rather than weeds, as a soil-cover fraction.
    pub crop_canopy_cover: f64,
    /// Relative weed cover within the combined canopy, in percent.
    pub weed_cover: f64,
    /// Profile water content at day end, in millimetres.
    pub profile_water: f64,
    /// Water retained on the soil surface at day end, in millimetres.
    pub surface_water: f64,
    /// Rainfall, in millimetres.
    pub rain: f64,
    /// Irrigation, in millimetres.
    pub irrigation: f64,
    /// Water entering the profile from the surface, in millimetres.
    pub infiltration: f64,
    /// Surface runoff, in millimetres.
    pub runoff: f64,
    /// Bottom drainage, in millimetres.
    pub drainage: f64,
    /// Water entering by capillary rise, in millimetres.
    pub capillary_rise: f64,
    /// Horizontal water entering from a water table inside the profile, in millimetres.
    pub horizontal_water: f64,
    /// Potential soil evaporation, in millimetres.
    pub potential_evaporation: f64,
    /// Actual soil evaporation, in millimetres.
    pub evaporation: f64,
    /// Potential crop transpiration, in millimetres.
    pub potential_transpiration: f64,
    /// Actual crop transpiration, in millimetres.
    pub transpiration: f64,
    /// Daily stomatal/transpiration stress, in percent.
    ///
    /// This is `100 * (1 - actual transpiration / potential transpiration)`, where zero
    /// means no transpiration restriction and 100 means complete suppression. It is
    /// undefined when potential transpiration is effectively zero. Because actual
    /// transpiration can already reflect multiple interacting limitations, this is not
    /// a pure soil-water stress coefficient.
    pub stomatal_stress: Option<f64>,
    /// Actual transpiration attributable to the crop rather than weeds, in millimetres.
    pub crop_transpiration: f64,
    /// Cumulative soil evaporation during the active crop cycle, in millimetres.
    pub cumulative_crop_evaporation: f64,
    /// Salt entering with infiltrated irrigation and surface storage, in grams per square metre.
    pub infiltrated_salt: f64,
    /// Salt leaving in bottom drainage, in grams per square metre.
    pub drained_salt: f64,
    /// Salt entering by capillary rise, in grams per square metre.
    pub capillary_salt: f64,
    /// Salt stored in the soil profile at day end, in grams per square metre.
    pub profile_salt: f64,
    /// Water stores and crop-depletion thresholds in the active root zone.
    pub root_zone_water: Option<RootZoneWater>,
    /// Water stores and crop-depletion thresholds at maximum rooting depth.
    pub maximum_root_zone_water: Option<RootZoneWater>,
    /// End-of-day water and salinity state for every soil compartment.
    pub compartments: Vec<DailyCompartmentResult>,
    /// Root-zone conductivity and salinity stress for the day.
    pub root_zone_salinity: Option<RootZoneSalinity>,
    /// Root-zone deficient-aeration coefficient as a dimensionless fraction.
    pub anaerobic_stress_coefficient: f64,
    /// Consecutive calendar days the root zone has been continuously anaerobic, capped at
    /// [`crate::parameters::SimulationParameters::deficient_aeration_delay`] and reset to zero on
    /// any day the root zone is not anaerobic.
    pub root_zone_anaerobic_days: i32,
    /// Temperature limitation of transpiration as a dimensionless coefficient.
    pub temperature_stress_coefficient: f64,
    /// Canopy leaf-expansion stress, in percent, when evaluated on this day.
    pub leaf_expansion_stress: Option<f64>,
    /// Canopy senescence stress, in percent, when evaluated on this day.
    pub senescence_stress: Option<f64>,
    /// Daily water productivity, in grams per square metre.
    pub water_productivity: f64,
    /// Cumulative dry biomass, in tonnes per hectare.
    pub biomass: f64,
    /// Cumulative dry biomass without fertility, salinity, or weed stress, in tonnes per hectare.
    pub unrestricted_biomass: f64,
    /// Actual dry-yield-to-biomass ratio, in percent, when yield formation has begun.
    pub harvest_index: Option<f64>,
    /// Cumulative dry yield, in tonnes per hectare.
    pub dry_yield: f64,
    /// Assimilate biomass mobilized into above-ground biomass today, in tonnes per hectare.
    pub mobilized_assimilates: f64,
    /// Assimilate biomass stored in roots today, in tonnes per hectare.
    pub stored_assimilates: f64,
    /// Forage cutting recorded after today's production update, when applicable.
    pub cutting: Option<CuttingResult>,
    /// Residual daily water-balance error, in millimetres.
    pub water_balance_error: f64,
    /// Residual daily salt-balance error, in grams per square metre.
    pub salt_balance_error: f64,
    /// Minimum air temperature, in degrees Celsius.
    pub minimum_temperature: f64,
    /// Maximum air temperature, in degrees Celsius.
    pub maximum_temperature: f64,
    /// Atmospheric carbon-dioxide concentration, in parts per million.
    pub carbon_dioxide: f64,
    /// Groundwater depth and conductivity for the day, when present.
    pub groundwater: Option<Groundwater>,
}

#[derive(Debug)]
struct SimulationState {
    daily_input_index: usize,
    previous_sum_gdd: f64,
    sum_gdd: f64,
    germinated: bool,
    delayed_days: i32,
    root_depth: Option<f64>,
    root_density_correction: f64,
    frozen_water_stress_thresholds: Option<(f64, f64)>,
    potential_canopy_cover: f64,
    canopy_cover: f64,
    previous_reported_canopy_cover: f64,
    withered_canopy_cover: f64,
    unrestricted_withered_canopy_cover: f64,
    unrestricted_initial_canopy_cover: f64,
    unrestricted_maximum_canopy_cover: f64,
    adjusted_initial_canopy_cover: f64,
    initial_regrowth_canopy_cover: f64,
    adjusted_maximum_canopy_cover: f64,
    protected_seedling: bool,
    weed_relative_cover: f64,
    early_senescence_time: f64,
    early_senescence_peak_cover: f64,
    cumulative_senescence_reference_et: f64,
    evaporation_limit_enabled: bool,
    previous_potential_transpiration: f64,
    previous_potential_evaporation: f64,
    previous_transpiration: f64,
    cumulative_potential_transpiration: f64,
    cumulative_transpiration: f64,
    cumulative_crop_evaporation: f64,
    irrigation_interval: i32,
    surface_water: f64,
    surface_conductivity: f64,
    evaporation: SoilEvaporationState,
    biomass: BiomassState,
    harvest_index: HarvestIndexState,
    pre_yield_harvest_index_multiplier: Option<f64>,
    post_yield_stress: PostYieldStressState,
    pollination_harvest_index: f64,
    anaerobic_exposure: i32,
    submerged_exposure: i32,
    anaerobic_stress_coefficient: f64,
    leaf_expansion_stress: Option<f64>,
    senescence_stress: Option<f64>,
    fertility_effects: FertilityEffects,
    salinity_effects: SalinityEffects,
    assimilate_transfer: AssimilateTransferState,
    cutting: CuttingIntervalState,
    cutting_record_index: usize,
    dry_yield: f64,
    compartments: Vec<SoilCompartment>,
}

/// Stateful point simulation that advances one day per [`Simulation::step`] call.
#[derive(Debug)]
pub struct Simulation {
    inputs: Arc<PreparedInputs>,
    run: RunConfig,
    canopy_timing: CanopyTiming,
    /// The crop cycle's true effective length in calendar days, per the reference's own
    /// `DetermineLengthGrowthStages`-derived cutoff. See [`crop_stage`](Self::crop_stage) and
    /// `crate::canopy::effective_cycle_length`. Distinct from (and, for some crops, shorter or
    /// longer than) `config.crop.development.days_to_maturity`, which several *other* cutoffs
    /// (potential ET's own validity window, among others) correctly use directly, matching the
    /// reference's own use of the raw field there.
    effective_cycle_length: i32,
    state: SimulationState,
}

#[derive(Debug)]
struct FertilityEffects {
    canopy_timing: FertilityCanopyTiming,
    gdd_to_full_canopy: i32,
    water_productivity_reduction: i8,
    canopy_decline: f64,
    calibration: Option<crate::biomass::FertilityCalibration>,
    current_stress: i32,
    previous_stress: i32,
    seasonal_potential_transpiration: f64,
    normalized_potential_transpiration: f64,
    reference_initial_canopy_cover: f64,
    reference_maximum_canopy_cover: f64,
}

#[derive(Debug)]
struct SalinityEffects {
    considered: bool,
    calibration: Option<SalinityCalibration>,
    average_stress: f64,
    pending_average_stress: f64,
    stress_observation_count: i32,
    growth_reduction: i8,
    maximum_cover_reduction: i8,
    stomatal_reduction: i8,
    canopy_decline: f64,
    root_zone: Option<RootZoneSalinity>,
}

#[derive(Debug)]
struct IrrigationApplication {
    depth: f64,
    conductivity: f64,
    generated_triggered: bool,
}

impl FertilityEffects {
    fn try_new(
        inputs: &PreparedInputs,
        run: &RunConfig,
        days_to_full_canopy: i32,
    ) -> Result<Self, Error> {
        let crop = &run.crop;
        let stress = inputs.management.fertility_stress;
        let calibration = if crop.parameters.fertility_stress.calibrated && stress > 0 {
            let calibration_climate = inputs.calibration_climate.as_ref().ok_or(
                Error::InvalidSimulationConfiguration(
                    "calibrated fertility stress requires a reference climate",
                ),
            )?;
            Some(crate::biomass::calibrate_fertility_stress(
                crop,
                &inputs.parameters,
                stress,
                run.first_crop_day_number,
                calibration_climate.reference_carbon_dioxide,
                calibration_climate.reference_temperatures.as_ref(),
            )?)
        } else {
            None
        };
        let seasonal_potential_transpiration = if calibration.is_some() {
            let carbon_dioxide = calibration_carbon_dioxide(&inputs.daily_inputs)?;
            let calibration_climate = inputs.calibration_climate.as_ref().ok_or(
                Error::InvalidSimulationConfiguration(
                    "calibrated fertility stress requires a reference climate",
                ),
            )?;
            prepared_seasonal_potential_transpiration_sum(
                crop,
                &inputs.parameters,
                run.first_crop_day_number,
                days_to_full_canopy,
                carbon_dioxide,
                calibration_climate.reference_temperatures.as_ref(),
            )?
        } else {
            0.0
        };
        let normalized_potential_transpiration = calibration.as_ref().map_or(0.0, |_| {
            (1.0 - f64::from(stress) / 100.0) * seasonal_potential_transpiration
        });
        let reference_initial_canopy_cover = crop.parameters.canopy.initial_seedling_cover;
        let reference_maximum_canopy_cover = crop.parameters.canopy.maximum_cover;
        let (canopy_timing, gdd_to_full_canopy, water_productivity_reduction, decline) =
            fertility_effect_values(
                inputs,
                run,
                stress,
                reference_initial_canopy_cover,
                reference_maximum_canopy_cover,
            )?;
        Ok(Self {
            canopy_timing,
            gdd_to_full_canopy,
            water_productivity_reduction,
            canopy_decline: decline,
            calibration,
            current_stress: stress,
            previous_stress: stress,
            seasonal_potential_transpiration,
            normalized_potential_transpiration,
            reference_initial_canopy_cover,
            reference_maximum_canopy_cover,
        })
    }

    fn update_for_stress(
        &mut self,
        inputs: &PreparedInputs,
        run: &RunConfig,
        stress: i32,
        salinity: &SalinityEffects,
    ) -> Result<(), Error> {
        let (canopy_timing, gdd_to_full_canopy, water_productivity_reduction, decline) =
            combined_stress_effect_values(
                inputs,
                run,
                stress,
                self.reference_initial_canopy_cover,
                self.reference_maximum_canopy_cover,
                salinity,
            )?;
        self.canopy_timing = canopy_timing;
        self.gdd_to_full_canopy = gdd_to_full_canopy;
        self.water_productivity_reduction = water_productivity_reduction;
        self.canopy_decline = decline;
        self.current_stress = stress;
        Ok(())
    }
}

impl SalinityEffects {
    const fn inactive() -> Self {
        Self {
            considered: false,
            calibration: None,
            average_stress: 0.0,
            pending_average_stress: 0.0,
            stress_observation_count: 0,
            growth_reduction: 0,
            maximum_cover_reduction: 0,
            stomatal_reduction: 0,
            canopy_decline: 0.0,
            root_zone: None,
        }
    }

    fn try_new(
        inputs: &PreparedInputs,
        run: &RunConfig,
        compartments: &[SoilCompartment],
    ) -> Result<Self, Error> {
        let response = &run.crop.parameters.salinity_stress;
        let thresholds_are_valid = matches!(
            (response.ece_minimum, response.ece_maximum),
            (Some(minimum), Some(maximum)) if minimum < maximum
        );
        let profile_contains_salt = compartments.iter().enumerate().try_fold(
            false,
            |contains_salt, (index, compartment)| {
                let layer = inputs.soil.layers.get(compartment.layer_index).ok_or(
                    crate::water_balance::WaterBalanceError::MissingLayer {
                        compartment: index,
                        layer: compartment.layer_index,
                    },
                )?;
                Ok::<_, Error>(contains_salt || total_salt(compartment, layer) > 0.0)
            },
        )?;
        let daily_inputs_can_add_salt = inputs.daily_inputs.iter().any(|input| {
            input
                .external_irrigation
                .as_ref()
                .is_some_and(|irrigation| irrigation.depth > 0.0 && irrigation.conductivity > 0.0)
                || input
                    .groundwater
                    .as_ref()
                    .is_some_and(|groundwater| groundwater.conductivity > 0.0)
        });
        let active = thresholds_are_valid && (profile_contains_salt || daily_inputs_can_add_salt);
        let calibration = if active {
            let calibration_climate = inputs.calibration_climate.as_ref().ok_or(
                Error::InvalidSimulationConfiguration(
                    "salinity stress requires a reference climate",
                ),
            )?;
            let temperatures: Vec<_> = inputs
                .daily_inputs
                .iter()
                .map(|input| ReferenceTemperature {
                    minimum: input.weather.minimum_temperature,
                    maximum: input.weather.maximum_temperature,
                })
                .collect();
            Some(calibrate_salinity_stress(
                &run.crop,
                &inputs.parameters,
                run.first_crop_day_number,
                run.first_day_number,
                &temperatures,
                calibration_climate.reference_carbon_dioxide,
                calibration_climate.reference_temperatures.as_ref(),
            )?)
        } else {
            None
        };
        Ok(Self {
            considered: thresholds_are_valid,
            calibration,
            ..Self::inactive()
        })
    }

    fn set_effects(&mut self, effects: &SalinityCanopyEffects) {
        self.growth_reduction = effects.growth_reduction;
        self.maximum_cover_reduction = effects.maximum_cover_reduction;
        self.stomatal_reduction = effects.stomatal_reduction;
        self.canopy_decline = effects.canopy_decline;
    }

    fn clear_effects(&mut self) {
        self.growth_reduction = 0;
        self.maximum_cover_reduction = 0;
        self.stomatal_reduction = 0;
        self.canopy_decline = 0.0;
    }
}

fn fertility_effect_values(
    inputs: &PreparedInputs,
    run: &RunConfig,
    stress_level: i32,
    reference_initial_canopy_cover: f64,
    reference_maximum_canopy_cover: f64,
) -> Result<(FertilityCanopyTiming, i32, i8, f64), Error> {
    combined_stress_effect_values(
        inputs,
        run,
        stress_level,
        reference_initial_canopy_cover,
        reference_maximum_canopy_cover,
        &SalinityEffects::inactive(),
    )
}

fn combined_stress_effect_values(
    inputs: &PreparedInputs,
    run: &RunConfig,
    stress_level: i32,
    reference_initial_canopy_cover: f64,
    reference_maximum_canopy_cover: f64,
    salinity: &SalinityEffects,
) -> Result<(FertilityCanopyTiming, i32, i8, f64), Error> {
    let crop = &run.crop;
    let stress_fraction = f64::from(stress_level) / 100.0;
    let reduction =
        |shape| roundc_i8(100.0 * (1.0 - stress_coefficient(stress_fraction, 0.0, 1.0, shape)));
    let canopy_timing = fertility_canopy_timing_with_limits(
        crop,
        reduction(crop.parameters.fertility_stress.canopy_growth_shape)
            .max(salinity.growth_reduction),
        reduction(crop.parameters.fertility_stress.maximum_canopy_shape)
            .max(salinity.maximum_cover_reduction),
        stress_level,
        reference_initial_canopy_cover,
        reference_maximum_canopy_cover,
    );
    let gdd_to_full_canopy = if crop.cycle.uses_growing_degree_days()
        && (inputs.management.fertility_stress != 0 || salinity.average_stress > f64::EPSILON)
    {
        cumulative_gdd_for_calendar_days(inputs, run, canopy_timing.days_to_full_canopy)?
    } else {
        derive_canopy_timing(crop).gdd_to_full_canopy
    };
    Ok((
        canopy_timing,
        gdd_to_full_canopy,
        reduction(crop.parameters.fertility_stress.water_productivity_shape),
        (1.0 - stress_coefficient(
            stress_fraction,
            0.0,
            1.0,
            crop.parameters.fertility_stress.canopy_decline_shape,
        ))
        .max(salinity.canopy_decline),
    ))
}

/// Final scalar results after the configured simulation period ends.
#[derive(Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct FinalResult {
    /// Last processed absolute day, or `None` when no day was processed.
    pub last_day_number: Option<i32>,
    /// Final cumulative dry biomass, in tonnes per hectare.
    pub biomass: f64,
    /// Final cumulative dry biomass without fertility, salinity, or weed stress,
    /// in tonnes per hectare.
    pub unrestricted_biomass: f64,
    /// Final dry yield, in tonnes per hectare.
    pub dry_yield: f64,
    /// Assimilate biomass stored during this season, in tonnes per hectare.
    pub stored_assimilates: f64,
}

/// Internal model state that can seed a linked simulation.
#[derive(Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct SimulationCheckpoint {
    /// Final soil-compartment state.
    pub compartments: Vec<SoilCompartmentState>,
    /// Water retained on the soil surface, in millimetres.
    pub surface_water: f64,
    /// Electrical conductivity of retained surface water, in dS/m.
    pub surface_conductivity: f64,
    /// Assimilate biomass stored during this season, in tonnes per hectare.
    pub stored_assimilates: f64,
}

/// User-facing results and separate continuation state returned by finalization.
#[derive(Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct CompletedSimulation {
    /// Final scalar simulation results.
    pub result: FinalResult,
    /// State reusable by a linked simulation.
    pub checkpoint: SimulationCheckpoint,
}

impl Simulation {
    /// Advances exactly one configured day and emits its result.
    ///
    /// `None` means that the configured simulation period has ended.
    ///
    /// # Errors
    ///
    /// Returns [`Error`] when a numerical process receives invalid state.
    #[expect(
        clippy::too_many_lines,
        reason = "keeping the reference model's daily process order visible is intentional"
    )]
    pub fn step(&mut self) -> Result<Option<DailyResult>, Error> {
        let Some(input) = self.inputs.daily_inputs.get(self.state.daily_input_index) else {
            return Ok(None);
        };
        let day_number = input.day_number;
        let minimum_temperature = input.weather.minimum_temperature;
        let maximum_temperature = input.weather.maximum_temperature;
        let reference_et = input.weather.reference_et;
        let rain = input.weather.rain;
        let carbon_dioxide = input.weather.carbon_dioxide;
        let (external_irrigation, external_irrigation_conductivity) = input
            .external_irrigation
            .as_ref()
            .map_or((0.0, 0.0), |irrigation| {
                (irrigation.depth, irrigation.conductivity)
            });
        let groundwater = input
            .groundwater
            .as_ref()
            .map(|groundwater| (groundwater.depth, groundwater.conductivity));
        let infiltration_override = input.infiltration_override;
        if day_number > self.run.last_day_number {
            return Ok(None);
        }
        self.initialize_groundwater(groundwater)?;
        let calendar_day_after_planting = day_number - self.run.first_crop_day_number + 1;
        let daily_gdd = self.advance_gdd(
            calendar_day_after_planting,
            minimum_temperature,
            maximum_temperature,
        );
        let mut rooting = self.advance_rooting(calendar_day_after_planting, daily_gdd);
        if let Some((depth, _)) = groundwater
            && depth >= 0.0
            && rooting.depth > depth
            && self.run.crop.parameters.water_stress.anaerobic_point_volume > 0
        {
            rooting.depth = depth.max(self.run.crop.parameters.rooting.minimum_depth);
        }
        let (beginning, mut infiltration, salt, capillary_rise, irrigation_application) = self
            .advance_infiltration(
                day_number,
                &rooting,
                rain,
                external_irrigation,
                external_irrigation_conductivity,
                groundwater,
                infiltration_override,
            )?;
        let mut irrigation = irrigation_application.depth;
        self.advance_germination(day_number)?;
        let day_after_planting =
            day_number - self.run.first_crop_day_number + 1 - self.state.delayed_days;
        self.advance_salinity_stress(day_after_planting, rooting.depth)?;
        self.state.leaf_expansion_stress = None;
        self.state.senescence_stress = None;
        self.advance_canopy(day_after_planting, daily_gdd, rooting.depth, reference_et)?;
        self.advance_early_senescence(day_after_planting, daily_gdd, rooting.depth, reference_et)?;
        let potential =
            self.daily_potential_et(day_after_planting, reference_et, carbon_dioxide, daily_gdd)?;
        let (evaporation, extraction) = self.evaporate_and_transpire(
            &rooting,
            &potential,
            reference_et,
            rain,
            irrigation,
            &mut infiltration,
        )?;
        let stomatal_stress =
            daily_stomatal_stress(extraction.actual_transpiration, potential.transpiration);
        if potential.transpiration > crate::compat::ZERO_THRESHOLD {
            irrigation += self.apply_net_irrigation(rooting.depth)?;
        }
        self.apply_transpiration_canopy_feedback(&extraction);
        let horizontal_inflow = self.advance_horizontal_groundwater_inflow(groundwater)?;
        concentrate_salts(
            &self.inputs.soil,
            &mut self.state.compartments,
            f64::from(self.inputs.parameters.salt_solubility),
        )?;
        self.commit_daily_salinity(rooting.depth)?;
        let balance = end_daily_balance(
            &self.inputs.soil,
            &self.state.compartments,
            &beginning,
            &DailyBalanceFluxes {
                rain,
                irrigation,
                drainage: infiltration.deep_drainage,
                runoff: infiltration.runoff,
                evaporation: evaporation.actual,
                transpiration: extraction.actual_transpiration,
                capillary_rise: capillary_rise.water,
                horizontal_water: horizontal_inflow.water,
                ending_surface_water: infiltration.surface_storage,
                infiltrated_salt: salt.infiltrated_salt,
                drained_salt: salt.drained_salt,
                capillary_salt: capillary_rise.salt,
                horizontal_salt: horizontal_inflow.salt,
            },
        )?;
        let unrestricted = self.daily_unrestricted_transpiration(
            day_after_planting,
            reference_et,
            carbon_dioxide,
            daily_gdd,
        )?;
        self.advance_cutting_interval(daily_gdd);
        let cutting_today = self.cutting_due(day_number, day_after_planting, daily_gdd);
        let daily_harvest_index = reference_harvest_index_for_day(
            &self.run.crop,
            &mut self.state.harvest_index,
            day_after_planting - 1,
            0,
            self.state.canopy_cover,
            self.state.withered_canopy_cover,
            self.inputs.parameters.minimum_canopy_for_harvest_index,
        );
        let biomass_before_yield_step = self.state.biomass.actual;
        let crop_transpiration =
            self.crop_transpiration(day_after_planting, extraction.actual_transpiration);
        let crop_extraction = RootExtraction {
            actual_transpiration: crop_transpiration,
            unmet_transpiration: extraction.unmet_transpiration,
        };
        let (productivity, assimilate_flux) = self.advance_biomass(
            day_after_planting,
            reference_et,
            carbon_dioxide,
            unrestricted,
            &daily_harvest_index,
            &crop_extraction,
        );
        let daily_biomass = self.state.biomass.actual - biomass_before_yield_step;
        let harvest_index = self.advance_yield(
            day_after_planting,
            daily_biomass,
            rooting.depth,
            reference_et,
            (minimum_temperature, maximum_temperature),
            &daily_harvest_index,
        )?;
        self.update_dynamic_fertility(day_after_planting)?;
        let reported_canopy_cover = self.state.canopy_cover;
        let adjusted_day_number = day_number - self.state.delayed_days;
        if adjusted_day_number >= self.run.first_crop_day_number
            && adjusted_day_number <= self.run.last_crop_day_number
            && (self.state.biomass.actual <= 0.0 || reported_canopy_cover > 0.0)
        {
            self.state.cumulative_crop_evaporation += evaporation.actual;
        }
        let (crop_canopy_cover, weed_cover) =
            self.crop_and_weed_cover(day_after_planting, reported_canopy_cover);
        // Growth-stage classification uses the previous day's canopy cover.
        let stage = self.crop_stage(day_number, self.state.previous_reported_canopy_cover);
        let root_zone_water =
            self.output_root_zone_water(day_after_planting, rooting.depth, reference_et)?;
        let maximum_root_zone_water = if root_zone_water.is_some() {
            self.output_root_zone_water(
                day_after_planting,
                self.run.crop.parameters.rooting.maximum_depth,
                reference_et,
            )?
        } else {
            None
        };
        let compartments = self.daily_compartment_results()?;
        let temperature_stress_coefficient =
            if reported_canopy_cover <= crate::compat::ZERO_THRESHOLD {
                1.0
            } else {
                temperature_stress_coefficient(
                    Some(0.0),
                    self.run.crop.parameters.temperature.full_transpiration_gdd,
                    daily_gdd,
                )
            };
        let cutting = self.advance_cutting(day_after_planting, daily_gdd, cutting_today);
        self.state.previous_reported_canopy_cover = reported_canopy_cover;
        if irrigation_application.generated_triggered {
            self.state.irrigation_interval = 0;
        }
        self.finish_daily_state(day_after_planting, &rooting, &potential, &extraction);
        let infiltrated_water = infiltration.infiltrated_rain
            + infiltration.infiltrated_irrigation
            + infiltration.infiltrated_storage;
        Ok(Some(DailyResult {
            day_number,
            day_after_planting: if day_number < self.run.first_crop_day_number
                || day_number > self.run.last_crop_day_number
            {
                None
            } else {
                Some(day_after_planting)
            },
            germinated: self.state.germinated,
            stage,
            delayed_days: self.state.delayed_days,
            growing_degree_days: daily_gdd,
            reference_et,
            cumulative_growing_degree_days: self.state.sum_gdd,
            rooting_depth: rooting.depth,
            canopy_cover: reported_canopy_cover,
            crop_canopy_cover,
            weed_cover,
            profile_water: balance.ending_profile_water,
            surface_water: infiltration.surface_storage,
            rain,
            irrigation,
            infiltration: infiltrated_water,
            runoff: infiltration.runoff,
            drainage: infiltration.deep_drainage,
            capillary_rise: capillary_rise.water,
            horizontal_water: horizontal_inflow.water,
            potential_evaporation: potential.soil_evaporation,
            evaporation: evaporation.actual,
            potential_transpiration: potential.transpiration,
            transpiration: extraction.actual_transpiration,
            stomatal_stress,
            crop_transpiration,
            cumulative_crop_evaporation: self.state.cumulative_crop_evaporation,
            infiltrated_salt: salt.infiltrated_salt,
            drained_salt: salt.drained_salt,
            capillary_salt: capillary_rise.salt,
            profile_salt: balance.ending_profile_salt,
            root_zone_water,
            maximum_root_zone_water,
            compartments,
            root_zone_salinity: self
                .state
                .salinity_effects
                .root_zone
                .as_ref()
                .map(|salinity| RootZoneSalinity {
                    extract_conductivity: salinity.extract_conductivity,
                    water_conductivity: salinity.water_conductivity,
                    field_capacity_conductivity: salinity.field_capacity_conductivity,
                    stress_coefficient: salinity.stress_coefficient,
                }),
            anaerobic_stress_coefficient: self.state.anaerobic_stress_coefficient,
            root_zone_anaerobic_days: self.state.anaerobic_exposure,
            temperature_stress_coefficient,
            leaf_expansion_stress: self.state.leaf_expansion_stress,
            senescence_stress: self.state.senescence_stress,
            water_productivity: if day_number >= self.run.first_crop_day_number
                && day_number <= self.run.last_crop_day_number
                && self.state.biomass.actual > crate::compat::ZERO_THRESHOLD
            {
                productivity.actual * 100.0
            } else {
                0.0
            },
            biomass: self.state.biomass.actual,
            unrestricted_biomass: self.state.biomass.unrestricted,
            harvest_index,
            dry_yield: self.state.dry_yield,
            mobilized_assimilates: assimilate_flux.into_biomass,
            stored_assimilates: assimilate_flux.out_of_biomass,
            cutting,
            water_balance_error: balance.water_error,
            salt_balance_error: balance.salt_error,
            minimum_temperature,
            maximum_temperature,
            carbon_dioxide,
            groundwater: groundwater.map(|(depth, conductivity)| Groundwater {
                depth,
                conductivity,
            }),
        }))
    }

    fn finish_daily_state(
        &mut self,
        day_after_planting: i32,
        rooting: &RootingResult,
        potential: &PotentialEt,
        extraction: &RootExtraction,
    ) {
        self.state.previous_potential_transpiration = potential.transpiration;
        self.state.previous_potential_evaporation = potential.soil_evaporation;
        self.state.previous_transpiration = extraction.actual_transpiration;
        self.state.cumulative_potential_transpiration += potential.transpiration;
        self.state.cumulative_transpiration += extraction.actual_transpiration;
        // Keep the root-depth sentinel until planting.
        if day_after_planting >= 1 {
            self.state.root_depth = Some(rooting.depth);
        }
        self.state.root_density_correction = rooting.density_correction;
        if self.state.daily_input_index == 0
            && self.run.first_day_number == self.run.first_crop_day_number
        {
            self.state.irrigation_interval = 1;
        } else {
            self.state.irrigation_interval += 1;
        }
        self.state.daily_input_index += 1;
    }

    /// Returns a mutable reference to the daily climate record that the next [`Self::step`] call
    /// will consume, or `None` once every record in the configured climate series has been
    /// consumed.
    ///
    /// This exists for callers that supply forcing incrementally (for example, a BMI-style
    /// interface overriding today's rainfall, temperature, reference ET, CO2, irrigation, or
    /// groundwater before advancing the simulation) rather than providing the complete climate
    /// series upfront. A mutation is visible to the very next `step()` call because `step()` reads
    /// the record at the same index fresh each time; the simulation applies no equation changes
    /// beyond using the overridden values as that day's ordinary input. If `step()` itself returns
    /// an error before advancing its internal day counter, the override remains in place at the
    /// same index for a subsequent retry, since nothing here or in `step()` reverts it.
    ///
    /// Returning `Some` here does not by itself mean `step()` will produce another
    /// [`DailyResult`]: `step()` stops advancing once a record's `day_number` exceeds the
    /// configured last simulation day, and from that point on this method keeps returning
    /// the same unconsumed, unadvanced record (`step()` never increments past it), not `None`.
    /// `None` is reserved for the case where the underlying climate vector itself has no more
    /// entries at all, which can be a strictly later point than the run's logical end when the
    /// configured climate series extends past the last simulation day (as it does for a project
    /// sharing one multi-year climate series across several linked runs). Callers that need to
    /// know whether the run has logically ended should track that against the simulation's own
    /// time bookkeeping, not by testing this method for `None`.
    ///
    /// Mutating this record clones the shared prepared-inputs template the first time it's
    /// called on a given [`Simulation`] (`Arc::make_mut`), so an override here never affects a
    /// sibling run cloned from the same [`PreparedSimulation`] template.
    pub fn next_day_climate_mut(&mut self) -> Option<&mut PreparedDailyInput> {
        Arc::make_mut(&mut self.inputs)
            .daily_inputs
            .get_mut(self.state.daily_input_index)
    }

    /// Returns a read-only, always-current view of the soil compartments, in profile order.
    ///
    /// Unlike [`Self::step`]'s [`DailyResult::compartments`] (a snapshot taken once per completed
    /// day), this reflects the live state at the moment of the call — including a write made via
    /// [`Self::compartments_mut`] that hasn't been through a `step()` yet. A BMI-style interface's
    /// compartment/layer-theta getters should read through here rather than caching the last
    /// `DailyResult`, so a `set_value` is visible to an immediately-following `get_value` without
    /// requiring an intervening `update()` first.
    #[must_use]
    pub fn compartments(&self) -> &[SoilCompartment] {
        &self.state.compartments
    }

    /// Returns a mutable view of the current soil compartments, in profile order.
    ///
    /// This exists for callers that need to overwrite compartment water content directly (for
    /// example, a BMI-style interface implementing a per-layer water-content setter) rather than
    /// only reading it back through [`Self::step`]'s [`DailyResult::compartments`]. Mutating a
    /// compartment's `theta` here takes effect starting with the next [`Self::step`] call, which
    /// reads compartment state fresh each time; nothing here recomputes derived quantities
    /// (salinity, drainage, or field-capacity adjustment). These are recalculated by `step()`
    /// itself from whatever `theta` holds when it runs.
    pub fn compartments_mut(&mut self) -> &mut [SoilCompartment] {
        &mut self.state.compartments
    }

    /// Returns the last absolute AquaCrop day number this run will simulate.
    ///
    /// This is **not** necessarily what the caller originally configured: `initialize` and, for a
    /// crop whose germination is delayed past the configured start, `step` itself can shorten this
    /// value (a non-forage crop's declared `premature_end_day`, or a forage crop's growing-period
    /// recomputation after a delayed start both adjust it in place). Callers that need to know how
    /// long the run will actually last — for example, a BMI-style interface's `end_time` — must read
    /// this live rather than caching the value supplied at construction, which can silently
    /// disagree with what `step` actually honors.
    #[must_use]
    pub const fn last_day_number(&self) -> i32 {
        self.run.last_day_number
    }

    /// Consumes the simulation and returns final results plus separate continuation state.
    #[must_use]
    pub fn finalize(self) -> CompletedSimulation {
        let last_day_number = self
            .state
            .daily_input_index
            .checked_sub(1)
            .and_then(|index| self.inputs.daily_inputs.get(index))
            .map(|input| input.day_number);
        let stored_assimilates = self.state.assimilate_transfer.biomass_stored;
        CompletedSimulation {
            result: FinalResult {
                last_day_number,
                biomass: self.state.biomass.actual,
                unrestricted_biomass: self.state.biomass.unrestricted,
                dry_yield: self.state.dry_yield,
                stored_assimilates,
            },
            checkpoint: SimulationCheckpoint {
                compartments: self
                    .state
                    .compartments
                    .into_iter()
                    .map(SoilCompartmentState::from)
                    .collect(),
                surface_water: self.state.surface_water,
                surface_conductivity: self.state.surface_conductivity,
                stored_assimilates,
            },
        }
    }

    fn advance_gdd(
        &mut self,
        day_after_planting: i32,
        minimum_temperature: f64,
        maximum_temperature: f64,
    ) -> f64 {
        let method = self
            .run
            .crop
            .cycle
            .gdd_method()
            .unwrap_or(self.inputs.parameters.gdd_method);
        let daily_gdd = growing_degree_days(
            self.run.crop.parameters.temperature.base,
            self.run.crop.parameters.temperature.upper,
            minimum_temperature,
            maximum_temperature,
            method,
        );
        self.state.previous_sum_gdd = self.state.sum_gdd;
        if day_after_planting >= 1 {
            self.state.sum_gdd += daily_gdd;
        }
        daily_gdd
    }

    fn advance_germination(&mut self, day_number: i32) -> Result<(), Error> {
        if self.state.germinated || day_number < self.run.first_crop_day_number {
            return Ok(());
        }
        let water = determine_root_zone_water(
            &self.inputs.soil,
            &self.state.compartments,
            self.run.crop.parameters.rooting.minimum_depth,
            f64::from(self.inputs.parameters.upper_soil_depth) / 100.0,
            &DepletionThresholds {
                leaf_expansion: 0.0,
                stomatal_closure: 0.0,
                senescence: 0.0,
            },
        )?;
        if !germination_is_possible(
            water.actual,
            water.wilting_point,
            water.field_capacity,
            self.inputs.parameters.germination_water,
        ) {
            self.state.delayed_days = self.state.delayed_days.checked_add(1).ok_or(
                Error::InvalidSimulationConfiguration("germination delay overflows"),
            )?;
            self.state.sum_gdd = 0.0;
            return Ok(());
        }

        self.state.germinated = true;
        self.state.protected_seedling =
            self.run.crop.parameters.planting_method == PlantingMethod::Seed;
        if self.state.delayed_days > 0 {
            self.reset_period_after_germination(day_number)?;
        }
        Ok(())
    }

    fn reset_period_after_germination(&mut self, day_number: i32) -> Result<(), Error> {
        let previous_last_day = self.run.last_day_number;
        self.run.first_crop_day_number = day_number;
        if self.run.crop.parameters.kind == CropKind::Forage {
            adjust_crop_period(&self.inputs, &mut self.run)?;
        } else {
            self.run.last_crop_day_number = day_number
                .checked_add(self.run.crop.cycle.calendar_days().maturity - 1)
                .ok_or(Error::InvalidSimulationConfiguration(
                    "delayed crop period overflows",
                ))?;
        }
        self.run.last_day_number = previous_last_day.max(self.run.last_crop_day_number);
        adjust_premature_end(&mut self.run)?;
        validate_config(&self.inputs, &self.run)?;

        let adjusted_days_to_full_canopy = adjust_gdd_crop_calendar(&self.inputs, &mut self.run)?;
        self.canopy_timing = derive_canopy_timing(&self.run.crop);
        if let Some(days) = adjusted_days_to_full_canopy {
            self.canopy_timing.days_to_full_canopy = days;
        }
        self.effective_cycle_length =
            effective_cycle_length(&self.run.crop, self.canopy_timing.days_to_full_canopy);
        self.state.fertility_effects = FertilityEffects::try_new(
            &self.inputs,
            &self.run,
            self.canopy_timing.days_to_full_canopy,
        )?;
        self.state.salinity_effects =
            SalinityEffects::try_new(&self.inputs, &self.run, &self.state.compartments)?;
        self.state.delayed_days = 0;
        Ok(())
    }

    #[expect(
        clippy::too_many_arguments,
        clippy::too_many_lines,
        reason = "the explicit daily fluxes preserve the generated-irrigation source equation"
    )]
    fn irrigation_for_day(
        &self,
        day_number: i32,
        rooting_depth: f64,
        rain: f64,
        runoff: f64,
        drainage: f64,
        external_depth: f64,
        external_conductivity: f64,
    ) -> Result<IrrigationApplication, Error> {
        let Some(schedule) = self.inputs.irrigation.as_ref() else {
            return Ok(IrrigationApplication {
                depth: external_depth,
                conductivity: external_conductivity,
                generated_triggered: false,
            });
        };
        if day_number < self.run.first_crop_day_number || day_number > self.run.last_crop_day_number
        {
            return Ok(IrrigationApplication {
                depth: 0.0,
                conductivity: 0.0,
                generated_triggered: false,
            });
        }
        match &schedule.mode {
            IrrigationMode::Rainfed | IrrigationMode::NetRequirement { .. } => {
                Ok(IrrigationApplication {
                    depth: 0.0,
                    conductivity: 0.0,
                    generated_triggered: false,
                })
            }
            IrrigationMode::Manual { first_day_number } => {
                let origin = first_day_number.unwrap_or(self.run.first_crop_day_number);
                let schedule_day = day_number - origin + 1;
                let record = schedule.records.iter().find(|record| {
                    matches!(record, IrrigationRecord::Manual { day, .. } if *day == schedule_day)
                });
                let Some(IrrigationRecord::Manual {
                    depth,
                    electrical_conductivity,
                    ..
                }) = record
                else {
                    return Ok(IrrigationApplication {
                        depth: 0.0,
                        conductivity: external_conductivity,
                        generated_triggered: false,
                    });
                };
                Ok(IrrigationApplication {
                    depth: f64::from(*depth),
                    conductivity: electrical_conductivity.unwrap_or(external_conductivity),
                    generated_triggered: false,
                })
            }
            IrrigationMode::Generated {
                time_criterion,
                depth_criterion,
                ..
            } => {
                if !schedule
                    .mode
                    .generated_irrigation_allowed(day_number, self.run.last_crop_day_number)
                {
                    return Ok(IrrigationApplication {
                        depth: 0.0,
                        conductivity: 0.0,
                        generated_triggered: false,
                    });
                }
                let crop_day = day_number - self.run.first_crop_day_number + 1;
                let record = schedule.records.iter().rev().find(|record| {
                    matches!(record, IrrigationRecord::Generated { from_day, .. } if *from_day <= crop_day)
                });
                let Some(IrrigationRecord::Generated {
                    time_parameter,
                    depth_parameter,
                    electrical_conductivity,
                    ..
                }) = record
                else {
                    return Ok(IrrigationApplication {
                        depth: 0.0,
                        conductivity: external_conductivity,
                        generated_triggered: false,
                    });
                };
                let mut target_water = None;
                let triggered = match time_criterion {
                    IrrigationTimeCriterion::FixedInterval => {
                        *time_parameter <= self.state.irrigation_interval
                    }
                    IrrigationTimeCriterion::WaterBetweenBunds => {
                        self.inputs.management.bund_height >= 0.01
                            && *depth_criterion == IrrigationDepthCriterion::FixedDepth
                            && *time_parameter
                                < roundc_i32(1_000.0 * self.inputs.management.bund_height)
                            && *time_parameter >= roundc_i32(self.state.surface_water)
                    }
                    IrrigationTimeCriterion::RootZoneDepletion
                    | IrrigationTimeCriterion::ReadilyAvailableWaterDepletion => {
                        let water = determine_root_zone_water(
                            &self.inputs.soil,
                            &self.state.compartments,
                            rooting_depth.max(self.run.crop.parameters.rooting.minimum_depth),
                            f64::from(self.inputs.parameters.upper_soil_depth) / 100.0,
                            &DepletionThresholds {
                                leaf_expansion: self
                                    .run
                                    .crop
                                    .parameters
                                    .water_stress
                                    .canopy_expansion_upper,
                                stomatal_closure: self
                                    .run
                                    .crop
                                    .parameters
                                    .water_stress
                                    .stomatal_closure_upper,
                                senescence: self.run.crop.parameters.water_stress.senescence_upper,
                            },
                        )?;
                        let projected = water.actual
                            - self.state.previous_potential_evaporation
                            - self.state.previous_potential_transpiration
                            + rain
                            - runoff
                            - drainage;
                        target_water = Some((water.field_capacity, projected));
                        let trigger_depletion = match time_criterion {
                            IrrigationTimeCriterion::RootZoneDepletion => {
                                f64::from(*time_parameter)
                            }
                            IrrigationTimeCriterion::ReadilyAvailableWaterDepletion => {
                                f64::from(*time_parameter) / 100.0
                                    * (water.field_capacity - water.stomatal_threshold)
                            }
                            _ => unreachable!(),
                        };
                        water.field_capacity - projected >= trigger_depletion
                    }
                };
                if triggered
                    && target_water.is_none()
                    && *depth_criterion == IrrigationDepthCriterion::ToFieldCapacity
                {
                    let water = determine_root_zone_water(
                        &self.inputs.soil,
                        &self.state.compartments,
                        rooting_depth.max(self.run.crop.parameters.rooting.minimum_depth),
                        f64::from(self.inputs.parameters.upper_soil_depth) / 100.0,
                        &DepletionThresholds {
                            leaf_expansion: self
                                .run
                                .crop
                                .parameters
                                .water_stress
                                .canopy_expansion_upper,
                            stomatal_closure: self
                                .run
                                .crop
                                .parameters
                                .water_stress
                                .stomatal_closure_upper,
                            senescence: self.run.crop.parameters.water_stress.senescence_upper,
                        },
                    )?;
                    let projected = water.actual
                        - self.state.previous_potential_evaporation
                        - self.state.previous_potential_transpiration
                        + rain
                        - runoff
                        - drainage;
                    target_water = Some((water.field_capacity, projected));
                }
                let depth = if triggered {
                    match depth_criterion {
                        IrrigationDepthCriterion::FixedDepth => {
                            depth_parameter.map_or(0.0, f64::from)
                        }
                        IrrigationDepthCriterion::ToFieldCapacity => {
                            let (field_capacity, projected) = target_water.unwrap_or((0.0, 0.0));
                            (field_capacity - projected + depth_parameter.map_or(0.0, f64::from))
                                .max(0.0)
                        }
                    }
                } else {
                    0.0
                };
                Ok(IrrigationApplication {
                    depth,
                    conductivity: electrical_conductivity.unwrap_or(external_conductivity),
                    generated_triggered: triggered,
                })
            }
        }
    }

    #[expect(
        clippy::too_many_arguments,
        reason = "the process inputs are passed separately by the daily simulation step"
    )]
    #[expect(
        clippy::too_many_lines,
        reason = "the ordered infiltration and salt-transport calculation is kept together"
    )]
    fn advance_infiltration(
        &mut self,
        day_number: i32,
        rooting: &RootingResult,
        rain: f64,
        external_irrigation: f64,
        external_irrigation_conductivity: f64,
        groundwater: Option<(f64, f64)>,
        infiltration_override: Option<f64>,
    ) -> Result<
        (
            DailyBalanceSnapshot,
            InfiltrationState,
            SaltTransport,
            CapillaryRise,
            IrrigationApplication,
        ),
        Error,
    > {
        let beginning = begin_daily_balance(
            &self.inputs.soil,
            &mut self.state.compartments,
            self.state.surface_water,
        )?;
        adjust_field_capacity_for_groundwater(
            &self.inputs.soil,
            &mut self.state.compartments,
            groundwater.map_or(-1.0, |(depth, _)| depth),
        )?;
        let redistribution_drainage =
            drain_profile(&self.inputs.soil, &mut self.state.compartments)?;
        // When an infiltration override is active, this replaces the day's entire infiltration
        // input -- matching the reference's `BMI_has_Infil_override` branch (`simul.f90`): the
        // curve-number runoff calculation and irrigation triggering are both skipped entirely
        // (`SetRunoff(0.0); InfiltratedRain = override; InfiltratedIrrigation = 0.0;
        // InfiltratedStorage = 0.0`), and the same `infiltrate` routine used every other day is
        // then handed only the override value. Any bunded surface water already on hand from a
        // previous day is left untouched (not drawn down, not lost) rather than being spent as an
        // infiltration input, matching the reference not reading `GetSurfaceStorage()` inside
        // `calculate_infiltration` itself -- it's only ever added to there, via the shared
        // bund-overflow step both paths run through.
        let (runoff, irrigation_application, carried_surface_storage) =
            if infiltration_override.is_some() {
                (
                    0.0,
                    IrrigationApplication {
                        depth: 0.0,
                        conductivity: 0.0,
                        generated_triggered: false,
                    },
                    self.state.surface_water,
                )
            } else {
                let runoff = self.calculate_runoff(rain)?;
                let irrigation_application = self.irrigation_for_day(
                    day_number,
                    rooting.depth,
                    rain,
                    runoff,
                    redistribution_drainage,
                    external_irrigation,
                    external_irrigation_conductivity,
                )?;
                (runoff, irrigation_application, 0.0)
            };
        let irrigation = irrigation_application.depth;
        let irrigation_conductivity = irrigation_application.conductivity;
        let mixed_surface_conductivity = if self.state.surface_water + irrigation + rain > 0.0 {
            (self.state.surface_conductivity * self.state.surface_water
                + irrigation_conductivity * irrigation)
                / (self.state.surface_water + irrigation + rain)
        } else {
            0.0
        };
        let mut infiltration = if let Some(value) = infiltration_override {
            InfiltrationState {
                infiltrated_rain: value,
                infiltrated_irrigation: 0.0,
                infiltrated_storage: 0.0,
                subsoil_drainage: 0.0,
                runoff,
                surface_storage: carried_surface_storage,
                deep_drainage: redistribution_drainage,
            }
        } else {
            InfiltrationState {
                infiltrated_rain: (rain - runoff).max(0.0),
                infiltrated_irrigation: irrigation,
                infiltrated_storage: self.state.surface_water,
                subsoil_drainage: 0.0,
                runoff,
                surface_storage: 0.0,
                deep_drainage: redistribution_drainage,
            }
        };
        let evaporation_depth = f64::from(self.inputs.parameters.maximum_evaporation_depth) / 100.0;
        infiltrate(
            &self.inputs.soil,
            &mut self.state.compartments,
            &InfiltrationParameters {
                rainfall_aggregation: RainfallAggregation::Daily,
                rooting_depth: rooting.depth,
                evaporation_depth,
                bund_height: self.inputs.management.bund_height,
            },
            &mut infiltration,
        )?;
        self.state.surface_water = infiltration.surface_storage;
        self.state.surface_conductivity = if infiltration.surface_storage > 0.0 {
            mixed_surface_conductivity
        } else {
            0.0
        };
        let capillary_rise = if let Some((depth, conductivity)) = groundwater {
            apply_capillary_rise(
                &self.inputs.soil,
                &mut self.state.compartments,
                depth,
                conductivity,
                self.inputs.parameters.capillary_rise_shape,
            )?
        } else {
            CapillaryRise {
                water: 0.0,
                salt: 0.0,
            }
        };
        let salt = transport_salts(
            &self.inputs.soil,
            &mut self.state.compartments,
            &infiltration,
            &SaltTransportParameters {
                irrigation_conductivity,
                surface_storage_conductivity: mixed_surface_conductivity,
                rooting_depth: rooting.depth,
                evaporation_depth,
                solubility: f64::from(self.inputs.parameters.salt_solubility),
            },
        )?;
        Ok((
            beginning,
            infiltration,
            salt,
            capillary_rise,
            irrigation_application,
        ))
    }

    fn advance_salinity_stress(
        &mut self,
        day_after_planting: i32,
        rooting_depth: f64,
    ) -> Result<(), Error> {
        if !self.state.salinity_effects.considered {
            return Ok(());
        }
        let root_zone = determine_root_zone_salinity(
            &self.inputs.soil,
            &self.state.compartments,
            rooting_depth,
            self.run.crop.parameters.rooting.minimum_depth,
            &SalinityResponse {
                minimum: self.run.crop.parameters.salinity_stress.ece_minimum,
                maximum: self.run.crop.parameters.salinity_stress.ece_maximum,
                shape: 0.0,
            },
            f64::from(self.inputs.parameters.salt_solubility),
        )?;
        let Some(root_zone) = root_zone else {
            self.state.salinity_effects.root_zone = None;
            return Ok(());
        };
        let salinity = &mut self.state.salinity_effects;
        salinity.pending_average_stress = (f64::from(salinity.stress_observation_count)
            * salinity.average_stress
            + 100.0 * (1.0 - root_zone.stress_coefficient))
            / f64::from(salinity.stress_observation_count + 1);
        salinity.root_zone = Some(root_zone);
        if salinity.calibration.is_none() {
            return Ok(());
        }

        let virtual_day = day_after_planting - 1;
        let calendar = self.run.crop.cycle.calendar_days();
        if virtual_day < calendar.emergence
            || virtual_day > calendar.maturity
            || !self.state.germinated
        {
            salinity.clear_effects();
        } else {
            let calibration =
                salinity
                    .calibration
                    .as_ref()
                    .ok_or(Error::InvalidSimulationConfiguration(
                        "salinity calibration is unavailable",
                    ))?;
            let stress = salinity.pending_average_stress;
            let fitted = roundc_i32(
                calibration.reduction_intercept
                    + calibration.reduction_linear * stress
                    + calibration.reduction_quadratic * stress * stress,
            );
            if fitted < 0 || stress <= 0.1 {
                salinity.clear_effects();
            } else {
                let reduction = if fitted > 100 || stress >= 99.9 {
                    100
                } else {
                    roundc_i8(f64::from(fitted))
                };
                let effects = salinity_canopy_effects(&self.run.crop, reduction);
                salinity.set_effects(&effects);
            }
        }
        self.state.fertility_effects.update_for_stress(
            &self.inputs,
            &self.run,
            self.state.fertility_effects.current_stress,
            &self.state.salinity_effects,
        )
    }

    fn commit_daily_salinity(&mut self, rooting_depth: f64) -> Result<(), Error> {
        if !self.state.salinity_effects.considered || self.state.canopy_cover <= 0.0 {
            return Ok(());
        }
        let Some(root_zone) = determine_root_zone_salinity(
            &self.inputs.soil,
            &self.state.compartments,
            rooting_depth,
            self.run.crop.parameters.rooting.minimum_depth,
            &SalinityResponse {
                minimum: self.run.crop.parameters.salinity_stress.ece_minimum,
                maximum: self.run.crop.parameters.salinity_stress.ece_maximum,
                shape: 0.0,
            },
            f64::from(self.inputs.parameters.salt_solubility),
        )?
        else {
            return Ok(());
        };
        let salinity = &mut self.state.salinity_effects;
        salinity.stress_observation_count += 1;
        salinity.average_stress = (f64::from(salinity.stress_observation_count - 1)
            * salinity.average_stress
            + 100.0 * (1.0 - root_zone.stress_coefficient))
            / f64::from(salinity.stress_observation_count);
        salinity.root_zone = Some(root_zone);
        Ok(())
    }

    fn initialize_groundwater(&mut self, groundwater: Option<(f64, f64)>) -> Result<(), Error> {
        let Some((depth, conductivity)) = groundwater else {
            return Ok(());
        };
        initialize_groundwater_profile(
            &self.inputs.soil,
            &mut self.state.compartments,
            depth,
            conductivity,
            f64::from(self.inputs.parameters.salt_solubility),
        )
    }

    fn advance_horizontal_groundwater_inflow(
        &mut self,
        groundwater: Option<(f64, f64)>,
    ) -> Result<GroundwaterInflow, Error> {
        let Some((depth, conductivity)) = groundwater else {
            return Ok(GroundwaterInflow {
                water: 0.0,
                salt: 0.0,
            });
        };
        horizontal_groundwater_inflow(
            &self.inputs.soil,
            &mut self.state.compartments,
            depth,
            conductivity,
            f64::from(self.inputs.parameters.salt_solubility),
        )
    }

    fn advance_canopy(
        &mut self,
        day_after_planting: i32,
        daily_gdd: f64,
        rooting_depth: f64,
        reference_et: f64,
    ) -> Result<(), Error> {
        let calendar_days = self.run.crop.cycle.calendar_days();
        if day_after_planting < 1 || day_after_planting > calendar_days.maturity {
            self.state.potential_canopy_cover = 0.0;
            self.state.canopy_cover = 0.0;
            return Ok(());
        }
        let virtual_canopy_day = day_after_planting - 1;
        let fertility_is_active = self.run.crop.parameters.planting_method
            == PlantingMethod::Regrowth
            || virtual_canopy_day >= calendar_days.emergence
                && self.state.sum_gdd
                    >= f64::from(self.run.crop.cycle.driving_calendar().emergence);
        self.state.potential_canopy_cover = potential_canopy_cover_with_timing(
            &self.run.crop,
            &self.canopy_timing,
            day_after_planting,
            0,
            self.state.sum_gdd,
            0,
            0,
        );
        self.state.canopy_cover = if !fertility_is_active {
            self.state.potential_canopy_cover
        } else if self.run.crop.cycle.uses_growing_degree_days() {
            self.advance_gdd_canopy(day_after_planting, daily_gdd, rooting_depth, reference_et)?
        } else {
            self.advance_calendar_days_canopy(day_after_planting, rooting_depth, reference_et)?
        };
        self.state.withered_canopy_cover = self
            .state
            .withered_canopy_cover
            .max(self.state.canopy_cover);
        Ok(())
    }

    #[expect(
        clippy::too_many_lines,
        reason = "the reference model's early-senescence branches are kept together for auditing"
    )]
    fn advance_early_senescence(
        &mut self,
        day_after_planting: i32,
        daily_gdd: f64,
        rooting_depth: f64,
        reference_et: f64,
    ) -> Result<(), Error> {
        if self.state.canopy_cover <= 0.0 || !self.state.germinated {
            return Ok(());
        }

        let crop = &self.run.crop;
        let calendar_days = crop.cycle.calendar_days();
        let driving_calendar = crop.cycle.driving_calendar();
        let calendar_mode = !crop.cycle.uses_growing_degree_days();
        let development_time = if calendar_mode {
            f64::from(day_after_planting - 1)
        } else {
            adjusted_canopy_gdd_with_timing(crop, &self.canopy_timing, self.state.sum_gdd)
        };
        let natural_senescence = if calendar_mode {
            f64::from(calendar_days.senescence)
        } else {
            f64::from(driving_calendar.senescence)
        };
        if development_time >= natural_senescence
            && self.state.early_senescence_time <= f64::EPSILON
        {
            return Ok(());
        }
        self.state.senescence_stress = Some(0.0);

        let threshold = adjusted_senescence_threshold(
            crop,
            &self.inputs.parameters,
            reference_et,
            self.state.early_senescence_time,
            true,
        );
        let water = determine_root_zone_water(
            &self.inputs.soil,
            &self.state.compartments,
            rooting_depth,
            f64::from(self.inputs.parameters.upper_soil_depth) / 100.0,
            &DepletionThresholds {
                leaf_expansion: crop.parameters.water_stress.canopy_expansion_upper,
                stomatal_closure: crop.parameters.water_stress.stomatal_closure_upper,
                senescence: threshold,
            },
        )?;
        let (actual, field_capacity, wilting_point) = if water.upper_zone_is_wetter {
            (
                water.upper_actual,
                water.upper_field_capacity,
                water.upper_wilting_point,
            )
        } else {
            (water.actual, water.field_capacity, water.wilting_point)
        };
        let stressed = !self.state.protected_seedling
            && actual < field_capacity - threshold * (field_capacity - wilting_point);
        if !stressed {
            self.state.early_senescence_time = 0.0;
            self.state.cumulative_senescence_reference_et = 0.0;
            return Ok(());
        }

        self.state.evaporation_limit_enabled = true;
        if self.state.early_senescence_time.abs() < f64::EPSILON {
            self.state.early_senescence_peak_cover = self.state.canopy_cover;
        }
        self.state.early_senescence_time += if calendar_mode { 1.0 } else { daily_gdd };

        let relative_depletion = if field_capacity > wilting_point {
            (field_capacity - actual) / (field_capacity - wilting_point)
        } else {
            0.0
        };
        let unmodified_threshold = adjusted_senescence_threshold(
            crop,
            &self.inputs.parameters,
            reference_et,
            self.state.early_senescence_time,
            false,
        );
        let lower_threshold = 0.999;
        let stress_limited_cover = self.fertility_adjusted_canopy(day_after_planting).max(0.0);
        let base_decline = if calendar_mode {
            calendar_days.canopy_decline_coefficient
        } else {
            driving_calendar.canopy_decline_coefficient
        };
        let minimum_decline = if calendar_mode { 0.001 } else { 0.0001 };
        let (adjusted_decline, senescence_stress) = if relative_depletion <= unmodified_threshold {
            (minimum_decline, 0.0)
        } else if relative_depletion >= lower_threshold {
            (
                base_decline * (stress_limited_cover + 2.29)
                    / (crop.parameters.canopy.maximum_cover + 2.29),
                100.0,
            )
        } else {
            let coefficient = stress_coefficient(
                relative_depletion,
                unmodified_threshold,
                lower_threshold,
                crop.parameters.water_stress.senescence_shape,
            );
            if coefficient > crate::compat::ZERO_THRESHOLD {
                (
                    base_decline * (stress_limited_cover + 2.29)
                        / (crop.parameters.canopy.maximum_cover + 2.29)
                        * (1.0 - coefficient.powi(8)),
                    100.0 * (1.0 - coefficient),
                )
            } else if calendar_mode {
                (0.0, 0.0)
            } else {
                (minimum_decline, 0.0)
            }
        };
        self.state.senescence_stress = Some(senescence_stress);

        let final_growth = if calendar_mode {
            if crop.parameters.kind == CropKind::Grain
                && crop.parameters.development.determinacy_linked_to_flowering
                && crop.parameters.planting_method != PlantingMethod::Regrowth
            {
                (calendar_days.flowering
                    + roundc_i32(f64::from(calendar_days.flowering_length) / 2.0))
                .min(calendar_days.senescence)
            } else {
                calendar_days.senescence
            }
        } else {
            final_canopy_growth_gdd(crop, &self.canopy_timing)
        };
        let delayed_wilting = f64::from(crop.parameters.water_stress.eto_delay_senescence);
        let dormant_cover = 0.05;
        let initial_cover = crop.parameters.canopy.initial_seedling_cover;
        let dormant_floor = || {
            if dormant_cover > initial_cover && delayed_wilting > 0.0 {
                initial_cover
                    + (1.0 - self.state.cumulative_senescence_reference_et / delayed_wilting)
                        * (dormant_cover - initial_cover)
            } else {
                initial_cover
            }
        };
        let peak = self.state.early_senescence_peak_cover;
        let invalid_decline = peak < 0.001
            || self.state.early_senescence_time * adjusted_decline * 3.33 / (peak + 2.29) > 100.0
            || self.state.previous_reported_canopy_cover >= 1.05 * peak;
        let past_delay = self.state.cumulative_senescence_reference_et > delayed_wilting
            || delayed_wilting.abs() < f64::EPSILON
            || development_time >= f64::from(final_growth);
        let mut senesced = if invalid_decline {
            if past_delay { 0.0 } else { dormant_floor() }
        } else {
            let decline = adjusted_decline.max(f64::EPSILON);
            let elapsed = (1.0 + (1.0 - self.state.previous_reported_canopy_cover / peak) / 0.05)
                .ln()
                / (decline * 3.33 / (peak + 2.29));
            let increment = if calendar_mode { 1.0 } else { daily_gdd };
            (peak
                * (1.0
                    - 0.05
                        * (((elapsed + increment) * decline * 3.33 / (peak + 2.29)).exp() - 1.0)))
                .max(0.0)
        };
        if delayed_wilting > 0.0
            && self.state.cumulative_senescence_reference_et <= delayed_wilting
            && development_time < f64::from(final_growth)
            && (senesced < initial_cover || senesced < dormant_cover)
        {
            senesced = dormant_floor();
        }
        if development_time < natural_senescence {
            senesced = senesced
                .min(stress_limited_cover)
                .min(self.state.previous_reported_canopy_cover);
            self.state.adjusted_maximum_canopy_cover = senesced;
            self.state.adjusted_initial_canopy_cover = senesced.min(initial_cover);
            self.state.canopy_cover = senesced;
        } else {
            self.state.canopy_cover = self.state.canopy_cover.min(senesced);
        }
        if roundc_i32(10_000.0 * senesced) <= roundc_i32(10_000.0 * dormant_cover)
            || roundc_i32(10_000.0 * senesced) <= roundc_i32(10_000.0 * initial_cover)
        {
            self.state.cumulative_senescence_reference_et += reference_et;
        }
        Ok(())
    }

    #[expect(
        clippy::too_many_lines,
        reason = "the source canopy update order is kept together for numerical auditing"
    )]
    fn advance_gdd_canopy(
        &mut self,
        day_after_planting: i32,
        daily_gdd: f64,
        rooting_depth: f64,
        reference_et: f64,
    ) -> Result<f64, Error> {
        let crop = &self.run.crop;
        let driving_calendar = crop.cycle.driving_calendar();
        let canopy_sum_gdd =
            adjusted_canopy_gdd_with_timing(crop, &self.canopy_timing, self.state.sum_gdd);
        if canopy_sum_gdd <= f64::from(driving_calendar.emergence)
            || roundc_i32(self.state.sum_gdd) > driving_calendar.maturity
        {
            return Ok(0.0);
        }

        let fertility_growth = driving_calendar.canopy_growth_coefficient
            * (1.0
                - f64::from(self.state.fertility_effects.canopy_timing.growth_reduction) / 100.0);
        let leaf_expansion = self.leaf_expansion_coefficient(rooting_depth, reference_et)?;
        let adjusted_growth = fertility_growth * leaf_expansion;
        let maximum = crop.parameters.canopy.maximum_cover
            * (1.0
                - f64::from(
                    self.state
                        .fertility_effects
                        .canopy_timing
                        .maximum_cover_reduction,
                ) / 100.0);
        let fertility_limited_cover = if crop.parameters.planting_method == PlantingMethod::Regrowth
            && canopy_sum_gdd <= f64::from(self.state.fertility_effects.gdd_to_full_canopy)
        {
            maximum
        } else {
            self.fertility_adjusted_canopy(day_after_planting)
        };
        let stress_limited_potential_cover = potential_canopy_cover_with_timing(
            crop,
            &self.canopy_timing,
            day_after_planting,
            0,
            self.state.sum_gdd,
            self.state.fertility_effects.canopy_timing.growth_reduction,
            self.state
                .fertility_effects
                .canopy_timing
                .maximum_cover_reduction,
        );
        let final_growth_gdd = final_canopy_growth_gdd(crop, &self.canopy_timing);

        if canopy_sum_gdd >= f64::from(final_growth_gdd) {
            // After canopy growth, decline starts from the achieved peak.
            let achieved = self.state.adjusted_maximum_canopy_cover;
            let cover = if canopy_sum_gdd >= f64::from(driving_calendar.senescence) {
                senescence_decline_from_achieved(
                    achieved,
                    crop.parameters.canopy.maximum_cover,
                    driving_calendar.canopy_decline_coefficient,
                    canopy_sum_gdd - f64::from(driving_calendar.senescence),
                )
            } else {
                achieved
            };
            return Ok(cover.min(fertility_limited_cover));
        }

        let initial = crop.parameters.canopy.initial_seedling_cover;
        let previous = if day_after_planting == 1
            && crop.parameters.planting_method == PlantingMethod::Regrowth
        {
            self.state.initial_regrowth_canopy_cover
        } else {
            self.state.canopy_cover
        };
        let mut cover = if previous <= self.state.adjusted_initial_canopy_cover
            || canopy_sum_gdd <= daily_gdd
            || self.state.protected_seedling && previous <= 1.25 * initial
        {
            if self.state.protected_seedling {
                let cover = stress_limited_potential_cover;
                if cover > 1.25 * initial {
                    self.state.protected_seedling = false;
                }
                cover
            } else {
                self.state.adjusted_initial_canopy_cover * (fertility_growth * daily_gdd).exp()
            }
        } else if previous < 0.97999 * maximum {
            self.state.leaf_expansion_stress = Some(100.0 * (1.0 - leaf_expansion));
            let previous_sum_gdd = adjusted_canopy_gdd_with_timing(
                crop,
                &self.canopy_timing,
                self.state.sum_gdd - daily_gdd,
            );
            let fictive_gdd = required_gdd(
                previous,
                self.state.adjusted_initial_canopy_cover,
                maximum,
                adjusted_growth,
                previous_sum_gdd,
            );
            self.state.adjusted_maximum_canopy_cover = if fictive_gdd > 0.0 {
                canopy_at_gdd(
                    fictive_gdd + f64::from(final_growth_gdd) - canopy_sum_gdd + daily_gdd,
                    self.state.adjusted_initial_canopy_cover,
                    adjusted_growth,
                    maximum,
                )
            } else {
                -1.0
            };
            if self.state.adjusted_maximum_canopy_cover < 0.0 {
                previous
            } else if (previous - 0.97999 * maximum).abs() < 0.001 {
                stress_limited_potential_cover
            } else {
                let required = required_gdd(
                    previous,
                    self.state.adjusted_initial_canopy_cover,
                    self.state.adjusted_maximum_canopy_cover,
                    adjusted_growth,
                    previous_sum_gdd,
                );
                if required < 0.0 {
                    previous
                } else {
                    canopy_at_gdd(
                        required + daily_gdd,
                        self.state.adjusted_initial_canopy_cover,
                        adjusted_growth,
                        self.state.adjusted_maximum_canopy_cover,
                    )
                }
            }
        } else {
            self.state.adjusted_initial_canopy_cover = initial;
            stress_limited_potential_cover
        };
        let comparison_tolerance =
            16.0 * f64::EPSILON * cover.abs().max(fertility_limited_cover.abs()).max(1.0);
        let exceeds_fertility_limit = cover > fertility_limited_cover + comparison_tolerance;
        cover = cover.min(fertility_limited_cover);
        if previous >= 0.97999 * maximum || exceeds_fertility_limit {
            self.state.leaf_expansion_stress = None;
        }
        self.state.adjusted_maximum_canopy_cover = cover;
        Ok(cover)
    }

    /// Advances canopy cover for a calendar-day crop.
    #[expect(
        clippy::too_many_lines,
        reason = "keeps the reference canopy-update sequence together"
    )]
    fn advance_calendar_days_canopy(
        &mut self,
        day_after_planting: i32,
        rooting_depth: f64,
        reference_et: f64,
    ) -> Result<f64, Error> {
        const DAY_INCREMENT: f64 = 1.0;
        let crop = &self.run.crop;
        let calendar_days = crop.cycle.calendar_days();
        let canopy_day = f64::from(adjusted_canopy_day_with_timing(
            crop,
            &self.canopy_timing,
            day_after_planting,
        ));
        if canopy_day <= f64::from(calendar_days.emergence)
            || day_after_planting > calendar_days.maturity
        {
            return Ok(0.0);
        }

        let fertility_growth = calendar_days.canopy_growth_coefficient
            * (1.0
                - f64::from(self.state.fertility_effects.canopy_timing.growth_reduction) / 100.0);
        let leaf_expansion = self.leaf_expansion_coefficient(rooting_depth, reference_et)?;
        let adjusted_growth = fertility_growth * leaf_expansion;
        let maximum = crop.parameters.canopy.maximum_cover
            * (1.0
                - f64::from(
                    self.state
                        .fertility_effects
                        .canopy_timing
                        .maximum_cover_reduction,
                ) / 100.0);
        let fertility_limited_cover = if crop.parameters.planting_method == PlantingMethod::Regrowth
            && canopy_day
                <= f64::from(
                    self.state
                        .fertility_effects
                        .canopy_timing
                        .days_to_full_canopy,
                ) {
            maximum
        } else {
            self.fertility_adjusted_canopy(day_after_planting)
        };
        let stress_limited_potential_cover = potential_canopy_cover_with_timing(
            crop,
            &self.canopy_timing,
            day_after_planting,
            0,
            self.state.sum_gdd,
            self.state.fertility_effects.canopy_timing.growth_reduction,
            self.state
                .fertility_effects
                .canopy_timing
                .maximum_cover_reduction,
        );
        let final_growth_days = final_canopy_growth_days(crop, &self.canopy_timing);

        if canopy_day >= f64::from(final_growth_days) {
            // After canopy growth, decline starts from the achieved peak.
            let achieved = self.state.adjusted_maximum_canopy_cover;
            let cover = if canopy_day >= f64::from(calendar_days.senescence) {
                senescence_decline_from_achieved(
                    achieved,
                    crop.parameters.canopy.maximum_cover,
                    calendar_days.canopy_decline_coefficient,
                    canopy_day - f64::from(calendar_days.senescence),
                )
            } else {
                achieved
            };
            return Ok(cover.min(fertility_limited_cover));
        }

        let initial = crop.parameters.canopy.initial_seedling_cover;
        let previous = if day_after_planting == 1
            && crop.parameters.planting_method == PlantingMethod::Regrowth
        {
            self.state.initial_regrowth_canopy_cover
        } else {
            self.state.canopy_cover
        };
        let mut cover = if previous <= self.state.adjusted_initial_canopy_cover
            || canopy_day <= DAY_INCREMENT
            || self.state.protected_seedling && previous <= 1.25 * initial
        {
            if self.state.protected_seedling {
                let cover = stress_limited_potential_cover;
                if cover > 1.25 * initial {
                    self.state.protected_seedling = false;
                }
                cover
            } else {
                self.state.adjusted_initial_canopy_cover * (fertility_growth * DAY_INCREMENT).exp()
            }
        } else if previous < 0.97999 * maximum {
            self.state.leaf_expansion_stress = Some(100.0 * (1.0 - leaf_expansion));
            let previous_day = f64::from(adjusted_canopy_day_with_timing(
                crop,
                &self.canopy_timing,
                day_after_planting - 1,
            ));
            let fictive_days = required_gdd(
                previous,
                self.state.adjusted_initial_canopy_cover,
                maximum,
                adjusted_growth,
                previous_day,
            );
            self.state.adjusted_maximum_canopy_cover = if fictive_days > 0.0 {
                canopy_at_gdd(
                    fictive_days + f64::from(final_growth_days) - canopy_day + DAY_INCREMENT,
                    self.state.adjusted_initial_canopy_cover,
                    adjusted_growth,
                    maximum,
                )
            } else {
                -1.0
            };
            if self.state.adjusted_maximum_canopy_cover < 0.0 {
                previous
            } else if (previous - 0.97999 * maximum).abs() < 0.001 {
                stress_limited_potential_cover
            } else {
                let required = required_gdd(
                    previous,
                    self.state.adjusted_initial_canopy_cover,
                    self.state.adjusted_maximum_canopy_cover,
                    adjusted_growth,
                    previous_day,
                );
                if required < 0.0 {
                    previous
                } else {
                    canopy_at_gdd(
                        required + DAY_INCREMENT,
                        self.state.adjusted_initial_canopy_cover,
                        adjusted_growth,
                        self.state.adjusted_maximum_canopy_cover,
                    )
                }
            }
        } else {
            self.state.adjusted_initial_canopy_cover = initial;
            stress_limited_potential_cover
        };
        let comparison_tolerance =
            16.0 * f64::EPSILON * cover.abs().max(fertility_limited_cover.abs()).max(1.0);
        let exceeds_fertility_limit = cover > fertility_limited_cover + comparison_tolerance;
        cover = cover.min(fertility_limited_cover);
        if previous >= 0.97999 * maximum || exceeds_fertility_limit {
            self.state.leaf_expansion_stress = None;
        }
        self.state.adjusted_maximum_canopy_cover = cover;
        Ok(cover)
    }

    fn leaf_expansion_coefficient(
        &self,
        rooting_depth: f64,
        reference_et: f64,
    ) -> Result<f64, Error> {
        let stress = &self.run.crop.parameters.water_stress;
        let adjustment = |threshold| {
            if self.run.crop.parameters.depletion_adjustment == DepletionAdjustment::Fao {
                adjust_depletion_threshold_for_eto(
                    threshold,
                    reference_et,
                    self.inputs.parameters.depletion_et_adjustment_factor,
                )
            } else {
                threshold
            }
        };
        let upper = adjustment(stress.canopy_expansion_upper);
        let lower = adjustment(stress.canopy_expansion_lower);
        let water = determine_root_zone_water(
            &self.inputs.soil,
            &self.state.compartments,
            rooting_depth,
            f64::from(self.inputs.parameters.upper_soil_depth) / 100.0,
            &DepletionThresholds {
                leaf_expansion: upper,
                stomatal_closure: adjustment(stress.stomatal_closure_upper),
                senescence: adjustment(stress.senescence_upper),
            },
        )?;
        let (actual, field_capacity, wilting_point) = if water.upper_zone_is_wetter {
            (
                water.upper_actual,
                water.upper_field_capacity,
                water.upper_wilting_point,
            )
        } else {
            (water.actual, water.field_capacity, water.wilting_point)
        };
        if actual >= field_capacity {
            return Ok(1.0);
        }
        if actual <= wilting_point {
            return Ok(0.0);
        }
        let relative_depletion = (field_capacity - actual) / (field_capacity - wilting_point);
        Ok(stress_coefficient(
            relative_depletion,
            upper,
            lower,
            stress.canopy_expansion_shape,
        ))
    }

    fn fertility_adjusted_canopy(&self, day_after_planting: i32) -> f64 {
        fertility_adjusted_potential_canopy_cover_with_timing(
            &self.run.crop,
            &self.canopy_timing,
            day_after_planting,
            0,
            self.state.sum_gdd,
            &self.state.fertility_effects.canopy_timing,
            self.state.fertility_effects.gdd_to_full_canopy,
            self.state.fertility_effects.canopy_decline,
        )
    }

    fn daily_potential_et(
        &self,
        day_after_planting: i32,
        reference_et: f64,
        carbon_dioxide: f64,
        daily_gdd: f64,
    ) -> Result<PotentialEt, Error> {
        let calendar = self.run.crop.cycle.calendar_days();
        let potential_day = if self.run.crop.cycle.uses_growing_degree_days() {
            calendar_days_for_gdd(
                &self.inputs,
                &self.run,
                roundc_i32(adjusted_canopy_gdd_with_timing(
                    &self.run.crop,
                    &self.canopy_timing,
                    self.state.sum_gdd,
                )),
                self.run.first_crop_day_number,
            )?
        } else {
            adjusted_canopy_day_with_timing(&self.run.crop, &self.canopy_timing, day_after_planting)
        };
        Ok(potential_et(&PotentialEtParameters {
            days_after_planting: potential_day,
            delayed_days: 0,
            days_to_germination: calendar.emergence,
            days_to_full_canopy: self.canopy_timing.days_to_full_canopy,
            days_to_senescence: calendar.senescence,
            days_to_harvest: calendar.maturity,
            day_last_cut: self.state.cutting.last_cut_day,
            canopy_cover: self.state.canopy_cover,
            reference_et,
            crop_coefficient: self.run.crop.parameters.evapotranspiration.kc_top,
            coefficient_decline: self
                .run
                .crop
                .parameters
                .evapotranspiration
                .kc_decline_cumulative,
            maximum_canopy_cover: self.run.crop.parameters.canopy.maximum_cover,
            withered_canopy_cover: self.state.withered_canopy_cover,
            late_evaporation_effect: f64::from(
                self.run
                    .crop
                    .parameters
                    .evapotranspiration
                    .late_season_evaporation_reduction,
            ),
            carbon_dioxide,
            growing_degree_days: daily_gdd,
            cold_stress_threshold_gdd: self.run.crop.parameters.temperature.full_transpiration_gdd,
            wet_bare_soil_coefficient: self.inputs.parameters.wet_bare_soil_coefficient,
            senescence_exponent: self.inputs.parameters.senescence_exponent,
            evaporation_limit_enabled: self.state.evaporation_limit_enabled,
        }))
    }

    #[expect(
        clippy::too_many_lines,
        reason = "surface and soil fluxes preserve the reference model's audited process order"
    )]
    fn evaporate_and_transpire(
        &mut self,
        rooting: &RootingResult,
        potential: &PotentialEt,
        reference_et: f64,
        rain: f64,
        irrigation: f64,
        infiltration: &mut InfiltrationState,
    ) -> Result<(SoilEvaporation, RootExtraction), Error> {
        if rain + irrigation > 0.0 {
            self.state.evaporation.prepare_stage_one(
                f64::from(self.inputs.soil.readily_evaporable_water),
                rain,
                irrigation,
                infiltration.runoff,
                infiltration.surface_storage,
            );
        }
        let surface_evaporation = potential
            .soil_evaporation
            .min(infiltration.surface_storage)
            .max(0.0);
        if surface_evaporation > 0.0 {
            infiltration.surface_storage -= surface_evaporation;
            self.state.surface_water = infiltration.surface_storage;
            if infiltration.surface_storage <= crate::compat::ZERO_THRESHOLD {
                infiltration.surface_storage = 0.0;
                self.state.surface_water = 0.0;
                self.state.surface_conductivity = 0.0;
            } else {
                self.state.surface_conductivity *= (infiltration.surface_storage
                    + surface_evaporation)
                    / infiltration.surface_storage;
            }
        }
        let mut evaporation = evaporate_soil(
            &self.inputs.soil,
            &mut self.state.compartments,
            &mut self.state.evaporation,
            potential.soil_evaporation - surface_evaporation,
            f64::from(self.inputs.parameters.maximum_evaporation_depth) / 100.0,
            f64::from(self.inputs.parameters.evaporation_decline_factor),
        )?;
        evaporation.actual += surface_evaporation;
        if rooting.depth <= crate::compat::ZERO_THRESHOLD
            || potential.transpiration <= crate::compat::ZERO_THRESHOLD
        {
            if infiltration.surface_storage <= f64::EPSILON {
                self.state.submerged_exposure = 0;
            }
            return Ok((
                evaporation,
                RootExtraction {
                    actual_transpiration: 0.0,
                    unmet_transpiration: potential.transpiration.max(0.0),
                },
            ));
        }
        let surface_path_is_open = infiltration.surface_storage > 0.0
            && (self.run.crop.parameters.water_stress.anaerobic_point_volume == 0
                || self.state.submerged_exposure < self.inputs.parameters.deficient_aeration_delay);
        let mut surface_transpiration = 0.0;
        let mut remaining_transpiration = potential.transpiration;
        if surface_path_is_open {
            self.state.submerged_exposure += 1;
            for compartment in &mut self.state.compartments {
                compartment.anaerobic_exposure = (compartment.anaerobic_exposure + 1)
                    .min(self.inputs.parameters.deficient_aeration_delay);
            }
            let oxygen_coefficient =
                if self.run.crop.parameters.water_stress.anaerobic_point_volume > 0 {
                    1.0 - f64::from(self.state.submerged_exposure)
                        / f64::from(self.inputs.parameters.deficient_aeration_delay)
                } else {
                    1.0
                };
            let salinity_coefficient = salinity_stress_coefficient(
                self.run.crop.parameters.salinity_stress.ece_minimum,
                self.run.crop.parameters.salinity_stress.ece_maximum,
                self.state.surface_conductivity,
                0.0,
            );
            let surface_demand =
                salinity_coefficient * oxygen_coefficient * potential.transpiration;
            let salt_mass = infiltration.surface_storage * self.state.surface_conductivity;
            if infiltration.surface_storage > surface_demand {
                surface_transpiration = surface_demand;
                infiltration.surface_storage -= surface_transpiration;
            } else if infiltration.surface_storage > 0.1 {
                surface_transpiration = infiltration.surface_storage - 0.1;
                infiltration.surface_storage = 0.1;
            }
            self.state.surface_water = infiltration.surface_storage;
            self.state.surface_conductivity = if infiltration.surface_storage > 0.0 {
                salt_mass / infiltration.surface_storage
            } else {
                0.0
            };
            remaining_transpiration = (surface_demand - surface_transpiration).max(0.0);
        }
        if infiltration.surface_storage < f64::EPSILON {
            self.state.submerged_exposure = 0;
        }
        let stomatal_depletion =
            if self.run.crop.parameters.depletion_adjustment == DepletionAdjustment::Fao {
                adjust_depletion_threshold_for_eto(
                    self.run.crop.parameters.water_stress.stomatal_closure_upper,
                    reference_et,
                    self.inputs.parameters.depletion_et_adjustment_factor,
                )
            } else {
                self.run.crop.parameters.water_stress.stomatal_closure_upper
            };
        let root_zone_stress = self.root_zone_stomatal_stress(rooting.depth, stomatal_depletion)?;
        let local_stress = self.local_root_stress(stomatal_depletion)?;
        let extraction = extract_root_water(
            &self.inputs.soil,
            &mut self.state.compartments,
            &RootExtractionParameters {
                rooting_depth: rooting.depth,
                potential_transpiration: root_zone_stress * remaining_transpiration,
                extraction_top: self.run.crop.parameters.rooting.extraction_top,
                extraction_bottom: self.run.crop.parameters.rooting.extraction_bottom,
                root_density_correction: rooting.density_correction,
                gravel_mode: ExtractionGravelMode::StrictReference,
            },
            &local_stress,
        )?;
        Ok((
            evaporation,
            RootExtraction {
                actual_transpiration: surface_transpiration + extraction.actual_transpiration,
                unmet_transpiration: (potential.transpiration
                    - surface_transpiration
                    - extraction.actual_transpiration)
                    .max(0.0),
            },
        ))
    }

    fn apply_net_irrigation(&mut self, rooting_depth: f64) -> Result<f64, Error> {
        let Some(IrrigationSchedule {
            mode:
                IrrigationMode::NetRequirement {
                    readily_available_water_depletion,
                },
            ..
        }) = self.inputs.irrigation
        else {
            return Ok(0.0);
        };
        let depletion = f64::from(readily_available_water_depletion) / 100.0;
        let water = determine_root_zone_water(
            &self.inputs.soil,
            &self.state.compartments,
            rooting_depth.max(self.run.crop.parameters.rooting.minimum_depth),
            f64::from(self.inputs.parameters.upper_soil_depth) / 100.0,
            &DepletionThresholds {
                leaf_expansion: self.run.crop.parameters.water_stress.canopy_expansion_upper,
                stomatal_closure: self.run.crop.parameters.water_stress.stomatal_closure_upper,
                senescence: self.run.crop.parameters.water_stress.senescence_upper,
            },
        )?;
        let root_threshold =
            water.field_capacity - depletion * (water.field_capacity - water.stomatal_threshold);
        if water.actual >= root_threshold {
            return Ok(0.0);
        }

        let mut irrigation = 0.0;
        for (index, compartment) in self.state.compartments.iter_mut().enumerate() {
            let layer = self.inputs.soil.layers.get(compartment.layer_index).ok_or(
                crate::water_balance::WaterBalanceError::MissingLayer {
                    compartment: index,
                    layer: compartment.layer_index,
                },
            )?;
            let field_capacity = layer.field_capacity / 100.0;
            let wilting_point = layer.wilting_point / 100.0;
            let stomatal_threshold = field_capacity
                - (field_capacity - wilting_point)
                    * self.run.crop.parameters.water_stress.stomatal_closure_upper;
            let target = field_capacity - depletion * (field_capacity - stomatal_threshold);
            let storage = 1_000.0 * compartment.thickness * layer.fine_earth_fraction();
            let added = compartment.weighting_factor * (target - compartment.theta) * storage;
            compartment.theta += added / storage;
            irrigation += added;
        }
        Ok(irrigation)
    }

    fn advance_yield(
        &mut self,
        day_after_planting: i32,
        daily_biomass: f64,
        rooting_depth: f64,
        reference_et: f64,
        temperatures: (f64, f64),
        daily_harvest_index: &DailyHarvestIndex,
    ) -> Result<Option<f64>, Error> {
        if matches!(
            self.run.crop.parameters.kind,
            CropKind::Vegetative | CropKind::Forage
        ) {
            self.state.dry_yield += daily_biomass * daily_harvest_index.percent / 100.0;
        } else {
            self.advance_reproductive_yield(
                day_after_planting,
                rooting_depth,
                reference_et,
                temperatures.0,
                temperatures.1,
                daily_harvest_index,
            )?;
        }
        Ok(
            (self.state.biomass.actual > 0.0 && self.state.dry_yield > 0.0)
                .then(|| 100.0 * self.state.dry_yield / self.state.biomass.actual),
        )
    }

    fn apply_transpiration_canopy_feedback(&mut self, extraction: &RootExtraction) {
        if self.state.canopy_cover - self.state.previous_reported_canopy_cover > 0.005
            && extraction.actual_transpiration < f64::EPSILON
        {
            self.state.canopy_cover = self.state.previous_reported_canopy_cover;
        }
    }

    fn advance_reproductive_yield(
        &mut self,
        day_after_planting: i32,
        rooting_depth: f64,
        reference_et: f64,
        minimum_temperature: f64,
        maximum_temperature: f64,
        daily_harvest_index: &DailyHarvestIndex,
    ) -> Result<(), Error> {
        let crop_day = day_after_planting - 1;
        let day_in_yield_formation = crop_day - self.run.crop.cycle.calendar_days().flowering;
        if day_in_yield_formation <= 0 || rooting_depth <= crate::compat::ZERO_THRESHOLD {
            return Ok(());
        }
        self.initialize_pre_yield_harvest_index_multiplier();
        let relative_depletion = self.relative_root_zone_depletion(rooting_depth)?;
        self.advance_pollination_harvest_index(
            day_in_yield_formation,
            relative_depletion,
            minimum_temperature,
            maximum_temperature,
        );
        let (leaf_response, stomatal_response) = self.advance_post_yield_harvest_index_stress(
            day_in_yield_formation,
            relative_depletion,
            reference_et,
        );
        let productivity = &self.run.crop.parameters.productivity;
        let leaf_enabled = productivity.vegetative_growth_impact_coefficient.is_some();
        let stomatal_enabled = productivity.stomatal_closure_impact_coefficient.is_some();
        let post_yield_multiplier = post_yield_harvest_index_multiplier(
            &self.state.post_yield_stress,
            leaf_response,
            stomatal_response,
            leaf_enabled,
            stomatal_enabled,
        );
        let total_multiplier = total_harvest_index_multiplier(
            self.state.pre_yield_harvest_index_multiplier.unwrap_or(1.0),
            post_yield_multiplier,
            productivity.maximum_harvest_index_increase.unwrap_or(0),
        );
        self.state.dry_yield = dry_yield(
            self.state.biomass.actual,
            daily_harvest_index
                .percent
                .min(self.state.pollination_harvest_index),
            f64::from(self.state.harvest_index.attainable_limit),
            total_multiplier,
        );
        Ok(())
    }

    fn initialize_pre_yield_harvest_index_multiplier(&mut self) {
        if self.state.pre_yield_harvest_index_multiplier.is_some() {
            return;
        }
        let mut multiplier = if self.state.biomass.potential < 0.000_1 {
            1.0
        } else {
            let relative_biomass =
                (self.state.biomass.actual / self.state.biomass.potential).min(1.0);
            let increase = self
                .run
                .crop
                .parameters
                .productivity
                .pre_flowering_harvest_index_increase;
            increase.map_or(1.0, |increase| {
                pre_yield_harvest_index_multiplier(
                    relative_biomass,
                    pre_yield_biomass_range(i32::from(increase)),
                    increase,
                )
            })
        };
        if self.state.canopy_cover <= 0.01 {
            multiplier = if self.state.withered_canopy_cover > 0.0
                && self.state.canopy_cover < self.state.withered_canopy_cover
            {
                0.0
            } else {
                1.0
            };
        }
        self.state.pre_yield_harvest_index_multiplier = Some(multiplier);
    }

    fn advance_pollination_harvest_index(
        &mut self,
        day_in_yield_formation: i32,
        relative_depletion: f64,
        minimum_temperature: f64,
        maximum_temperature: f64,
    ) {
        if self.run.crop.parameters.kind == CropKind::Tuber {
            self.state.pollination_harvest_index = f64::from(
                self.run
                    .crop
                    .parameters
                    .productivity
                    .reference_harvest_index,
            );
            return;
        }
        if self.run.crop.parameters.kind != CropKind::Grain
            || day_in_yield_formation > self.run.crop.cycle.calendar_days().flowering_length
            || 100.0 * self.state.canopy_cover
                <= f64::from(self.inputs.parameters.minimum_canopy_for_harvest_index)
        {
            return;
        }
        let pollination_stress = pollination_stress_coefficient(
            relative_depletion,
            self.run.crop.parameters.water_stress.pollination_upper,
            minimum_temperature,
            maximum_temperature,
            self.run
                .crop
                .parameters
                .temperature
                .pollination_minimum
                .map(f64::from),
            self.run
                .crop
                .parameters
                .temperature
                .pollination_maximum
                .map(f64::from),
        );
        self.state.pollination_harvest_index += pollination_stress
            * (1.0
                + self
                    .run
                    .crop
                    .parameters
                    .development
                    .potential_excess_fruits
                    .map_or(0.0, |value| f64::from(value) / 100.0))
            * flowering_fraction(
                day_in_yield_formation,
                self.run.crop.cycle.calendar_days().flowering_length,
            )
            * f64::from(
                self.run
                    .crop
                    .parameters
                    .productivity
                    .reference_harvest_index,
            );
        self.state.pollination_harvest_index = self.state.pollination_harvest_index.min(f64::from(
            self.run
                .crop
                .parameters
                .productivity
                .reference_harvest_index,
        ));
    }

    fn advance_post_yield_harvest_index_stress(
        &mut self,
        day_in_yield_formation: i32,
        relative_depletion: f64,
        reference_et: f64,
    ) -> (i32, i32) {
        let crop = &self.run.crop;
        let calendar = crop.cycle.calendar_days();
        let leaf_response = if crop.parameters.development.determinacy_linked_to_flowering {
            roundc_i32(f64::from(calendar.flowering_length) / 2.0)
        } else {
            calendar.senescence - calendar.flowering
        };
        let stomatal_response = harvest_index_rate(crop).map_or(0, |rate| {
            if rate > 99.0 {
                0
            } else {
                roundc_i32(f64::from(crop.parameters.productivity.reference_harvest_index) / rate)
            }
        });
        let effects_allowed = self
            .state
            .pre_yield_harvest_index_multiplier
            .is_some_and(|multiplier| multiplier > 0.99)
            && self.state.canopy_cover > 0.001 - f64::EPSILON;
        let adjustment = |threshold| {
            if crop.parameters.depletion_adjustment == DepletionAdjustment::Fao {
                adjust_depletion_threshold_for_eto(
                    threshold,
                    reference_et,
                    self.inputs.parameters.depletion_et_adjustment_factor,
                )
            } else {
                threshold
            }
        };
        let leaf_stress = stress_coefficient(
            relative_depletion,
            adjustment(crop.parameters.water_stress.canopy_expansion_upper),
            adjustment(crop.parameters.water_stress.canopy_expansion_lower),
            crop.parameters.water_stress.canopy_expansion_shape,
        );
        let stomatal_stress = stress_coefficient(
            relative_depletion,
            adjustment(crop.parameters.water_stress.stomatal_closure_upper),
            1.0,
            crop.parameters.water_stress.stomatal_closure_shape,
        );
        let leaf_impact = effects_allowed
            .then_some(
                crop.parameters
                    .productivity
                    .vegetative_growth_impact_coefficient,
            )
            .flatten();
        let stomatal_impact = effects_allowed
            .then_some(
                crop.parameters
                    .productivity
                    .stomatal_closure_impact_coefficient,
            )
            .flatten();
        advance_post_yield_stress(
            &mut self.state.post_yield_stress,
            &PostYieldStressInputs {
                day_in_yield_formation,
                leaf_response,
                stomatal_response,
                leaf_stress_coefficient: leaf_stress,
                stomatal_stress_coefficient: stomatal_stress,
                leaf_impact_coefficient: leaf_impact,
                stomatal_impact_coefficient: stomatal_impact,
            },
        );
        (leaf_response, stomatal_response)
    }

    fn relative_root_zone_depletion(&self, rooting_depth: f64) -> Result<f64, Error> {
        let water = determine_root_zone_water(
            &self.inputs.soil,
            &self.state.compartments,
            rooting_depth,
            f64::from(self.inputs.parameters.upper_soil_depth) / 100.0,
            &DepletionThresholds {
                leaf_expansion: self.run.crop.parameters.water_stress.canopy_expansion_upper,
                stomatal_closure: self.run.crop.parameters.water_stress.stomatal_closure_upper,
                senescence: self.run.crop.parameters.water_stress.senescence_upper,
            },
        )?;
        let (actual, field_capacity, wilting_point) = if water.upper_zone_is_wetter {
            (
                water.upper_actual,
                water.upper_field_capacity,
                water.upper_wilting_point,
            )
        } else {
            (water.actual, water.field_capacity, water.wilting_point)
        };
        Ok((field_capacity - actual) / (field_capacity - wilting_point))
    }

    fn advance_cutting_interval(&mut self, daily_gdd: f64) {
        if self.inputs.management.cuttings.considered {
            advance_cutting_interval(&mut self.state.cutting, daily_gdd);
        }
    }

    fn cutting_due(&mut self, day_number: i32, day_after_planting: i32, daily_gdd: f64) -> bool {
        let management = &self.inputs.management.cuttings;
        if !management.considered || day_after_planting < 1 {
            return false;
        }
        let inputs = CuttingInputs {
            day_in_season: day_after_planting,
            daily_gdd,
            biomass: self.state.biomass.actual,
            dry_yield: self.state.dry_yield,
            dry_matter: self.run.crop.parameters.development.dry_matter,
        };
        if management.generated {
            while management
                .records
                .get(self.state.cutting_record_index + 1)
                .is_some_and(|next| {
                    day_after_planting >= next.from_day.max(management.first_window_day)
                })
            {
                self.state.cutting_record_index += 1;
            }
            let Some(record) = management.records.get(self.state.cutting_record_index) else {
                return false;
            };
            let through_day = management
                .records
                .get(self.state.cutting_record_index + 1)
                .map_or_else(
                    || {
                        management.window_length.map_or(
                            self.run.crop.cycle.calendar_days().maturity,
                            |window_length| management.first_window_day + window_length - 1,
                        )
                    },
                    |next| next.from_day - 1,
                );
            record.target.is_some_and(|target| {
                generated_cutting_due(
                    &self.state.cutting,
                    &inputs,
                    &management.criterion,
                    target,
                    record.from_day.max(management.first_window_day),
                    through_day,
                )
            })
        } else {
            fixed_cutting_due(
                management,
                self.state.cutting_record_index,
                day_number,
                self.run.first_crop_day_number,
            )
        }
    }

    fn advance_cutting(
        &mut self,
        day_after_planting: i32,
        daily_gdd: f64,
        cutting_today: bool,
    ) -> Option<CuttingResult> {
        if !cutting_today {
            return None;
        }
        let management = &self.inputs.management.cuttings;
        let inputs = CuttingInputs {
            day_in_season: day_after_planting,
            daily_gdd,
            biomass: self.state.biomass.actual,
            dry_yield: self.state.dry_yield,
            dry_matter: self.run.crop.parameters.development.dry_matter,
        };
        let result = CuttingResult {
            number: self.state.cutting.count + 1,
            interval: self.state.cutting.days,
            biomass: inputs.biomass - self.state.cutting.previous_biomass,
            cumulative_biomass: inputs.biomass,
            dry_yield: inputs.dry_yield - self.state.cutting.previous_yield,
            cumulative_dry_yield: inputs.dry_yield,
        };
        record_cutting(
            &mut self.state.cutting,
            &inputs,
            &mut self.state.canopy_cover,
            management.canopy_cover_after_cutting,
        );
        self.state.withered_canopy_cover = 0.0;
        self.state.unrestricted_withered_canopy_cover = 0.0;
        self.state.adjusted_maximum_canopy_cover = self.state.canopy_cover;
        if !management.generated {
            self.state.cutting_record_index += 1;
        }
        Some(result)
    }

    fn daily_unrestricted_transpiration(
        &mut self,
        day_after_planting: i32,
        reference_et: f64,
        carbon_dioxide: f64,
        daily_gdd: f64,
    ) -> Result<(f64, i32), Error> {
        let calendar = self.run.crop.cycle.calendar_days();
        let canopy_cover = potential_canopy_cover_with_timing_and_limits(
            &self.run.crop,
            &self.canopy_timing,
            day_after_planting,
            0,
            self.state.sum_gdd,
            0,
            0,
            self.state.unrestricted_initial_canopy_cover,
            self.state.unrestricted_maximum_canopy_cover,
        )
        .max(0.0);
        self.state.unrestricted_withered_canopy_cover = self
            .state
            .unrestricted_withered_canopy_cover
            .max(canopy_cover);
        let potential_day = if self.run.crop.cycle.uses_growing_degree_days() {
            calendar_days_for_gdd(
                &self.inputs,
                &self.run,
                roundc_i32(adjusted_canopy_gdd_with_timing(
                    &self.run.crop,
                    &self.canopy_timing,
                    self.state.sum_gdd,
                )),
                self.run.first_crop_day_number,
            )?
        } else {
            adjusted_canopy_day_with_timing(
                &self.run.crop,
                &self.canopy_timing,
                day_after_planting - 1,
            )
        };
        let transpiration = potential_et(&PotentialEtParameters {
            days_after_planting: potential_day,
            delayed_days: 0,
            days_to_germination: calendar.emergence,
            days_to_full_canopy: self.canopy_timing.days_to_full_canopy,
            days_to_senescence: calendar.senescence,
            days_to_harvest: calendar.maturity,
            day_last_cut: 0,
            canopy_cover,
            reference_et,
            crop_coefficient: self.run.crop.parameters.evapotranspiration.kc_top,
            coefficient_decline: self
                .run
                .crop
                .parameters
                .evapotranspiration
                .kc_decline_cumulative,
            maximum_canopy_cover: self.state.unrestricted_maximum_canopy_cover,
            withered_canopy_cover: self.state.unrestricted_withered_canopy_cover,
            late_evaporation_effect: f64::from(
                self.run
                    .crop
                    .parameters
                    .evapotranspiration
                    .late_season_evaporation_reduction,
            ),
            carbon_dioxide,
            growing_degree_days: daily_gdd,
            cold_stress_threshold_gdd: self.run.crop.parameters.temperature.full_transpiration_gdd,
            wet_bare_soil_coefficient: self.inputs.parameters.wet_bare_soil_coefficient,
            senescence_exponent: self.inputs.parameters.senescence_exponent,
            evaporation_limit_enabled: false,
        })
        .transpiration;
        Ok((transpiration, potential_day))
    }

    fn update_dynamic_fertility(&mut self, day_after_planting: i32) -> Result<(), Error> {
        let calendar = self.run.crop.cycle.calendar_days();
        if day_after_planting < 1 || day_after_planting > calendar.maturity {
            return Ok(());
        }
        let Some(calibration) = self.state.fertility_effects.calibration.as_ref() else {
            return Ok(());
        };
        let selected = self.inputs.management.fertility_stress;
        let mut new_stress = if self.state.biomass.unrestricted > 0.001 {
            let relative_biomass = self.state.biomass.total / self.state.biomass.unrestricted;
            let adjusted_biomass = 100.0
                * (calibration.attainable_biomass_fraction
                    + (calibration.attainable_biomass_fraction - relative_biomass));
            let candidate = if adjusted_biomass >= 100.0 {
                0
            } else if adjusted_biomass <= f64::EPSILON {
                80
            } else {
                let fitted = roundc_i32(
                    calibration.stress_intercept
                        + calibration.stress_linear * adjusted_biomass
                        + calibration.stress_quadratic * adjusted_biomass * adjusted_biomass,
                );
                if fitted < 0 { selected } else { fitted.min(80) }
            };
            candidate.min(selected)
        } else {
            selected
        };
        let vegetation_limit_days = if self
            .run
            .crop
            .parameters
            .development
            .determinacy_linked_to_flowering
        {
            roundc_i32(f64::from(calendar.flowering_length) / 2.0)
        } else {
            calendar.senescence - calendar.flowering
        };
        if self.run.crop.parameters.kind == CropKind::Grain
            && self
                .run
                .crop
                .parameters
                .development
                .determinacy_linked_to_flowering
            && day_after_planting > calendar.flowering + vegetation_limit_days + 1
        {
            new_stress = new_stress
                .max(self.state.fertility_effects.current_stress)
                .min(selected);
        }
        let seasonal_potential = self
            .state
            .fertility_effects
            .seasonal_potential_transpiration;
        self.state.fertility_effects.previous_stress = self.state.fertility_effects.current_stress;
        self.state
            .fertility_effects
            .normalized_potential_transpiration =
            (1.0 - f64::from(new_stress) / 100.0) * seasonal_potential;
        self.state.fertility_effects.update_for_stress(
            &self.inputs,
            &self.run,
            new_stress,
            &self.state.salinity_effects,
        )
    }

    fn advance_rooting(
        &self,
        day_after_planting: i32,
        daily_gdd: f64,
    ) -> crate::rooting::RootingResult {
        if day_after_planting < 1 {
            return crate::rooting::RootingResult {
                depth: 0.0,
                unrestricted_depth: 0.0,
                density_correction: 1.0,
            };
        }
        advance_rooting_depth(
            &self.run.crop,
            &self.inputs.soil,
            &self.state.compartments,
            self.state.root_depth,
            &RootingTime {
                day_after_planting,
                delayed_days: self.state.delayed_days,
                previous_sum_gdd: self.state.previous_sum_gdd,
                sum_gdd: self.state.sum_gdd,
                daily_gdd,
            },
            &RootingStress {
                potential_canopy: 100.0 * self.state.potential_canopy_cover,
                actual_canopy: 100.0 * self.state.canopy_cover,
                potential_transpiration: self.state.previous_potential_transpiration,
                actual_transpiration: self.state.previous_transpiration,
                cumulative_potential_transpiration: self.state.cumulative_potential_transpiration,
                cumulative_actual_transpiration: self.state.cumulative_transpiration,
                root_shape_factor: Some(self.inputs.parameters.root_stress_shape),
                germinated: self.state.germinated,
            },
            self.inputs.parameters.initial_root_depth,
        )
    }

    fn calculate_runoff(&mut self, rain: f64) -> Result<f64, Error> {
        if !self.inputs.management.runoff_enabled {
            return Ok(0.0);
        }
        let corrected = corrected_curve_number(
            self.inputs.soil.curve_number,
            self.inputs.management.curve_number_correction,
        );
        let daily_curve_number = if self.inputs.parameters.antecedent_moisture_correction {
            let wetness = relative_wetness_topsoil(
                &self.inputs.soil,
                &mut self.state.compartments,
                self.inputs.parameters.runoff_depth,
            )?;
            curve_number_for_wetness(corrected, wetness)
        } else {
            corrected
        };
        runoff(
            rain,
            daily_curve_number,
            INITIAL_ABSTRACTION,
            &RainfallAggregation::Daily,
        )
    }

    fn root_zone_stomatal_stress(
        &mut self,
        rooting_depth: f64,
        stomatal_depletion: f64,
    ) -> Result<f64, Error> {
        let water = determine_root_zone_water(
            &self.inputs.soil,
            &self.state.compartments,
            rooting_depth,
            f64::from(self.inputs.parameters.upper_soil_depth) / 100.0,
            &DepletionThresholds {
                leaf_expansion: self.run.crop.parameters.water_stress.canopy_expansion_upper,
                stomatal_closure: stomatal_depletion,
                senescence: self.run.crop.parameters.water_stress.senescence_upper,
            },
        )?;
        let (actual, field_capacity, wilting_point, threshold) = if water.upper_zone_is_wetter {
            (
                water.upper_actual,
                water.upper_field_capacity,
                water.upper_wilting_point,
                water.upper_stomatal_threshold,
            )
        } else {
            (
                water.actual,
                water.field_capacity,
                water.wilting_point,
                water.stomatal_threshold,
            )
        };
        let water_coefficient = if actual >= 0.999 * threshold {
            1.0
        } else {
            let relative_depletion = (field_capacity - actual) / (field_capacity - wilting_point);
            stress_coefficient(relative_depletion, stomatal_depletion, 1.0, 0.0)
        };
        let incoming = water_coefficient
            * (1.0 - f64::from(self.state.salinity_effects.stomatal_reduction) / 100.0);
        let salinity_coefficient = if let (Some(calibration), Some(salinity)) = (
            self.state.salinity_effects.calibration.as_ref(),
            self.state.salinity_effects.root_zone.as_ref(),
        ) {
            let relative_depletion = (water.field_capacity - water.actual)
                / (water.field_capacity - water.wilting_point);
            adjust_stomatal_for_soil_water_salinity(
                self.run.crop.parameters.salinity_stress.ece_minimum,
                self.run.crop.parameters.salinity_stress.ece_maximum,
                self.run.crop.parameters.salinity_stress.stomatal_response,
                salinity.extract_conductivity,
                salinity.water_conductivity,
                salinity.field_capacity_conductivity,
                relative_depletion,
                calibration.reduction_intercept,
                calibration.reduction_linear,
                calibration.reduction_quadratic,
                incoming,
            )
        } else {
            incoming
        };
        let anaerobic_coefficient = advance_root_zone_anaerobic_stress(
            water.saturation,
            water.actual,
            self.run.crop.parameters.water_stress.anaerobic_point_volume,
            rooting_depth,
            self.inputs.parameters.deficient_aeration_delay,
            &mut self.state.anaerobic_exposure,
        );
        self.state.anaerobic_stress_coefficient = anaerobic_coefficient;
        Ok(anaerobic_coefficient * salinity_coefficient)
    }

    fn local_root_stress(&mut self, stomatal_depletion: f64) -> Result<Vec<f64>, Error> {
        let calibration = self
            .state
            .salinity_effects
            .calibration
            .as_ref()
            .map(|calibration| {
                (
                    calibration.reduction_intercept,
                    calibration.reduction_linear,
                    calibration.reduction_quadratic,
                )
            });
        let mut stress = Vec::with_capacity(self.state.compartments.len());
        for (index, compartment) in self.state.compartments.iter_mut().enumerate() {
            let layer = self.inputs.soil.layers.get(compartment.layer_index).ok_or(
                crate::water_balance::WaterBalanceError::MissingLayer {
                    compartment: index,
                    layer: compartment.layer_index,
                },
            )?;
            let water_coefficient = local_root_water_stress(
                compartment,
                layer,
                stomatal_depletion,
                self.run.crop.parameters.water_stress.stomatal_closure_shape,
                f64::from(self.state.salinity_effects.stomatal_reduction),
            );
            let salinity_coefficient = if let Some((intercept, linear, quadratic)) = calibration {
                let field_capacity = layer.field_capacity / 100.0;
                let wilting_point = layer.wilting_point / 100.0;
                let relative_depletion =
                    (field_capacity - compartment.theta) / (field_capacity - wilting_point);
                adjust_stomatal_for_soil_water_salinity(
                    self.run.crop.parameters.salinity_stress.ece_minimum,
                    self.run.crop.parameters.salinity_stress.ece_maximum,
                    self.run.crop.parameters.salinity_stress.stomatal_response,
                    saturated_extract_conductivity(
                        compartment,
                        layer,
                        f64::from(self.inputs.parameters.salt_solubility),
                    ),
                    soil_water_conductivity(
                        compartment,
                        layer,
                        f64::from(self.inputs.parameters.salt_solubility),
                    ),
                    field_capacity_water_conductivity(
                        compartment,
                        layer,
                        f64::from(self.inputs.parameters.salt_solubility),
                    ),
                    relative_depletion,
                    intercept,
                    linear,
                    quadratic,
                    water_coefficient,
                )
            } else {
                water_coefficient
            };
            stress.push(advance_local_anaerobic_stress(
                compartment,
                layer,
                self.run.crop.parameters.water_stress.anaerobic_point_volume,
                self.inputs.parameters.deficient_aeration_delay,
                self.state.submerged_exposure,
                salinity_coefficient,
            ));
        }
        Ok(stress)
    }

    fn crop_stage(&self, day_number: i32, canopy_cover: f64) -> CropStage {
        let virtual_day = day_number - self.state.delayed_days - self.run.first_crop_day_number;
        let calendar = self.run.crop.cycle.calendar_days();
        if virtual_day < 0 || virtual_day >= self.effective_cycle_length {
            return CropStage::OutsideCropCycle;
        }
        if virtual_day < calendar.emergence {
            return CropStage::Establishment;
        }
        if virtual_day > calendar.emergence && canopy_cover < f64::EPSILON {
            return CropStage::NoGrowth;
        }
        if self.run.crop.parameters.kind == CropKind::Grain && virtual_day >= calendar.flowering {
            if virtual_day < calendar.flowering + calendar.flowering_length {
                return CropStage::Flowering;
            }
            return CropStage::YieldFormation;
        }
        if self.run.crop.parameters.kind == CropKind::Tuber && virtual_day >= calendar.flowering {
            return CropStage::YieldFormation;
        }
        CropStage::Vegetative
    }

    fn output_root_zone_water(
        &mut self,
        day_after_planting: i32,
        depth: f64,
        reference_et: f64,
    ) -> Result<Option<RootZoneWater>, Error> {
        if depth <= crate::compat::ZERO_THRESHOLD {
            return Ok(None);
        }
        let crop = &self.run.crop;
        // Leaf and senescence thresholds change only during the active crop cycle.
        let (emerged, past_maturity) = if crop.cycle.uses_growing_degree_days() {
            let calendar = crop.cycle.driving_calendar();
            (
                self.state.sum_gdd > f64::from(calendar.emergence),
                roundc_i32(self.state.sum_gdd) > calendar.maturity,
            )
        } else {
            let calendar = crop.cycle.calendar_days();
            (
                day_after_planting > calendar.emergence,
                day_after_planting > calendar.maturity,
            )
        };
        let active = emerged && !past_maturity;
        let raw_leaf_expansion = crop.parameters.water_stress.canopy_expansion_upper;
        let raw_senescence = crop.parameters.water_stress.senescence_upper;
        let fao_adjusted = |threshold| {
            adjust_depletion_threshold_for_eto(
                threshold,
                reference_et,
                self.inputs.parameters.depletion_et_adjustment_factor,
            )
        };
        let (leaf_expansion, senescence) =
            if crop.parameters.depletion_adjustment != DepletionAdjustment::Fao {
                (raw_leaf_expansion, raw_senescence)
            } else if active {
                let adjusted = (
                    fao_adjusted(raw_leaf_expansion),
                    fao_adjusted(raw_senescence),
                );
                self.state.frozen_water_stress_thresholds = Some(adjusted);
                adjusted
            } else if past_maturity {
                self.state
                    .frozen_water_stress_thresholds
                    .unwrap_or((raw_leaf_expansion, raw_senescence))
            } else {
                (raw_leaf_expansion, raw_senescence)
            };
        let stomatal_closure = if crop.parameters.depletion_adjustment == DepletionAdjustment::Fao {
            fao_adjusted(crop.parameters.water_stress.stomatal_closure_upper)
        } else {
            crop.parameters.water_stress.stomatal_closure_upper
        };
        determine_root_zone_water(
            &self.inputs.soil,
            &self.state.compartments,
            depth,
            f64::from(self.inputs.parameters.upper_soil_depth) / 100.0,
            &DepletionThresholds {
                leaf_expansion,
                stomatal_closure,
                senescence,
            },
        )
        .map(Some)
    }

    fn daily_compartment_results(&self) -> Result<Vec<DailyCompartmentResult>, Error> {
        self.state
            .compartments
            .iter()
            .enumerate()
            .map(|(index, compartment)| {
                let layer = self.inputs.soil.layers.get(compartment.layer_index).ok_or(
                    crate::water_balance::WaterBalanceError::MissingLayer {
                        compartment: index,
                        layer: compartment.layer_index,
                    },
                )?;
                Ok(DailyCompartmentResult {
                    thickness: compartment.thickness,
                    theta: compartment.theta,
                    extract_conductivity: saturated_extract_conductivity(
                        compartment,
                        layer,
                        f64::from(self.inputs.parameters.salt_solubility),
                    ),
                    drainage: compartment.drainage,
                    adjusted_field_capacity: compartment.adjusted_field_capacity,
                    layer_index: compartment.layer_index,
                })
            })
            .collect()
    }

    fn crop_and_weed_cover(&self, day_after_planting: i32, total_cover: f64) -> (f64, f64) {
        if self.state.weed_relative_cover <= 0.0 || total_cover <= 0.0 {
            return (total_cover, 0.0);
        }
        let (development_time, full_canopy_time, senescence_time) =
            if self.run.crop.cycle.uses_growing_degree_days() {
                (
                    adjusted_canopy_gdd_with_timing(
                        &self.run.crop,
                        &self.canopy_timing,
                        self.state.sum_gdd,
                    ),
                    f64::from(self.state.fertility_effects.gdd_to_full_canopy),
                    f64::from(self.run.crop.cycle.driving_calendar().senescence),
                )
            } else {
                (
                    f64::from(day_after_planting - 1),
                    f64::from(
                        self.state
                            .fertility_effects
                            .canopy_timing
                            .days_to_full_canopy,
                    ),
                    f64::from(self.run.crop.cycle.calendar_days().senescence),
                )
            };
        let weed_cover = relative_weed_cover(
            self.state.weed_relative_cover,
            self.run.weed_cover_delta,
            development_time,
            full_canopy_time,
            senescence_time,
        );
        (total_cover * (1.0 - weed_cover / 100.0), weed_cover)
    }

    fn crop_transpiration(&self, day_after_planting: i32, total_transpiration: f64) -> f64 {
        let total_cover = self.state.canopy_cover;
        let (crop_cover, weed_cover) = self.crop_and_weed_cover(day_after_planting, total_cover);
        if weed_cover <= 0.0 {
            return total_transpiration;
        }
        let adjusted_total =
            (1.72 * total_cover - total_cover.powi(2) + 0.30 * total_cover.powi(3)).clamp(0.0, 1.0);
        if adjusted_total <= 0.0001 || crop_cover <= 0.0001 {
            0.0
        } else {
            let adjusted_crop = crop_cover + adjusted_total - total_cover;
            total_transpiration * adjusted_crop / adjusted_total
        }
    }

    fn advance_biomass(
        &mut self,
        day_after_planting: i32,
        reference_et: f64,
        carbon_dioxide: f64,
        unrestricted: (f64, i32),
        daily_harvest_index: &DailyHarvestIndex,
        extraction: &crate::transpiration::RootExtraction,
    ) -> (DailyWaterProductivity, crate::perennial::AssimilateFlux) {
        let (unrestricted_transpiration, unrestricted_virtual_canopy_day) = unrestricted;
        let fertility = &self.state.fertility_effects;
        let biomass_fraction = fertility
            .calibration
            .as_ref()
            .map_or(1.0, |prepared| prepared.attainable_biomass_fraction);
        let biomass_before = self.state.biomass.actual;
        let productivity = advance_biomass(
            &mut self.state.biomass,
            &self.run.crop.parameters.productivity,
            &BiomassInputs {
                reference_et,
                actual_transpiration: extraction.actual_transpiration,
                crop_transpiration: extraction.actual_transpiration,
                unrestricted_transpiration,
                co2: carbon_dioxide,
                actual_yield_formation_switch: actual_yield_formation_switch(
                    &self.run.crop,
                    day_after_planting - 1,
                    daily_harvest_index,
                ),
                unrestricted_yield_formation_switch: unrestricted_yield_formation_switch(
                    &self.run.crop,
                    unrestricted_virtual_canopy_day,
                ),
                fertility_wp_reduction: f64::from(fertility.water_productivity_reduction),
                normalized_fertility_potential_transpiration: fertility
                    .normalized_potential_transpiration,
                fertility_biomass_fraction: biomass_fraction,
            },
        );
        let daily_biomass = self.state.biomass.actual - biomass_before;
        let previous_fertility_stress = f64::from(fertility.previous_stress) / 100.0;
        let previous_maximum_cover_reduction = roundc_i8(
            100.0
                * (1.0
                    - stress_coefficient(
                        previous_fertility_stress,
                        0.0,
                        1.0,
                        self.run
                            .crop
                            .parameters
                            .fertility_stress
                            .maximum_canopy_shape,
                    )),
        );
        let flux = transfer_assimilates(
            &mut self.state.assimilate_transfer,
            &self.run.crop.parameters.assimilate_transfer,
            &AssimilateTransferInputs {
                virtual_day: day_after_planting,
                maturity_day: self.run.crop.cycle.calendar_days().maturity,
                canopy_cover: self.state.previous_reported_canopy_cover,
                maximum_canopy_cover: self.run.crop.parameters.canopy.maximum_cover,
                maximum_canopy_reduction: f64::from(previous_maximum_cover_reduction),
                cutting_canopy_cover: f64::from(
                    self.inputs.management.cuttings.canopy_cover_after_cutting,
                ) / 100.0,
                cutting_previous_day: self.state.cutting.last_cut_day == day_after_planting - 1,
                no_more_crop: false,
            },
            daily_biomass,
        );
        let transfer = flux.into_biomass - flux.out_of_biomass;
        self.state.biomass.actual += transfer;
        self.state.biomass.total += transfer;
        self.state.biomass.unrestricted += transfer;
        self.state.biomass.potential = biomass_fraction * self.state.biomass.unrestricted;
        (productivity, flux)
    }
}

fn prepare_daily_inputs(
    first_day_number: i32,
    weather: Vec<DailyWeather>,
    external_irrigation: Option<Vec<DailyIrrigation>>,
    groundwater: Option<Vec<Groundwater>>,
) -> Result<Vec<PreparedDailyInput>, Error> {
    if external_irrigation
        .as_ref()
        .is_some_and(|irrigation| irrigation.len() != weather.len())
    {
        return Err(Error::InvalidSimulationConfiguration(
            "external irrigation must contain one value for every weather day",
        ));
    }
    if groundwater
        .as_ref()
        .is_some_and(|groundwater| groundwater.len() != weather.len())
    {
        return Err(Error::InvalidSimulationConfiguration(
            "groundwater must contain one value for every weather day",
        ));
    }
    let mut external_irrigation = external_irrigation.map(Vec::into_iter);
    let mut groundwater = groundwater.map(Vec::into_iter);
    weather
        .into_iter()
        .enumerate()
        .map(|(offset, weather)| {
            let offset = i32::try_from(offset)
                .map_err(|_| Error::InvalidSimulationConfiguration("weather series is too long"))?;
            let day_number = first_day_number.checked_add(offset).ok_or(
                Error::InvalidSimulationConfiguration("simulation day range overflows"),
            )?;
            Ok(PreparedDailyInput {
                day_number,
                weather,
                external_irrigation: external_irrigation.as_mut().and_then(Iterator::next),
                groundwater: groundwater.as_mut().and_then(Iterator::next),
                infiltration_override: None,
            })
        })
        .collect()
}

fn calibration_carbon_dioxide(inputs: &[PreparedDailyInput]) -> Result<f64, Error> {
    let carbon_dioxide = inputs
        .first()
        .ok_or(Error::InvalidSimulationConfiguration(
            "weather series cannot be empty",
        ))?
        .weather
        .carbon_dioxide;
    let rounded = roundc_i32(100.0 * carbon_dioxide);
    if inputs
        .iter()
        .any(|input| roundc_i32(100.0 * input.weather.carbon_dioxide) != rounded)
    {
        return Err(Error::InvalidSimulationConfiguration(
            "calibrated fertility stress requires constant daily CO2",
        ));
    }
    Ok(carbon_dioxide)
}

fn validate_config(inputs: &PreparedInputs, run: &RunConfig) -> Result<(), Error> {
    if inputs.season_year <= 0 {
        return Err(Error::InvalidSimulationConfiguration(
            "season year must be positive",
        ));
    }
    if run.first_day_number > run.last_day_number {
        return Err(Error::InvalidSimulationConfiguration(
            "first day must not follow last day",
        ));
    }
    if run.first_crop_day_number > run.last_crop_day_number {
        return Err(Error::InvalidSimulationConfiguration(
            "first crop day must not follow last crop day",
        ));
    }
    if run.crop.parameters.water_stress.anaerobic_point_volume > 0
        && inputs.parameters.deficient_aeration_delay <= 0
    {
        return Err(Error::InvalidSimulationConfiguration(
            "deficient-aeration delay must be positive when the crop responds to oxygen stress",
        ));
    }
    let expected_length = i64::from(run.last_day_number) - i64::from(run.first_day_number) + 1;
    if usize::try_from(expected_length)
        .ok()
        .is_none_or(|required| inputs.daily_inputs.len() < required)
    {
        return Err(Error::InvalidSimulationConfiguration(
            "weather must span the complete simulation period",
        ));
    }
    for input in &inputs.daily_inputs {
        let weather = &input.weather;
        let weather_values = [
            weather.minimum_temperature,
            weather.maximum_temperature,
            weather.reference_et,
            weather.rain,
            weather.carbon_dioxide,
        ];
        if weather_values.iter().any(|value| !value.is_finite())
            || weather.reference_et < 0.0
            || weather.rain < 0.0
            || weather.carbon_dioxide <= 0.0
            || input
                .external_irrigation
                .as_ref()
                .is_some_and(|irrigation| {
                    !irrigation.depth.is_finite()
                        || irrigation.depth < 0.0
                        || !irrigation.conductivity.is_finite()
                        || irrigation.conductivity < 0.0
                })
            || input.groundwater.as_ref().is_some_and(|groundwater| {
                !groundwater.depth.is_finite()
                    || groundwater.depth < 0.0
                    || !groundwater.conductivity.is_finite()
                    || groundwater.conductivity < 0.0
            })
        {
            return Err(Error::InvalidSimulationConfiguration(
                "daily weather or water input contains invalid values",
            ));
        }
    }
    Ok(())
}

fn adjust_premature_end(run: &mut RunConfig) -> Result<(), Error> {
    if run.crop.parameters.kind == CropKind::Forage {
        return Ok(());
    }

    let planting_year = CalendarDate::from_day_number(run.first_crop_day_number)?.year();
    let Some(premature_end) =
        premature_end_day_number(run.crop.parameters.premature_end_day, planting_year)?
    else {
        return Ok(());
    };
    let final_simulated_day =
        premature_end
            .checked_sub(1)
            .ok_or(Error::InvalidSimulationConfiguration(
                "premature crop end precedes the AquaCrop calendar",
            ))?;
    run.last_day_number = run.last_day_number.min(final_simulated_day);
    run.last_crop_day_number = run.last_crop_day_number.min(final_simulated_day);

    Ok(())
}

fn adjust_perennial_for_season(inputs: &PreparedInputs, run: &mut RunConfig) {
    let crop = &mut run.crop;
    if crop.parameters.kind != CropKind::Forage {
        return;
    }
    if inputs.season_year <= 1 {
        crop.parameters.planting_method = if crop.parameters.rooting.sown_in_first_year {
            PlantingMethod::Seed
        } else {
            PlantingMethod::Transplant
        };
        crop.parameters.rooting.minimum_depth = crop.parameters.rooting.first_year_minimum_depth;
        return;
    }
    crop.parameters.planting_method = PlantingMethod::Regrowth;
    crop.parameters.rooting.minimum_depth = crop.parameters.rooting.maximum_depth;
    let minimum_regrowth_size = 10.0 * crop.parameters.canopy.seedling_size;
    if roundc_i32(100.0 * crop.parameters.canopy.regrowth_plant_size)
        < roundc_i32(100.0 * crop.parameters.canopy.seedling_size)
    {
        crop.parameters.canopy.regrowth_plant_size = minimum_regrowth_size;
    }
    let population_per_square_metre = f64::from(crop.parameters.canopy.planting_density) / 10_000.0;
    let maximum_plant_size =
        crop.parameters.canopy.maximum_cover * 10_000.0 / population_per_square_metre;
    if roundc_i32(100.0 * crop.parameters.canopy.regrowth_plant_size)
        > roundc_i32(100.0 * maximum_plant_size)
    {
        crop.parameters.canopy.regrowth_plant_size = maximum_plant_size;
    }
    crop.parameters.canopy.initial_regrowth_cover =
        population_per_square_metre * crop.parameters.canopy.regrowth_plant_size / 10_000.0;
}

#[expect(
    clippy::too_many_lines,
    reason = "the source's coupled self-thinning and weed-replacement branches remain auditable"
)]
fn apply_perennial_and_weed_cover(
    inputs: &PreparedInputs,
    run: &mut RunConfig,
    timing: &CanopyTiming,
    fertility: &FertilityEffects,
) -> (f64, f64) {
    let crop = &mut run.crop;
    let original_maximum = crop.parameters.canopy.maximum_cover;
    let original_seedling = crop.parameters.canopy.initial_seedling_cover;
    let perennial = crop.parameters.kind == CropKind::Forage;
    let self_thinning = crop
        .parameters
        .canopy
        .self_thinning_years
        .zip(crop.parameters.canopy.self_thinning_shape);
    let maximum_self_thinning = if perennial {
        self_thinning.map_or(1.0, |(years, shape)| {
            perennial_maximum_cover_multiplier(inputs.season_year, i32::from(years), shape)
        })
    } else {
        1.0
    };
    let initial_self_thinning = if perennial {
        self_thinning.map_or(1.0, |(years, shape)| {
            perennial_initial_cover_multiplier(inputs.season_year, i32::from(years), shape)
        })
    } else {
        1.0
    };
    let first_day_regrowth = if crop.parameters.planting_method == PlantingMethod::Regrowth {
        maximum_self_thinning
            * fertility_adjusted_potential_canopy_cover(
                crop,
                1,
                0,
                0.0,
                &fertility.canopy_timing,
                fertility.gdd_to_full_canopy,
                fertility.canopy_decline,
            )
    } else {
        0.0
    };
    let management = &inputs.management;
    let mut canopy_multiplier = weed_canopy_multiplier(
        management.weed_cover,
        original_maximum,
        management.weed_canopy_shape,
    );
    let mut relative_cover = management.weed_cover;
    let mut weed_replaces_thinned_cover = false;
    if !perennial && management.weed_cover > 0 && management.fertility_stress > 0 {
        canopy_multiplier = 1.0;
    }
    if perennial && management.weed_cover > 0 {
        if management.fertility_stress > 0 {
            canopy_multiplier = 1.0;
            weed_replaces_thinned_cover = true;
            if maximum_self_thinning > 0.005 {
                let replacement = f64::from(management.weed_self_thinning_replacement) / 100.0;
                let mut adjusted = roundc_i8(
                    f64::from(management.weed_cover)
                        + (1.0 - maximum_self_thinning)
                            * original_maximum
                            * (1.0
                                - f64::from(fertility.canopy_timing.maximum_cover_reduction)
                                    / 100.0)
                            * replacement,
                );
                let lower = roundc_i8(
                    100.0
                        * (1.0
                            - maximum_self_thinning
                                / (maximum_self_thinning
                                    + (1.0 - maximum_self_thinning) * replacement)),
                );
                adjusted = adjusted.max(lower);
                relative_cover = if adjusted > 100 { 98 } else { adjusted };
            } else {
                relative_cover = 100;
            }
        } else {
            let adjustment = perennial_weed_adjustment(
                management.weed_cover,
                original_maximum,
                management.weed_canopy_shape,
                maximum_self_thinning,
                inputs.season_year,
                management.weed_self_thinning_replacement,
            );
            canopy_multiplier = adjustment.canopy_multiplier;
            relative_cover = adjustment.relative_cover;
        }
        if management.weed_cover_delta != 0 && maximum_self_thinning < 0.999 {
            run.weed_cover_delta = if maximum_self_thinning < 0.005 {
                0
            } else {
                roundc_i32(
                    f64::from(management.weed_cover_delta)
                        * maximum_self_thinning.powf(
                            1.0 + f64::from(management.weed_self_thinning_replacement) / 100.0,
                        ),
                )
            };
        }
    }
    let replacement = f64::from(management.weed_self_thinning_replacement) / 100.0;
    let replacement_enabled = if weed_replaces_thinned_cover {
        1.0
    } else {
        0.0
    };
    let maximum_base =
        maximum_self_thinning + replacement_enabled * (1.0 - maximum_self_thinning) * replacement;
    let initial_base =
        initial_self_thinning + replacement_enabled * (1.0 - initial_self_thinning) * replacement;
    let maximum = canopy_multiplier * original_maximum * maximum_base;
    let initial = canopy_multiplier * original_seedling * initial_base;
    let decline_denominator = original_maximum * maximum_base + 2.29;
    let decline_multiplier = (maximum + 2.29) / decline_denominator;
    crop.cycle.calendar_days_mut().canopy_decline_coefficient *= decline_multiplier;
    if let Some(gdd) = crop.cycle.gdd_mut() {
        gdd.canopy_decline_coefficient *= decline_multiplier;
    }
    crop.parameters.canopy.initial_seedling_cover = initial;
    crop.parameters.canopy.maximum_cover = maximum;
    let regrowth = if crop.parameters.planting_method != PlantingMethod::Regrowth {
        0.0
    } else if (crop.cycle.uses_growing_degree_days() && timing.gdd_to_initial_cover.is_none())
        || (!crop.cycle.uses_growing_degree_days() && timing.days_to_initial_cover.is_none())
    {
        maximum
    } else if crop.cycle.uses_growing_degree_days() {
        canopy_at_time(
            f64::from(timing.gdd_to_initial_cover.unwrap_or(0)),
            initial,
            crop.cycle.driving_calendar().canopy_growth_coefficient,
            maximum,
        )
    } else {
        canopy_at_time(
            f64::from(timing.days_to_initial_cover.unwrap_or(0)),
            initial,
            crop.cycle.calendar_days().canopy_growth_coefficient,
            maximum,
        )
    };
    crop.parameters.canopy.initial_regrowth_cover = regrowth;
    (
        first_day_regrowth * canopy_multiplier,
        f64::from(relative_cover),
    )
}

fn first_cutting_record_index(inputs: &PreparedInputs, run: &RunConfig) -> usize {
    let cutting = &inputs.management.cuttings;
    if cutting.generated {
        return 0;
    }
    cutting
        .records
        .iter()
        .position(|record| {
            let day_number = cutting
                .first_list_day_number
                .unwrap_or(run.first_crop_day_number)
                + record.from_day
                - 1;
            day_number >= run.first_crop_day_number
        })
        .unwrap_or(cutting.records.len())
}

fn adjust_crop_period(inputs: &PreparedInputs, run: &mut RunConfig) -> Result<(), Error> {
    if run.crop.parameters.kind != CropKind::Forage {
        return Ok(());
    }
    let season_days = run
        .last_crop_day_number
        .checked_sub(run.first_crop_day_number)
        .and_then(|duration| duration.checked_add(1))
        .ok_or(Error::InvalidSimulationConfiguration(
            "crop-period duration overflows",
        ))?;
    let days_from_senescence_to_end = run.crop.cycle.calendar_days().senescence_to_maturity();
    let gdd_from_senescence_to_end = run.crop.cycle.driving_calendar().senescence_to_maturity();
    run.crop.cycle.calendar_days_mut().maturity = season_days;
    if run.crop.cycle.uses_growing_degree_days() {
        let gdd_to_maturity = cumulative_gdd_for_calendar_days(inputs, run, season_days)?;
        let gdd_to_senescence = gdd_to_maturity - gdd_from_senescence_to_end;
        let gdd = run
            .crop
            .cycle
            .gdd_mut()
            .ok_or(Error::InvalidCropConfiguration(
                "GDD crop cycle is missing its GDD calendar",
            ))?;
        gdd.maturity = gdd_to_maturity;
        gdd.senescence = gdd_to_senescence;
        if gdd_to_senescence >= gdd_to_maturity {
            run.crop
                .cycle
                .gdd_mut()
                .ok_or(Error::InvalidCropConfiguration(
                    "GDD crop cycle is missing its GDD calendar",
                ))?
                .senescence = gdd_to_maturity;
            run.crop.cycle.calendar_days_mut().senescence = season_days;
        } else {
            let days_to_senescence =
                calendar_days_for_gdd(inputs, run, gdd_to_senescence, run.first_crop_day_number)?;
            run.crop.cycle.calendar_days_mut().senescence = days_to_senescence;
        }
    } else {
        let days_to_senescence = season_days - days_from_senescence_to_end;
        run.crop.cycle.calendar_days_mut().senescence = if days_to_senescence >= season_days {
            season_days
        } else {
            days_to_senescence
        };
    }
    Ok(())
}

fn regrowth_initial_canopy_times(crop: &PreparedCrop, timing: &CanopyTiming) -> Option<(i32, i32)> {
    if crop.parameters.planting_method != PlantingMethod::Regrowth {
        return None;
    }
    timing
        .days_to_initial_cover
        .zip(timing.gdd_to_initial_cover)
        .filter(|(days, _)| *days > 0)
}

#[expect(
    clippy::too_many_lines,
    reason = "the conversion keeps all climate-dependent crop-calendar fields together"
)]
fn adjust_gdd_crop_calendar(
    inputs: &PreparedInputs,
    run: &mut RunConfig,
) -> Result<Option<i32>, Error> {
    if !run.crop.cycle.uses_growing_degree_days() {
        return Ok(None);
    }
    let supplied = run.crop.cycle.calendar_days().clone();
    let driving_calendar = run.crop.cycle.driving_calendar().clone();
    let timing = derive_canopy_timing(&run.crop);
    let full_canopy_gdd = timing.gdd_to_full_canopy.min(driving_calendar.maturity);
    let days_to_emergence = if run.crop.parameters.planting_method == PlantingMethod::Regrowth {
        supplied.emergence
    } else {
        calendar_days_for_gdd(
            inputs,
            run,
            driving_calendar.emergence,
            run.first_crop_day_number,
        )
        .unwrap_or(supplied.emergence)
    };
    let regrowth_initial = regrowth_initial_canopy_times(&run.crop, &timing);
    let days_to_full_canopy = if let Some((initial_days, initial_gdd)) = regrowth_initial {
        let remaining_gdd = full_canopy_gdd - driving_calendar.emergence - initial_gdd;
        supplied.emergence
            + initial_days
            + calendar_days_for_gdd(inputs, run, remaining_gdd, run.first_crop_day_number)
                .unwrap_or(timing.days_to_full_canopy - supplied.emergence - initial_days)
    } else {
        calendar_days_for_gdd(inputs, run, full_canopy_gdd, run.first_crop_day_number)
            .unwrap_or(timing.days_to_full_canopy)
    };
    let days_to_maximum_rooting = calendar_days_for_gdd(
        inputs,
        run,
        driving_calendar.maximum_rooting,
        run.first_crop_day_number,
    )
    .unwrap_or(supplied.maximum_rooting);
    let adjust_complete_cycle = run.crop.parameters.kind != CropKind::Forage;
    let days_to_senescence = if adjust_complete_cycle {
        calendar_days_for_gdd(
            inputs,
            run,
            driving_calendar.senescence,
            run.first_crop_day_number,
        )
        .unwrap_or(supplied.senescence)
    } else {
        supplied.senescence
    };
    let days_to_maturity = if adjust_complete_cycle {
        calendar_days_for_gdd(
            inputs,
            run,
            driving_calendar.maturity,
            run.first_crop_day_number,
        )
        .unwrap_or(supplied.maturity)
    } else {
        supplied.maturity
    };
    let days_to_flowering =
        if matches!(&run.crop.parameters.kind, CropKind::Grain | CropKind::Tuber) {
            calendar_days_for_gdd(
                inputs,
                run,
                driving_calendar.flowering,
                run.first_crop_day_number,
            )
            .unwrap_or(supplied.flowering)
        } else {
            supplied.flowering
        };
    let flowering_length_days = if run.crop.parameters.kind == CropKind::Grain {
        calendar_days_for_gdd(
            inputs,
            run,
            driving_calendar.flowering_length,
            run.first_crop_day_number + days_to_flowering,
        )
        .unwrap_or(supplied.flowering_length)
    } else if run.crop.parameters.kind == CropKind::Tuber {
        0
    } else {
        supplied.flowering_length
    };
    let harvest_index_start =
        if matches!(&run.crop.parameters.kind, CropKind::Grain | CropKind::Tuber) {
            run.first_crop_day_number + days_to_flowering
        } else {
            run.first_crop_day_number
        };
    let days_to_harvest_index = driving_calendar
        .harvest_index
        .map(|target| {
            calendar_days_for_gdd(inputs, run, target, harvest_index_start).or(supplied
                .harvest_index
                .ok_or(Error::InvalidSimulationConfiguration(
                    "GDD crop calendar cannot resolve harvest-index timing",
                )))
        })
        .transpose()?
        .or(supplied.harvest_index);
    let decline_coefficient_per_day = calendar_decline_coefficient(
        inputs,
        run,
        days_to_senescence,
        driving_calendar.senescence,
        driving_calendar.maturity,
    )
    .unwrap_or(supplied.canopy_decline_coefficient);

    let canopy_growth_coefficient = driving_calendar.canopy_growth_coefficient;
    let calendar = run.crop.cycle.calendar_days_mut();
    calendar.emergence = days_to_emergence;
    calendar.maximum_rooting = days_to_maximum_rooting;
    calendar.senescence = days_to_senescence;
    calendar.maturity = days_to_maturity;
    calendar.flowering = days_to_flowering;
    calendar.flowering_length = flowering_length_days;
    calendar.harvest_index = days_to_harvest_index;
    calendar.canopy_growth_coefficient =
        f64::from(full_canopy_gdd) / f64::from(days_to_full_canopy) * canopy_growth_coefficient;
    calendar.canopy_decline_coefficient = decline_coefficient_per_day;
    Ok(Some(days_to_full_canopy))
}

fn calendar_days_for_gdd(
    inputs: &PreparedInputs,
    run: &RunConfig,
    target_gdd: i32,
    first_day_number: i32,
) -> Result<i32, Error> {
    calendar_days_for_gdd_inputs(
        &inputs.daily_inputs,
        &run.crop.parameters.temperature,
        required_gdd_method(&run.crop)?,
        target_gdd,
        first_day_number,
    )
}

fn calendar_days_for_gdd_inputs(
    inputs: &[PreparedDailyInput],
    temperature: &CropTemperature,
    method: crate::climate::GrowingDegreeDayMethod,
    target_gdd: i32,
    first_day_number: i32,
) -> Result<i32, Error> {
    if target_gdd <= 0 {
        return Ok(0);
    }
    let first_index = inputs
        .iter()
        .position(|day| day.day_number == first_day_number)
        .ok_or(Error::InvalidSimulationConfiguration(
            "weather does not cover a crop-stage start day",
        ))?;
    let mut remaining = f64::from(target_gdd);
    let mut days = 0_i32;
    for input in &inputs[first_index..] {
        remaining -= growing_degree_days(
            temperature.base,
            temperature.upper,
            input.weather.minimum_temperature,
            input.weather.maximum_temperature,
            method,
        );
        days += 1;
        if remaining <= 0.0 {
            return Ok(days);
        }
    }
    Err(Error::InvalidSimulationConfiguration(
        "weather does not cover a complete GDD crop stage",
    ))
}

fn cumulative_gdd_for_calendar_days(
    inputs: &PreparedInputs,
    run: &RunConfig,
    calendar_days: i32,
) -> Result<i32, Error> {
    let day_count = usize::try_from(calendar_days).map_err(|_| {
        Error::InvalidSimulationConfiguration("crop stage has a negative calendar duration")
    })?;
    let first_index = inputs
        .daily_inputs
        .iter()
        .position(|day| day.day_number == run.first_crop_day_number)
        .ok_or(Error::InvalidSimulationConfiguration(
            "weather does not cover the crop-cycle start day",
        ))?;
    let end_index =
        first_index
            .checked_add(day_count)
            .ok_or(Error::InvalidSimulationConfiguration(
                "crop-stage weather range overflows",
            ))?;
    let inputs = inputs.daily_inputs.get(first_index..end_index).ok_or(
        Error::InvalidSimulationConfiguration("weather does not cover a complete crop stage"),
    )?;
    let method = required_gdd_method(&run.crop)?;
    Ok(roundc_i32(
        inputs
            .iter()
            .map(|day| {
                growing_degree_days(
                    run.crop.parameters.temperature.base,
                    run.crop.parameters.temperature.upper,
                    day.weather.minimum_temperature,
                    day.weather.maximum_temperature,
                    method,
                )
            })
            .sum(),
    ))
}

fn daily_stomatal_stress(actual_transpiration: f64, potential_transpiration: f64) -> Option<f64> {
    if potential_transpiration < f64::EPSILON {
        None
    } else {
        Some(100.0 * (1.0 - actual_transpiration / potential_transpiration))
    }
}

fn calendar_decline_coefficient(
    inputs: &PreparedInputs,
    run: &RunConfig,
    days_to_senescence: i32,
    gdd_to_senescence: i32,
    gdd_to_maturity: i32,
) -> Result<f64, Error> {
    let crop = &run.crop;
    let mut gdd = crop.cycle.driving_calendar().clone();
    gdd.senescence = gdd_to_senescence;
    gdd.maturity = gdd_to_maturity;
    calendar_decline_coefficient_inputs(
        &inputs.daily_inputs,
        &crop.parameters.temperature,
        required_gdd_method(crop)?,
        crop.parameters.canopy.maximum_cover,
        run.first_crop_day_number,
        days_to_senescence,
        &gdd,
    )
}

fn required_gdd_method(crop: &PreparedCrop) -> Result<GrowingDegreeDayMethod, Error> {
    crop.cycle
        .gdd_method()
        .ok_or(Error::InvalidCropConfiguration(
            "GDD crop cycle is missing its GDD calculation method",
        ))
}

fn calendar_decline_coefficient_inputs(
    inputs: &[PreparedDailyInput],
    temperature: &CropTemperature,
    method: crate::climate::GrowingDegreeDayMethod,
    maximum_cover: f64,
    first_crop_day_number: i32,
    days_to_senescence: i32,
    gdd: &DevelopmentCalendar,
) -> Result<f64, Error> {
    let gdd_coefficient = gdd.canopy_decline_coefficient;
    let complete_decline_gdd =
        roundc_i32((maximum_cover + 2.29) / (gdd_coefficient * 3.33) * 21.0_f64.ln() + 0.5);
    let decline_gdd = if gdd.senescence + complete_decline_gdd <= gdd.maturity {
        complete_decline_gdd
    } else if gdd.senescence < gdd.maturity {
        gdd.maturity - gdd.senescence
    } else {
        5
    };
    let ending_cover = if gdd.senescence + complete_decline_gdd <= gdd.maturity {
        0.0
    } else {
        maximum_cover
            * (1.0
                - 0.05
                    * ((f64::from(decline_gdd) * gdd_coefficient * 3.33 / (maximum_cover + 2.29))
                        .exp()
                        - 1.0))
    };
    let decline_start = first_crop_day_number
        .checked_add(days_to_senescence)
        .ok_or(Error::InvalidSimulationConfiguration(
            "canopy-decline start day overflows",
        ))?;
    let decline_days =
        calendar_days_for_gdd_inputs(inputs, temperature, method, decline_gdd, decline_start)?;
    Ok(((maximum_cover + 2.29) / f64::from(decline_days)
        * (1.0 + (1.0 - ending_cover / maximum_cover) / 0.05).ln())
        / 3.33)
}

#[cfg(test)]
mod tests {
    use super::{
        DailyIrrigation, DailyWeather, Groundwater, calibration_carbon_dioxide,
        daily_stomatal_stress, prepare_daily_inputs,
    };

    fn weather(carbon_dioxide: f64) -> DailyWeather {
        DailyWeather {
            minimum_temperature: 10.0,
            maximum_temperature: 20.0,
            reference_et: 5.0,
            rain: 0.0,
            carbon_dioxide,
        }
    }

    #[test]
    fn daily_input_day_number_is_derived_from_weather_position() {
        let inputs = prepare_daily_inputs(10, vec![weather(400.0), weather(400.0)], None, None)
            .expect("two weather days should define two daily inputs");

        assert_eq!(inputs[1].day_number, 11);
    }

    #[test]
    fn groundwater_must_align_with_every_weather_day() {
        let result = prepare_daily_inputs(
            1,
            vec![weather(400.0)],
            None,
            Some(vec![
                Groundwater {
                    depth: 1.0,
                    conductivity: 0.0,
                },
                Groundwater {
                    depth: 1.0,
                    conductivity: 0.0,
                },
            ]),
        );

        assert!(result.is_err());
    }

    #[test]
    fn external_irrigation_is_preserved_outside_weather() {
        let inputs = prepare_daily_inputs(
            1,
            vec![weather(400.0)],
            Some(vec![DailyIrrigation {
                depth: 12.5,
                conductivity: 0.8,
            }]),
            None,
        )
        .expect("aligned irrigation should prepare");
        let irrigation = inputs[0]
            .external_irrigation
            .as_ref()
            .expect("the prepared day should retain external irrigation");

        assert_eq!((irrigation.depth, irrigation.conductivity), (12.5, 0.8));
    }

    #[test]
    fn calibrated_fertility_rejects_variable_daily_carbon_dioxide() {
        let inputs = prepare_daily_inputs(1, vec![weather(400.0), weather(401.0)], None, None)
            .expect("valid weather should prepare");

        assert!(calibration_carbon_dioxide(&inputs).is_err());
    }

    #[test]
    fn daily_stomatal_stress_is_undefined_when_potential_transpiration_is_zero() {
        assert_eq!(daily_stomatal_stress(0.0, 0.0), None);
    }

    #[test]
    fn daily_stomatal_stress_is_zero_when_actual_equals_potential_transpiration() {
        assert_eq!(daily_stomatal_stress(4.0, 4.0), Some(0.0));
    }

    #[test]
    fn daily_stomatal_stress_reports_partial_transpiration_reduction_in_percent() {
        assert_eq!(daily_stomatal_stress(3.0, 4.0), Some(25.0));
    }
}
