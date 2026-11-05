//! Water-productivity adjustment and daily biomass accumulation.

use crate::Error;
use crate::calendar::CalendarDate;
use crate::canopy::{
    adjusted_canopy_day_with_timing, adjusted_canopy_gdd_with_timing, canopy_at_time,
    derive_canopy_timing, potential_canopy_cover_with_timing,
};
use crate::climate::{GrowingDegreeDayMethod, ReferenceTemperature, growing_degree_days};
use crate::compat::roundc_i32;
use crate::crop::{
    CropKind, CropProductivity, CropTemperature, DevelopmentCalendar, PlantingMethod, PreparedCrop,
    PreparedCropCycle,
};
use crate::et::{PotentialEtParameters, potential_et};
use crate::harvest::calibration_yield_formation_switch;
use crate::parameters::SimulationParameters;

/// Atmospheric carbon-dioxide concentration used to calibrate crop productivity,
/// in parts per million.
pub const REFERENCE_CO2: f64 = 369.41;

/// Cumulative biomass state in the original model units.
#[derive(Debug)]
pub struct BiomassState {
    /// Actual crop biomass, in tonnes per hectare.
    pub actual: f64,
    /// Potential biomass under the configured soil-fertility stress, in tonnes per hectare.
    pub potential: f64,
    /// Potential biomass without soil-fertility stress, in tonnes per hectare.
    pub unrestricted: f64,
    /// Actual crop-plus-weed biomass used by dynamic fertility stress, in tonnes per hectare.
    pub total: f64,
    /// Cumulative actual transpiration normalized by reference ET.
    pub normalized_actual_transpiration: f64,
}

/// Daily values needed to update biomass.
#[derive(Debug)]
pub struct BiomassInputs {
    /// Reference evapotranspiration, in millimetres.
    pub reference_et: f64,
    /// Actual transpiration of crop and weeds, in millimetres.
    pub actual_transpiration: f64,
    /// Actual transpiration assigned to the crop, in millimetres.
    pub crop_transpiration: f64,
    /// Potential crop transpiration without water or fertility stress, in millimetres.
    pub unrestricted_transpiration: f64,
    /// Atmospheric carbon-dioxide concentration, in parts per million.
    pub co2: f64,
    /// Actual-crop progress of the switch to yield-formation water productivity, from zero to one.
    pub actual_yield_formation_switch: f64,
    /// Unrestricted-crop progress of the switch to yield-formation water productivity, from zero to one.
    pub unrestricted_yield_formation_switch: f64,
    /// Reduction of water productivity by soil-fertility stress, in percent.
    pub fertility_wp_reduction: f64,
    /// Cumulative normalized potential transpiration under fertility stress.
    pub normalized_fertility_potential_transpiration: f64,
    /// Fraction of unrestricted biomass attainable under fertility stress.
    pub fertility_biomass_fraction: f64,
}

/// Water-productivity values used by one daily biomass step.
#[derive(Debug)]
pub struct DailyWaterProductivity {
    /// Productivity after carbon-dioxide, yield-stage, and fertility adjustments.
    pub actual: f64,
    /// Productivity after carbon-dioxide and yield-stage adjustments only.
    pub unrestricted: f64,
}

/// Prepared relationship between soil-fertility stress and attainable biomass.
#[derive(Debug)]
pub struct FertilityCalibration {
    /// Intercept of stress percent as a function of relative biomass percent.
    pub stress_intercept: f64,
    /// Linear coefficient per relative-biomass percentage point.
    pub stress_linear: f64,
    /// Quadratic coefficient per squared relative-biomass percentage point.
    pub stress_quadratic: f64,
    /// Attainable fraction of unrestricted biomass at the selected stress level.
    pub attainable_biomass_fraction: f64,
    /// Unstressed seasonal sum of potential transpiration divided by reference ET.
    pub seasonal_potential_transpiration: f64,
}

/// Prepared quadratic relationship from biomass loss to maximum-canopy reduction.
#[derive(Debug)]
pub struct SalinityCalibration {
    /// Intercept of maximum-canopy reduction as a function of biomass-loss percent.
    pub reduction_intercept: f64,
    /// Linear coefficient per biomass-loss percentage point.
    pub reduction_linear: f64,
    /// Quadratic coefficient per squared biomass-loss percentage point.
    pub reduction_quadratic: f64,
}

/// Canopy and stomatal effects associated with one salinity reduction level.
#[derive(Debug)]
pub struct SalinityCanopyEffects {
    /// Reduction of the canopy growth coefficient, in percent.
    pub growth_reduction: i8,
    /// Reduction of maximum canopy cover, in percent.
    pub maximum_cover_reduction: i8,
    /// Reduction of the stomatal stress coefficient, in percent.
    pub stomatal_reduction: i8,
    /// Additional canopy decline, in canopy-cover percentage points per day.
    pub canopy_decline: f64,
}

#[derive(Debug)]
struct ReferenceStressEffects {
    growth_reduction: i8,
    maximum_cover_reduction: i8,
    water_productivity_reduction: i8,
    stomatal_reduction: i8,
    canopy_decline: f64,
}

struct ReferenceCropCalendar {
    calendar_days: DevelopmentCalendar,
    full_canopy: i32,
    start_offset: usize,
}

impl BiomassState {
    /// Creates a zeroed cumulative biomass state.
    #[must_use]
    pub const fn new() -> Self {
        Self {
            actual: 0.0,
            potential: 0.0,
            unrestricted: 0.0,
            total: 0.0,
            normalized_actual_transpiration: 0.0,
        }
    }
}

impl Default for BiomassState {
    fn default() -> Self {
        Self::new()
    }
}

/// Advances actual, total, and potential biomass by one daily step.
///
/// State, productivity, and input fields retain their field-documented units;
/// the returned daily productivities are in tonnes per hectare per millimetre
/// of normalized transpiration.
#[must_use]
pub fn advance_biomass(
    state: &mut BiomassState,
    productivity: &CropProductivity,
    inputs: &BiomassInputs,
) -> DailyWaterProductivity {
    let base = productivity.normalized_water_productivity / 100.0;
    let mut actual = base
        * (1.0
            - (1.0 - f64::from(productivity.yield_formation_water_productivity) / 100.0)
                * inputs.actual_yield_formation_switch);
    let mut unrestricted = base
        * (1.0
            - (1.0 - f64::from(productivity.yield_formation_water_productivity) / 100.0)
                * inputs.unrestricted_yield_formation_switch);
    if roundc_i32(100.0 * inputs.co2) != roundc_i32(100.0 * REFERENCE_CO2) {
        let adjustment = co2_productivity_adjustment(
            inputs.co2,
            productivity.normalized_water_productivity,
            productivity.elevated_co2_adaptation,
        );
        actual *= adjustment;
        unrestricted *= adjustment;
    }

    if inputs.reference_et > 0.0 {
        if inputs.fertility_wp_reduction > 0.0 {
            if state.normalized_actual_transpiration
                / inputs.normalized_fertility_potential_transpiration
                < 1.0 - f64::EPSILON
            {
                state.normalized_actual_transpiration +=
                    inputs.actual_transpiration / inputs.reference_et;
                if state.normalized_actual_transpiration > 0.0 {
                    actual *= 1.0
                        - (inputs.fertility_wp_reduction / 100.0)
                            * (2.0
                                * (state.normalized_actual_transpiration
                                    / inputs.normalized_fertility_potential_transpiration)
                                    .ln())
                            .exp();
                }
            } else {
                actual *= 1.0 - inputs.fertility_wp_reduction / 100.0;
            }
        } else {
            state.normalized_actual_transpiration +=
                inputs.actual_transpiration / inputs.reference_et;
        }

        state.actual += actual * (inputs.crop_transpiration / inputs.reference_et);
        state.total += actual * (inputs.actual_transpiration / inputs.reference_et);
        state.unrestricted +=
            unrestricted * (inputs.unrestricted_transpiration / inputs.reference_et);
    }
    state.potential = inputs.fertility_biomass_fraction * state.unrestricted;

    DailyWaterProductivity {
        actual,
        unrestricted,
    }
}

/// Integrates unstressed potential transpiration over the reference crop cycle.
///
/// `first_crop_day_number` is an absolute AquaCrop day number,
/// `carbon_dioxide` is atmospheric CO2 concentration in parts per million,
/// and reference temperatures are in degrees Celsius and must contain exactly
/// one 365-day climatology. Prepared crop time, canopy, coefficient, and temperature
/// fields retain the units documented on [`PreparedCrop`]; program parameters retain
/// the units documented on [`SimulationParameters`]. The result is the
/// dimensionless seasonal sum of daily potential transpiration divided by the
/// standard reference ET of 5 millimetres per day.
///
/// # Errors
///
/// Returns [`Error`] for a malformed reference climatology, an invalid crop
/// date, or a reference crop stage that cannot be evaluated.
pub fn seasonal_potential_transpiration_sum(
    crop: &PreparedCrop,
    parameters: &SimulationParameters,
    first_crop_day_number: i32,
    carbon_dioxide: f64,
    reference_temperatures: &[ReferenceTemperature],
) -> Result<f64, Error> {
    if reference_temperatures.len() != 365 {
        return Err(Error::InvalidSimulationConfiguration(
            "reference temperature climatology must contain 365 days",
        ));
    }
    let calendar = reference_crop_calendar(crop, first_crop_day_number, reference_temperatures)?;
    Ok(integrate_seasonal_potential_transpiration(
        crop,
        parameters,
        reference_temperatures,
        &calendar,
        carbon_dioxide,
    ))
}

pub(crate) fn prepared_seasonal_potential_transpiration_sum(
    crop: &PreparedCrop,
    parameters: &SimulationParameters,
    first_crop_day_number: i32,
    days_to_full_canopy: i32,
    carbon_dioxide: f64,
    reference_temperatures: &[ReferenceTemperature],
) -> Result<f64, Error> {
    if reference_temperatures.len() != 365 {
        return Err(Error::InvalidSimulationConfiguration(
            "reference temperature climatology must contain 365 days",
        ));
    }
    let date = CalendarDate::from_day_number(first_crop_day_number)?;
    let reference_date = CalendarDate::new(1901, date.month(), date.day())?;
    let start_offset = usize::try_from(reference_date.day_number() - 1)
        .map_err(|_| Error::InvalidSimulationConfiguration("invalid reference crop date"))?;
    Ok(integrate_seasonal_potential_transpiration(
        crop,
        parameters,
        reference_temperatures,
        &ReferenceCropCalendar {
            calendar_days: crop.cycle.calendar_days().clone(),
            full_canopy: days_to_full_canopy,
            start_offset,
        },
        carbon_dioxide,
    ))
}

fn integrate_seasonal_potential_transpiration(
    crop: &PreparedCrop,
    parameters: &SimulationParameters,
    reference_temperatures: &[ReferenceTemperature],
    calendar: &ReferenceCropCalendar,
    carbon_dioxide: f64,
) -> f64 {
    let cycle = &crop.cycle;
    let calendar_days = &calendar.calendar_days;
    let driving_calendar = cycle.driving_calendar();
    let mut sum_gdd = 0.0;
    let mut growth_on = crop.parameters.planting_method == PlantingMethod::Regrowth;
    let mut seasonal_sum = 0.0;
    let mut canopy_timing = derive_canopy_timing(crop);
    canopy_timing.days_to_full_canopy = calendar.full_canopy;
    for day in 1..=calendar_days.maturity {
        let temperature = &reference_temperatures
            [(calendar.start_offset + usize::try_from(day - 1).unwrap_or(0)) % 365];
        let method = cycle.gdd_method().unwrap_or(parameters.gdd_method);
        let daily_gdd = growing_degree_days(
            crop.parameters.temperature.base,
            crop.parameters.temperature.upper,
            temperature.minimum,
            temperature.maximum,
            method,
        );
        if cycle.uses_growing_degree_days() {
            sum_gdd += daily_gdd;
        }
        let canopy_cover =
            if crop.parameters.planting_method == PlantingMethod::Regrowth && day == 1 {
                let initial_time = if cycle.uses_growing_degree_days() {
                    canopy_timing.gdd_to_initial_cover
                } else {
                    canopy_timing.days_to_initial_cover
                };
                if let Some(initial_time) = initial_time {
                    let growth = if cycle.uses_growing_degree_days() {
                        driving_calendar.canopy_growth_coefficient
                    } else {
                        calendar_days.canopy_growth_coefficient
                    };
                    canopy_at_time(
                        f64::from(initial_time),
                        crop.parameters.canopy.initial_seedling_cover,
                        growth,
                        crop.parameters.canopy.maximum_cover,
                    )
                } else {
                    crop.parameters.canopy.maximum_cover
                }
            } else if growth_on {
                potential_canopy_cover_with_timing(crop, &canopy_timing, day, 0, sum_gdd, 0, 0)
            } else if reference_growth_starts(cycle, calendar, day, sum_gdd) {
                growth_on = true;
                crop.parameters.canopy.initial_seedling_cover
            } else {
                0.0
            };
        let withered_cover = if day >= calendar.full_canopy {
            crop.parameters.canopy.maximum_cover
        } else {
            canopy_cover
        };
        if canopy_cover > 0.000_1 {
            let potential = potential_et(&PotentialEtParameters {
                days_after_planting: adjusted_canopy_day_with_timing(crop, &canopy_timing, day),
                delayed_days: 0,
                days_to_germination: calendar.calendar_days.emergence,
                days_to_full_canopy: calendar.full_canopy,
                days_to_senescence: calendar.calendar_days.senescence,
                days_to_harvest: calendar.calendar_days.maturity,
                day_last_cut: 0,
                canopy_cover,
                reference_et: 5.0,
                crop_coefficient: crop.parameters.evapotranspiration.kc_top,
                coefficient_decline: crop.parameters.evapotranspiration.kc_decline_cumulative,
                maximum_canopy_cover: crop.parameters.canopy.maximum_cover,
                withered_canopy_cover: withered_cover,
                late_evaporation_effect: f64::from(
                    crop.parameters
                        .evapotranspiration
                        .late_season_evaporation_reduction,
                ),
                carbon_dioxide,
                growing_degree_days: daily_gdd,
                cold_stress_threshold_gdd: crop.parameters.temperature.full_transpiration_gdd,
                wet_bare_soil_coefficient: parameters.wet_bare_soil_coefficient,
                senescence_exponent: parameters.senescence_exponent,
                evaporation_limit_enabled: false,
            });
            seasonal_sum += potential.transpiration / 5.0;
        }
    }
    seasonal_sum
}

/// Calibrates the source stress–biomass relationship for one crop and climate.
///
/// `fertility_stress` is the selected management stress in percent,
/// `first_crop_day_number` is an absolute AquaCrop day number, and
/// `carbon_dioxide` is atmospheric CO2 concentration in parts per million.
/// Reference temperatures are degrees Celsius in a 365-day climatology. Prepared crop
/// and program fields retain their individually documented units. Regression
/// inputs and outputs use percent biomass and percent fertility stress.
///
/// # Errors
///
/// Returns [`Error`] when reference inputs are invalid or the regression is singular.
pub fn calibrate_fertility_stress(
    crop: &PreparedCrop,
    parameters: &SimulationParameters,
    fertility_stress: i32,
    first_crop_day_number: i32,
    carbon_dioxide: f64,
    reference_temperatures: &[ReferenceTemperature],
) -> Result<FertilityCalibration, Error> {
    let calendar = reference_crop_calendar(crop, first_crop_day_number, reference_temperatures)?;
    let seasonal_potential = seasonal_potential_transpiration_sum(
        crop,
        parameters,
        first_crop_day_number,
        carbon_dioxide,
        reference_temperatures,
    )?;
    let mut matrix = [(0.0, 0.0, 0.0); 8];
    let mut unrestricted = 0.0;
    for (index, row) in matrix.iter_mut().enumerate() {
        let stress = i32::try_from(index).unwrap_or(0) * 10;
        let mut effects = fertility_stress_effects(crop, stress);
        let full_canopy =
            reference_full_canopy_under_fertility(crop, &calendar, &mut effects, stress);
        let gdd_full_canopy =
            if effects.growth_reduction == 0 && effects.maximum_cover_reduction == 0 {
                derive_canopy_timing(crop).gdd_to_full_canopy
            } else if crop.cycle.uses_growing_degree_days() {
                reference_days_for_gdd(
                    &crop.parameters.temperature,
                    required_gdd_method(crop)?,
                    reference_temperatures,
                    calendar.start_offset,
                    full_canopy,
                )?
            } else {
                derive_canopy_timing(crop).gdd_to_full_canopy
            };
        let normalized = normalized_reference_biomass(
            crop,
            parameters,
            reference_temperatures,
            &calendar,
            &effects,
            full_canopy,
            gdd_full_canopy,
            stress,
            seasonal_potential,
            carbon_dioxide,
        );
        if index == 0 {
            unrestricted = normalized;
        }
        let relative = if index == 0 || unrestricted <= 0.000_01 {
            100.0
        } else {
            100.0 * normalized / unrestricted
        };
        *row = (f64::from(stress), relative, relative * relative);
    }
    let (stress_intercept, stress_linear, stress_quadratic) = regress_stress_biomass(&matrix)?;
    let attainable_biomass_fraction = invert_stress_biomass_relationship(
        fertility_stress,
        stress_intercept,
        stress_linear,
        stress_quadratic,
    );
    Ok(FertilityCalibration {
        stress_intercept,
        stress_linear,
        stress_quadratic,
        attainable_biomass_fraction,
        seasonal_potential_transpiration: seasonal_potential,
    })
}

/// Calibrates the source relationship between biomass loss from salinity and
/// reduction of maximum canopy cover.
///
/// `first_crop_day_number` is an absolute AquaCrop day number,
/// `carbon_dioxide` is in parts per million, and reference temperatures are in
/// degrees Celsius. `temperature_first_day_number` is the absolute day of the
/// first value in `temperatures`, whose values are also in degrees Celsius.
/// Prepared crop and program fields retain their documented units. Regression inputs
/// and outputs are percentages.
///
/// # Errors
///
/// Returns [`Error`] when reference inputs are invalid or the regression is singular.
pub fn calibrate_salinity_stress(
    crop: &PreparedCrop,
    parameters: &SimulationParameters,
    first_crop_day_number: i32,
    temperature_first_day_number: i32,
    temperatures: &[ReferenceTemperature],
    carbon_dioxide: f64,
    reference_temperatures: &[ReferenceTemperature],
) -> Result<SalinityCalibration, Error> {
    let calendar = reference_crop_calendar(crop, first_crop_day_number, reference_temperatures)?;
    let seasonal_potential = seasonal_potential_transpiration_sum(
        crop,
        parameters,
        first_crop_day_number,
        carbon_dioxide,
        reference_temperatures,
    )?;
    let mut matrix = [(0.0, 0.0, 0.0); 10];
    let mut unrestricted = 0.0;
    for (index, row) in matrix.iter_mut().enumerate() {
        let reduction = i32::try_from(index).unwrap_or(0) * 10;
        let effects = salinity_reference_effects(crop, &calendar, reduction);
        let adjusted_maximum = (1.0 - f64::from(effects.maximum_cover_reduction) / 100.0)
            * crop.parameters.canopy.maximum_cover;
        let full_canopy = crate::canopy::time_to_canopy_cover(
            0.98 * adjusted_maximum,
            crop.parameters.canopy.initial_seedling_cover,
            adjusted_maximum,
            calendar.calendar_days.canopy_growth_coefficient
                * (1.0 - f64::from(effects.growth_reduction) / 100.0),
            calendar.calendar_days.emergence,
        );
        let gdd_full_canopy = if crop.cycle.uses_growing_degree_days() {
            series_gdd_for_days(
                &crop.parameters.temperature,
                required_gdd_method(crop)?,
                temperature_first_day_number,
                temperatures,
                first_crop_day_number,
                full_canopy,
            )?
        } else {
            derive_canopy_timing(crop).gdd_to_full_canopy
        };
        let normalized = normalized_reference_biomass(
            crop,
            parameters,
            reference_temperatures,
            &calendar,
            &effects,
            full_canopy,
            gdd_full_canopy,
            reduction,
            seasonal_potential,
            carbon_dioxide,
        );
        if index == 0 {
            unrestricted = normalized;
        }
        let biomass_loss = if index == 0 || unrestricted <= 0.000_01 {
            0.0
        } else {
            100.0 * (1.0 - normalized / unrestricted)
        };
        *row = (
            f64::from(reduction),
            biomass_loss,
            biomass_loss * biomass_loss,
        );
    }
    let (reduction_intercept, reduction_linear, reduction_quadratic) =
        regress_stress_biomass(&matrix)?;
    Ok(SalinityCalibration {
        reduction_intercept,
        reduction_linear,
        reduction_quadratic,
    })
}

/// Calculates the source canopy and stomatal effects for salinity stress.
///
/// `maximum_cover_reduction` is in percent. Prepared crop fields retain their documented
/// units; returned reductions are percentages and canopy decline is in
/// canopy-cover percentage points per day.
#[must_use]
pub fn salinity_canopy_effects(
    crop: &PreparedCrop,
    maximum_cover_reduction: i8,
) -> SalinityCanopyEffects {
    let calendar = ReferenceCropCalendar {
        calendar_days: crop.cycle.calendar_days().clone(),
        full_canopy: derive_canopy_timing(crop).days_to_full_canopy,
        start_offset: 0,
    };
    let effects = salinity_reference_effects(crop, &calendar, i32::from(maximum_cover_reduction));
    SalinityCanopyEffects {
        growth_reduction: effects.growth_reduction,
        maximum_cover_reduction: effects.maximum_cover_reduction,
        stomatal_reduction: effects.stomatal_reduction,
        canopy_decline: effects.canopy_decline,
    }
}

fn fertility_stress_effects(crop: &PreparedCrop, stress: i32) -> ReferenceStressEffects {
    let stress = f64::from(stress) / 100.0;
    let reduction = |shape| {
        crate::compat::roundc_i8(
            100.0 * (1.0 - crate::stress::stress_coefficient(stress, 0.0, 1.0, shape)),
        )
    };
    ReferenceStressEffects {
        growth_reduction: reduction(crop.parameters.fertility_stress.canopy_growth_shape),
        maximum_cover_reduction: reduction(crop.parameters.fertility_stress.maximum_canopy_shape),
        water_productivity_reduction: reduction(
            crop.parameters.fertility_stress.water_productivity_shape,
        ),
        stomatal_reduction: 0,
        canopy_decline: 1.0
            - crate::stress::stress_coefficient(
                stress,
                0.0,
                1.0,
                crop.parameters.fertility_stress.canopy_decline_shape,
            ),
    }
}

#[expect(
    clippy::too_many_lines,
    reason = "the source canopy-distortion branches are kept together for numerical auditing"
)]
fn salinity_reference_effects(
    crop: &PreparedCrop,
    calendar: &ReferenceCropCalendar,
    reduction: i32,
) -> ReferenceStressEffects {
    let reduction_value = f64::from(reduction);
    let distortion = f64::from(crop.parameters.salinity_stress.canopy_distortion);
    let mut growth_reduction = 0;
    let mut days_to_full = f64::from(calendar.full_canopy);
    let mut maximum_full_day = days_to_full;
    let mut gdd_to_full = f64::from(derive_canopy_timing(crop).gdd_to_full_canopy);
    let maximum = crop.parameters.canopy.maximum_cover;
    let initial = crop.parameters.canopy.initial_seedling_cover;
    let mut target = 0.98 * maximum;
    let driving_calendar = crop.cycle.driving_calendar();
    if initial <= target && initial < maximum && reduction > 0 {
        if crop.cycle.uses_growing_degree_days() {
            gdd_to_full = ((0.25 * maximum * maximum / initial) / (maximum - target)).ln()
                / driving_calendar.canopy_growth_coefficient;
        } else {
            days_to_full = ((0.25 * maximum * maximum / initial) / (maximum - target)).ln()
                / calendar.calendar_days.canopy_growth_coefficient;
        }
        let adjusted_maximum = 0.90 * maximum * (1.0 - reduction_value / 100.0);
        target = 0.98 * adjusted_maximum;
        if adjusted_maximum - target >= 0.000_1 {
            if crop.cycle.uses_growing_degree_days() {
                let fastest = ((0.25 * adjusted_maximum * adjusted_maximum / initial)
                    / (adjusted_maximum - target))
                    .ln()
                    / gdd_to_full;
                let mut latest = f64::from(derive_canopy_timing(crop).gdd_to_full_canopy)
                    + f64::from(
                        driving_calendar.senescence - derive_canopy_timing(crop).gdd_to_full_canopy,
                    ) / 2.0;
                if crop.parameters.development.determinacy_linked_to_flowering {
                    latest = latest.min(
                        f64::from(driving_calendar.flowering)
                            + f64::from(driving_calendar.flowering_length) / 2.0,
                    );
                }
                let slowest = if latest > gdd_to_full {
                    ((0.25 * adjusted_maximum * adjusted_maximum / initial)
                        / (adjusted_maximum - target))
                        .ln()
                        / latest
                } else {
                    fastest
                };
                let adjusted = if reduction < 10 {
                    fastest
                        - (fastest - slowest)
                            * (reduction_value.exp() / 10.0_f64.exp())
                            * distortion
                            / 100.0
                } else {
                    fastest - (fastest - slowest) * distortion / 100.0
                };
                growth_reduction = crate::compat::roundc_i8(
                    100.0 * (driving_calendar.canopy_growth_coefficient - adjusted)
                        / driving_calendar.canopy_growth_coefficient,
                );
            } else {
                let fastest = ((0.25 * adjusted_maximum * adjusted_maximum / initial)
                    / (adjusted_maximum - target))
                    .ln()
                    / days_to_full;
                maximum_full_day = f64::from(calendar.full_canopy)
                    + f64::from(calendar.calendar_days.senescence - calendar.full_canopy) / 2.0;
                if crop.parameters.development.determinacy_linked_to_flowering {
                    maximum_full_day = maximum_full_day.min(
                        f64::from(calendar.calendar_days.flowering)
                            + f64::from(calendar.calendar_days.flowering_length) / 2.0,
                    );
                }
                let slowest = if maximum_full_day > days_to_full {
                    ((0.25 * adjusted_maximum * adjusted_maximum / initial)
                        / (adjusted_maximum - target))
                        .ln()
                        / maximum_full_day
                } else {
                    fastest
                };
                let adjusted = if reduction < 10 {
                    fastest
                        - (fastest - slowest) * 1.5_f64.powi(reduction) / 1.5_f64.powi(10)
                            * distortion
                            / 100.0
                } else {
                    fastest - (fastest - slowest) * distortion / 100.0
                };
                growth_reduction = crate::compat::roundc_i8(
                    100.0 * (calendar.calendar_days.canopy_growth_coefficient - adjusted)
                        / calendar.calendar_days.canopy_growth_coefficient,
                );
            }
        }
    }

    let canopy_decline = if reduction == 0 {
        0.0
    } else {
        let adjusted_maximum = 0.98 * maximum * (1.0 - reduction_value / 100.0);
        let decline_start =
            maximum_full_day - (maximum_full_day - days_to_full) * distortion / 100.0;
        if f64::from(calendar.calendar_days.senescence) > decline_start && distortion > 0.0 {
            let smooth = if reduction < 10 {
                1.5_f64.powi(reduction) / 1.5_f64.powi(10)
            } else {
                1.0
            };
            let final_cover = (adjusted_maximum
                - smooth * 0.5 * distortion / 100.0 * (adjusted_maximum - initial))
                .max(initial);
            (100.0 * (adjusted_maximum - final_cover)
                / (f64::from(calendar.calendar_days.senescence) - decline_start))
                .clamp(0.001, 1.0)
        } else {
            0.001
        }
    };
    ReferenceStressEffects {
        growth_reduction,
        maximum_cover_reduction: crate::compat::roundc_i8(reduction_value),
        water_productivity_reduction: 0,
        stomatal_reduction: crate::compat::roundc_i8(reduction_value),
        canopy_decline,
    }
}

fn reference_full_canopy_under_fertility(
    crop: &PreparedCrop,
    calendar: &ReferenceCropCalendar,
    effects: &mut ReferenceStressEffects,
    stress: i32,
) -> i32 {
    if stress == 0 || effects.growth_reduction == 0 && effects.maximum_cover_reduction == 0 {
        return calendar.full_canopy;
    }
    let maximum_day = if crop.parameters.development.determinacy_linked_to_flowering {
        calendar.calendar_days.flowering
            + roundc_i32(f64::from(calendar.calendar_days.flowering_length) / 2.0)
    } else {
        calendar.calendar_days.senescence
    };
    let calculate = |growth_reduction: i8, maximum_reduction: i8| {
        let maximum =
            (1.0 - f64::from(maximum_reduction) / 100.0) * crop.parameters.canopy.maximum_cover;
        crate::canopy::time_to_canopy_cover(
            0.98 * maximum,
            crop.parameters.canopy.initial_seedling_cover,
            maximum,
            calendar.calendar_days.canopy_growth_coefficient
                * (1.0 - f64::from(growth_reduction) / 100.0),
            calendar.calendar_days.emergence,
        )
    };
    let mut full = calculate(effects.growth_reduction, effects.maximum_cover_reduction);
    while full > maximum_day && effects.growth_reduction > 0 {
        effects.growth_reduction -= 1;
        full = calculate(effects.growth_reduction, effects.maximum_cover_reduction);
    }
    while full > maximum_day
        && (1.0 - f64::from(effects.maximum_cover_reduction) / 100.0)
            * crop.parameters.canopy.maximum_cover
            > 0.10
        && effects.maximum_cover_reduction <= 50
    {
        effects.maximum_cover_reduction += 1;
        full = calculate(effects.growth_reduction, effects.maximum_cover_reduction);
    }
    full
}

#[expect(
    clippy::too_many_arguments,
    clippy::too_many_lines,
    reason = "the arguments are the prepared state of the source calibration pass"
)]
fn normalized_reference_biomass(
    crop: &PreparedCrop,
    parameters: &SimulationParameters,
    temperatures: &[ReferenceTemperature],
    calendar: &ReferenceCropCalendar,
    effects: &ReferenceStressEffects,
    days_to_full_canopy: i32,
    gdd_to_full_canopy: i32,
    stress: i32,
    seasonal_potential: f64,
    carbon_dioxide: f64,
) -> f64 {
    let stressed_seasonal_potential = (1.0 - f64::from(stress) / 100.0) * seasonal_potential;
    let driving_calendar = crop.cycle.driving_calendar();
    let day_per_gdd = if crop.cycle.uses_growing_degree_days()
        && gdd_to_full_canopy < driving_calendar.senescence
    {
        f64::from(calendar.calendar_days.senescence - days_to_full_canopy)
            / f64::from(driving_calendar.senescence - gdd_to_full_canopy)
    } else {
        1.0
    };
    let mut sum_gdd = 0.0;
    let mut growth_on = crop.parameters.planting_method == PlantingMethod::Regrowth;
    let mut normalized_biomass = 0.0;
    let mut normalized_transpiration = 0.0;
    let adjusted_maximum = (1.0 - f64::from(effects.maximum_cover_reduction) / 100.0)
        * crop.parameters.canopy.maximum_cover;
    let mut withered_cover = 0.0_f64;
    let mut canopy_timing = derive_canopy_timing(crop);
    canopy_timing.days_to_full_canopy = calendar.full_canopy;
    for day in 1..=calendar.calendar_days.maturity {
        let temperature =
            &temperatures[(calendar.start_offset + usize::try_from(day - 1).unwrap_or(0)) % 365];
        let method = crop.cycle.gdd_method().unwrap_or(parameters.gdd_method);
        let daily_gdd = growing_degree_days(
            crop.parameters.temperature.base,
            crop.parameters.temperature.upper,
            temperature.minimum,
            temperature.maximum,
            method,
        );
        if crop.cycle.uses_growing_degree_days() {
            sum_gdd += daily_gdd;
        }
        let canopy_cover = if growth_on {
            fertility_reference_canopy(
                crop,
                &canopy_timing,
                calendar,
                effects,
                days_to_full_canopy,
                gdd_to_full_canopy,
                day_per_gdd,
                day,
                sum_gdd,
            )
        } else if reference_growth_starts(&crop.cycle, calendar, day, sum_gdd) {
            growth_on = true;
            crop.parameters.canopy.initial_seedling_cover
        } else {
            0.0
        };
        withered_cover = withered_cover.max(canopy_cover);
        let effective_day = adjusted_canopy_day_with_timing(crop, &canopy_timing, day);
        if effective_day >= days_to_full_canopy {
            withered_cover = adjusted_maximum;
        }
        if canopy_cover <= 0.000_1 {
            continue;
        }
        let potential = potential_et(&PotentialEtParameters {
            days_after_planting: effective_day,
            delayed_days: 0,
            days_to_germination: calendar.calendar_days.emergence,
            days_to_full_canopy: calendar.full_canopy,
            days_to_senescence: calendar.calendar_days.senescence,
            days_to_harvest: calendar.calendar_days.maturity,
            day_last_cut: 0,
            canopy_cover,
            reference_et: 5.0,
            crop_coefficient: crop.parameters.evapotranspiration.kc_top,
            coefficient_decline: crop.parameters.evapotranspiration.kc_decline_cumulative,
            maximum_canopy_cover: crop.parameters.canopy.maximum_cover,
            withered_canopy_cover: withered_cover,
            late_evaporation_effect: f64::from(
                crop.parameters
                    .evapotranspiration
                    .late_season_evaporation_reduction,
            ),
            carbon_dioxide,
            growing_degree_days: daily_gdd,
            cold_stress_threshold_gdd: crop.parameters.temperature.full_transpiration_gdd,
            wet_bare_soil_coefficient: parameters.wet_bare_soil_coefficient,
            senescence_exponent: parameters.senescence_exponent,
            evaporation_limit_enabled: false,
        });
        normalized_transpiration += potential.transpiration / 5.0;
        let mut productivity = crop.parameters.productivity.normalized_water_productivity;
        let yield_formation_switch = calendar
            .calendar_days
            .harvest_index
            .map_or(0.0, |duration| {
                calibration_yield_formation_switch(
                    crop,
                    day,
                    calendar.calendar_days.flowering,
                    f64::from(crop.parameters.productivity.reference_harvest_index)
                        / f64::from(duration),
                )
            });
        productivity *= 1.0
            - (1.0
                - f64::from(
                    crop.parameters
                        .productivity
                        .yield_formation_water_productivity,
                ) / 100.0)
                * yield_formation_switch;
        if effects.water_productivity_reduction > 0
            && normalized_transpiration > 0.0
            && stressed_seasonal_potential > f64::EPSILON
        {
            if normalized_transpiration < stressed_seasonal_potential {
                productivity *= 1.0
                    - f64::from(effects.water_productivity_reduction) / 100.0
                        * (normalized_transpiration / stressed_seasonal_potential).powi(2);
            } else {
                productivity *= 1.0 - f64::from(effects.water_productivity_reduction) / 100.0;
            }
        }
        normalized_biomass += productivity
            * (1.0 - f64::from(effects.stomatal_reduction) / 100.0)
            * (potential.transpiration / 5.0);
    }
    normalized_biomass
}

#[expect(
    clippy::too_many_arguments,
    reason = "the parameters mirror CCiNoWaterStressSF state without hidden globals"
)]
fn fertility_reference_canopy(
    crop: &PreparedCrop,
    canopy_timing: &crate::canopy::CanopyTiming,
    calendar: &ReferenceCropCalendar,
    effects: &ReferenceStressEffects,
    days_to_full_canopy: i32,
    gdd_to_full_canopy: i32,
    day_per_gdd: f64,
    day: i32,
    sum_gdd: f64,
) -> f64 {
    let gdd_mode = crop.cycle.uses_growing_degree_days();
    let effective_day = adjusted_canopy_day_with_timing(crop, canopy_timing, day);
    let effective_gdd = adjusted_canopy_gdd_with_timing(crop, canopy_timing, sum_gdd);
    let driving_calendar = crop.cycle.driving_calendar();
    let time = if gdd_mode {
        effective_gdd
    } else {
        f64::from(effective_day)
    };
    let emergence = if gdd_mode {
        f64::from(driving_calendar.emergence)
    } else {
        f64::from(calendar.calendar_days.emergence)
    };
    let senescence = if gdd_mode {
        f64::from(driving_calendar.senescence)
    } else {
        f64::from(calendar.calendar_days.senescence)
    };
    let after_maturity = if gdd_mode {
        roundc_i32(sum_gdd) > driving_calendar.maturity
    } else {
        day > calendar.calendar_days.maturity
    };
    if after_maturity {
        return 0.0;
    }
    let growth = if gdd_mode {
        driving_calendar.canopy_growth_coefficient
    } else {
        calendar.calendar_days.canopy_growth_coefficient
    } * (1.0 - f64::from(effects.growth_reduction) / 100.0);
    let decline = if gdd_mode {
        driving_calendar.canopy_decline_coefficient
    } else {
        calendar.calendar_days.canopy_decline_coefficient
    };
    let adjusted_maximum = (1.0 - f64::from(effects.maximum_cover_reduction) / 100.0)
        * crop.parameters.canopy.maximum_cover;
    let base = raw_canopy_cover(
        time,
        emergence,
        senescence,
        crop.parameters.canopy.initial_seedling_cover,
        adjusted_maximum,
        growth,
        decline,
        crop.parameters.canopy.maximum_cover,
    );
    let decline_start = if gdd_mode {
        f64::from(gdd_to_full_canopy)
    } else {
        f64::from(days_to_full_canopy)
    };
    let time_scale = if gdd_mode { day_per_gdd } else { 1.0 };
    if effective_day <= days_to_full_canopy
        || time <= decline_start
        || effects.canopy_decline <= crate::compat::ZERO_THRESHOLD
        || decline_start >= senescence
    {
        return base;
    }
    if time < senescence {
        return (base
            - time_scale * effects.canopy_decline / 100.0 * (time - decline_start).powi(2)
                / (senescence - decline_start))
            .max(0.0);
    }
    let cover_at_senescence = canopy_at_time(
        senescence - emergence,
        crop.parameters.canopy.initial_seedling_cover,
        growth,
        adjusted_maximum,
    );
    let continuing = cover_at_senescence
        - time_scale * effects.canopy_decline / 100.0 * (time - decline_start).powi(2)
            / (senescence - decline_start);
    if continuing < 0.0 {
        return 0.0;
    }
    let adjusted_at_senescence = cover_at_senescence
        - time_scale * effects.canopy_decline / 100.0 * (senescence - decline_start);
    if adjusted_at_senescence < 0.001 {
        return 0.0;
    }
    let adjusted_decline =
        decline * (adjusted_at_senescence + 2.29) / (crop.parameters.canopy.maximum_cover + 2.29);
    let late_season = adjusted_at_senescence
        * (1.0
            - 0.05
                * (((time - senescence) * adjusted_decline * 3.33
                    / (adjusted_at_senescence + 2.29))
                    .exp()
                    - 1.0));
    continuing.min(late_season).max(0.0)
}

fn regress_stress_biomass<const N: usize>(
    matrix: &[(f64, f64, f64); N],
) -> Result<(f64, f64, f64), Error> {
    let mean = matrix.iter().fold((0.0, 0.0, 0.0), |sum, row| {
        (sum.0 + row.0, sum.1 + row.1, sum.2 + row.2)
    });
    let count = f64::from(u32::try_from(N).unwrap_or(1));
    let mean = (mean.0 / count, mean.1 / count, mean.2 / count);
    let sums = matrix.iter().fold([0.0; 5], |mut sums, row| {
        let y = row.0 - mean.0;
        let x1 = row.1 - mean.1;
        let x2 = row.2 - mean.2;
        sums[0] += x1 * y;
        sums[1] += x2 * y;
        sums[2] += x1 * x1;
        sums[3] += x2 * x2;
        sums[4] += x1 * x2;
        sums
    });
    if roundc_i32(sums[4] * 1_000.0).abs() == 0 {
        return Err(Error::InvalidSimulationConfiguration(
            "stress–biomass regression is singular",
        ));
    }
    let quadratic =
        (sums[0] - sums[1] * sums[2] / sums[4]) / (sums[4] - sums[2] * sums[3] / sums[4]);
    let linear = (sums[0] - quadratic * sums[4]) / sums[2];
    let intercept = mean.0 - linear * mean.1 - quadratic * mean.2;
    Ok((intercept, linear, quadratic))
}

fn invert_stress_biomass_relationship(
    selected_stress: i32,
    intercept: f64,
    linear: f64,
    quadratic: f64,
) -> f64 {
    if selected_stress == 0 {
        return 1.0;
    }
    let mut biomass_low = 100_i8;
    let mut stress_low = 0.0;
    loop {
        let biomass_top = biomass_low;
        let stress_top = stress_low;
        biomass_low -= 1;
        let biomass = f64::from(biomass_low);
        stress_low = intercept + linear * biomass + quadratic * biomass * biomass;
        if stress_low >= f64::from(selected_stress) || biomass_low <= 0 || stress_low >= 99.99 {
            let bounded_low = if stress_low >= 99.99 {
                100.0
            } else {
                stress_low
            };
            let attainable = if (bounded_low - stress_top).abs() < 0.001 {
                f64::from(biomass_top)
            } else {
                f64::from(biomass_top)
                    - (f64::from(selected_stress) - stress_top) / (bounded_low - stress_top)
            };
            return attainable / 100.0;
        }
    }
}

fn reference_crop_calendar(
    crop: &PreparedCrop,
    first_crop_day_number: i32,
    temperatures: &[ReferenceTemperature],
) -> Result<ReferenceCropCalendar, Error> {
    let date = CalendarDate::from_day_number(first_crop_day_number)?;
    let reference_date = CalendarDate::new(1901, date.month(), date.day())?;
    let start_offset = usize::try_from(reference_date.day_number() - 1)
        .map_err(|_| Error::InvalidSimulationConfiguration("invalid reference crop date"))?;
    let timing = derive_canopy_timing(crop);
    let mut calendar = ReferenceCropCalendar {
        calendar_days: crop.cycle.calendar_days().clone(),
        full_canopy: timing.days_to_full_canopy,
        start_offset,
    };
    if !crop.cycle.uses_growing_degree_days() {
        return Ok(calendar);
    }
    let gdd_method = required_gdd_method(crop)?;
    let driving_calendar = crop.cycle.driving_calendar();
    if crop.parameters.planting_method == PlantingMethod::Regrowth
        && timing.days_to_initial_cover.is_some_and(|days| days > 0)
    {
        let days_to_initial_cover = timing.days_to_initial_cover.unwrap_or(0);
        let gdd_to_initial_cover = timing.gdd_to_initial_cover.unwrap_or(0);
        let remaining_gdd =
            timing.gdd_to_full_canopy - driving_calendar.emergence - gdd_to_initial_cover;
        calendar.full_canopy = calendar.calendar_days.emergence
            + days_to_initial_cover
            + reference_days_for_gdd(
                &crop.parameters.temperature,
                gdd_method,
                temperatures,
                start_offset,
                remaining_gdd,
            )?;
    } else {
        calendar.calendar_days.emergence = reference_days_for_gdd(
            &crop.parameters.temperature,
            gdd_method,
            temperatures,
            start_offset,
            driving_calendar.emergence,
        )?;
        calendar.full_canopy = reference_days_for_gdd(
            &crop.parameters.temperature,
            gdd_method,
            temperatures,
            start_offset,
            timing.gdd_to_full_canopy,
        )?;
    }
    if crop.parameters.kind != CropKind::Forage {
        calendar.calendar_days.senescence = reference_days_for_gdd(
            &crop.parameters.temperature,
            gdd_method,
            temperatures,
            start_offset,
            driving_calendar.senescence,
        )?;
        calendar.calendar_days.maturity = reference_days_for_gdd(
            &crop.parameters.temperature,
            gdd_method,
            temperatures,
            start_offset,
            driving_calendar.maturity,
        )?;
    }
    adjust_reference_reproductive_calendar(
        crop,
        gdd_method,
        temperatures,
        start_offset,
        &mut calendar,
    )?;
    calendar.calendar_days.canopy_growth_coefficient = f64::from(timing.gdd_to_full_canopy)
        / f64::from(calendar.full_canopy)
        * driving_calendar.canopy_growth_coefficient;
    calendar.calendar_days.canopy_decline_coefficient = reference_decline_coefficient(
        crop,
        gdd_method,
        temperatures,
        start_offset,
        calendar.calendar_days.senescence,
    )?;
    Ok(calendar)
}

fn required_gdd_method(crop: &PreparedCrop) -> Result<GrowingDegreeDayMethod, Error> {
    crop.cycle
        .gdd_method()
        .ok_or(Error::InvalidCropConfiguration(
            "GDD crop cycle is missing its GDD calculation method",
        ))
}

fn adjust_reference_reproductive_calendar(
    crop: &PreparedCrop,
    gdd_method: GrowingDegreeDayMethod,
    temperatures: &[ReferenceTemperature],
    start_offset: usize,
    calendar: &mut ReferenceCropCalendar,
) -> Result<(), Error> {
    let driving_calendar = crop.cycle.driving_calendar();
    if matches!(&crop.parameters.kind, CropKind::Grain | CropKind::Tuber) {
        calendar.calendar_days.flowering = reference_days_for_gdd(
            &crop.parameters.temperature,
            gdd_method,
            temperatures,
            start_offset,
            driving_calendar.flowering,
        )?;
    }
    calendar.calendar_days.flowering_length = if crop.parameters.kind == CropKind::Grain {
        reference_days_for_gdd(
            &crop.parameters.temperature,
            gdd_method,
            temperatures,
            (start_offset + usize::try_from(calendar.calendar_days.flowering).unwrap_or(0)) % 365,
            driving_calendar.flowering_length,
        )?
    } else if crop.parameters.kind == CropKind::Tuber {
        0
    } else {
        calendar.calendar_days.flowering_length
    };
    let harvest_index_offset = if matches!(&crop.parameters.kind, CropKind::Grain | CropKind::Tuber)
    {
        usize::try_from(calendar.calendar_days.flowering).unwrap_or(0)
    } else {
        0
    };
    calendar.calendar_days.harvest_index = driving_calendar
        .harvest_index
        .map(|target| {
            reference_days_for_gdd(
                &crop.parameters.temperature,
                gdd_method,
                temperatures,
                (start_offset + harvest_index_offset) % 365,
                target,
            )
        })
        .transpose()?;
    Ok(())
}

fn reference_days_for_gdd(
    crop_temperature: &CropTemperature,
    gdd_method: GrowingDegreeDayMethod,
    temperatures: &[ReferenceTemperature],
    start_offset: usize,
    target_gdd: i32,
) -> Result<i32, Error> {
    if target_gdd <= 0 {
        return Ok(0);
    }
    let mut remaining = f64::from(target_gdd);
    let mut days = 0_i32;
    let mut index = start_offset;
    let mut iterations = 0_i32;
    while remaining > 0.1 {
        let temperature = &temperatures[index % 365];
        let daily = growing_degree_days(
            crop_temperature.base,
            crop_temperature.upper,
            temperature.minimum,
            temperature.maximum,
            gdd_method,
        );
        if daily <= 0.0 {
            iterations += 1;
            if iterations > 36_500 {
                return Err(Error::InvalidSimulationConfiguration(
                    "reference climate cannot accumulate the requested GDD stage",
                ));
            }
        } else if daily <= remaining || roundc_i32((daily - remaining) / remaining) >= 1 {
            days += 1;
        }
        remaining -= daily;
        index += 1;
    }
    Ok(days)
}

fn series_gdd_for_days(
    crop_temperature: &CropTemperature,
    gdd_method: GrowingDegreeDayMethod,
    first_day_number: i32,
    temperatures: &[ReferenceTemperature],
    period_first_day_number: i32,
    days: i32,
) -> Result<i32, Error> {
    let start = period_first_day_number
        .checked_sub(first_day_number)
        .and_then(|offset| usize::try_from(offset).ok())
        .ok_or(Error::InvalidSimulationConfiguration(
            "temperature series starts after the crop period",
        ))?;
    let count = usize::try_from(days.max(0)).unwrap_or(0);
    let end = start
        .checked_add(count)
        .filter(|end| *end <= temperatures.len())
        .ok_or(Error::InvalidSimulationConfiguration(
            "temperature series does not cover salinity calibration",
        ))?;
    let sum: f64 = temperatures[start..end]
        .iter()
        .map(|temperature| {
            growing_degree_days(
                crop_temperature.base,
                crop_temperature.upper,
                temperature.minimum,
                temperature.maximum,
                gdd_method,
            )
        })
        .sum();
    Ok(roundc_i32(sum))
}

fn reference_decline_coefficient(
    crop: &PreparedCrop,
    gdd_method: GrowingDegreeDayMethod,
    temperatures: &[ReferenceTemperature],
    start_offset: usize,
    days_to_senescence: i32,
) -> Result<f64, Error> {
    let maximum = crop.parameters.canopy.maximum_cover;
    let driving_calendar = crop.cycle.driving_calendar();
    let gdd_decline = driving_calendar.canopy_decline_coefficient;
    let complete_decline =
        roundc_i32((maximum + 2.29) / (gdd_decline * 3.33) * 21.0_f64.ln() + 0.5);
    let (decline_gdd, ending_cover) = if driving_calendar.senescence + complete_decline
        <= driving_calendar.maturity
    {
        (complete_decline, 0.0)
    } else {
        let duration = if driving_calendar.senescence < driving_calendar.maturity {
            driving_calendar.maturity - driving_calendar.senescence
        } else {
            5
        };
        let cover = maximum
            * (1.0
                - 0.05
                    * ((f64::from(duration) * gdd_decline * 3.33 / (maximum + 2.29)).exp() - 1.0));
        (duration, cover)
    };
    let decline_days = reference_days_for_gdd(
        &crop.parameters.temperature,
        gdd_method,
        temperatures,
        (start_offset + usize::try_from(days_to_senescence).unwrap_or(0)) % 365,
        decline_gdd,
    )?;
    if decline_days <= 0 {
        return Err(Error::InvalidSimulationConfiguration(
            "reference canopy decline has zero calendar duration",
        ));
    }
    Ok((maximum + 2.29) / f64::from(decline_days)
        * (1.0 + (1.0 - ending_cover / maximum) / 0.05).ln()
        / 3.33)
}

fn reference_growth_starts(
    cycle: &PreparedCropCycle,
    calendar: &ReferenceCropCalendar,
    day: i32,
    sum_gdd: f64,
) -> bool {
    if cycle.uses_growing_degree_days() {
        sum_gdd > f64::from(cycle.driving_calendar().emergence)
    } else {
        day == calendar.calendar_days.emergence + 1
    }
}

#[expect(
    clippy::too_many_arguments,
    reason = "the arguments mirror the source canopy equation without global state"
)]
fn raw_canopy_cover(
    time: f64,
    emergence: f64,
    senescence: f64,
    initial: f64,
    maximum: f64,
    growth: f64,
    decline: f64,
    decline_reference_maximum: f64,
) -> f64 {
    if time < 1.0 || initial <= f64::EPSILON || time <= emergence {
        return 0.0;
    }
    if time < senescence {
        return canopy_at_time(time - emergence, initial, growth, maximum).min(1.0);
    }
    if maximum < 0.001 {
        return 0.0;
    }
    let cover_at_senescence = canopy_at_time(senescence - emergence, initial, growth, maximum);
    if cover_at_senescence < 0.001 {
        return 0.0;
    }
    let adjusted_decline =
        decline * (cover_at_senescence + 2.29) / (decline_reference_maximum + 2.29);
    (cover_at_senescence
        * (1.0
            - 0.05
                * (((time - senescence) * 3.33 * adjusted_decline / (cover_at_senescence + 2.29))
                    .exp()
                    - 1.0)))
        .max(0.0)
}

/// Returns the source model's carbon-dioxide multiplier for normalized productivity.
///
/// `co2` is atmospheric concentration in parts per million by volume,
/// `normalized_water_productivity` is in grams per square metre, and
/// `adaptation` is percent. The returned multiplier is dimensionless.
#[must_use]
pub fn co2_productivity_adjustment(
    co2: f64,
    normalized_water_productivity: f64,
    adaptation: i8,
) -> f64 {
    let crop_type = if normalized_water_productivity >= 40.0 {
        0.0
    } else if normalized_water_productivity <= 20.0 {
        1.0
    } else {
        (40.0 - normalized_water_productivity) / (40.0 - 20.0)
    };
    let sink = (f64::from(adaptation) / 100.0).clamp(0.0, 1.0);
    let old = if co2 <= 550.0 {
        let weight = if co2 <= REFERENCE_CO2 {
            0.0
        } else if co2 >= 550.0 {
            1.0
        } else {
            1.0 - (550.0 - co2) / (550.0 - REFERENCE_CO2)
        };
        Some(
            (co2 / REFERENCE_CO2)
                / (1.0
                    + (co2 - REFERENCE_CO2)
                        * ((1.0 - weight) * 0.000_138
                            + weight * (0.000_138 * sink + 0.001_165 * (1.0 - sink)))),
        )
    } else {
        None
    };
    let adjusted = if co2 > REFERENCE_CO2 {
        if co2 >= 2_000.0 {
            Some(1.58)
        } else {
            let shape = -4.618_24 - 3.438_31 * sink - 5.325_87 * sink * sink;
            let relative = (co2 - REFERENCE_CO2) / (2_000.0 - REFERENCE_CO2);
            Some(1.0 + 0.58 * ((relative * shape).exp() - 1.0) / (shape.exp() - 1.0))
        }
    } else {
        None
    };
    let selected = if co2 <= REFERENCE_CO2 {
        old.unwrap_or(1.0)
    } else if co2 <= 550.0 {
        old.unwrap_or(1.0).min(adjusted.unwrap_or(1.0))
    } else {
        adjusted.unwrap_or(1.0)
    };
    1.0 + crop_type * (selected - 1.0)
}

#[cfg(test)]
mod tests {
    use assertables::assert_eq_f64;

    use super::{
        BiomassInputs, BiomassState, REFERENCE_CO2, advance_biomass, co2_productivity_adjustment,
    };
    use crate::crop::CropProductivity;

    #[test]
    fn reference_concentration_keeps_productivity_unchanged() {
        assert_eq_f64!(
            co2_productivity_adjustment(REFERENCE_CO2, 15.0, 100),
            1.0_f64
        );
    }

    #[test]
    fn c3_productivity_reaches_the_source_cap_at_two_thousand() {
        assert_eq_f64!(co2_productivity_adjustment(2_000.0, 15.0, 100), 1.58_f64);
    }

    #[test]
    fn c4_productivity_has_no_carbon_dioxide_adjustment() {
        assert_eq_f64!(co2_productivity_adjustment(2_000.0, 40.0, 100), 1.0_f64);
    }

    #[test]
    fn actual_and_unrestricted_biomass_use_their_own_yield_formation_clocks() {
        let mut state = BiomassState::new();
        let daily = advance_biomass(
            &mut state,
            &CropProductivity {
                normalized_water_productivity: 15.0,
                yield_formation_water_productivity: 50,
                elevated_co2_adaptation: 100,
                reference_harvest_index: 50,
                pre_flowering_harvest_index_increase: Some(0),
                vegetative_growth_impact_coefficient: Some(0.0),
                stomatal_closure_impact_coefficient: Some(0.0),
                maximum_harvest_index_increase: Some(0),
            },
            &BiomassInputs {
                reference_et: 5.0,
                actual_transpiration: 5.0,
                crop_transpiration: 5.0,
                unrestricted_transpiration: 5.0,
                co2: REFERENCE_CO2,
                actual_yield_formation_switch: 1.0,
                unrestricted_yield_formation_switch: 0.0,
                fertility_wp_reduction: 0.0,
                normalized_fertility_potential_transpiration: 0.0,
                fertility_biomass_fraction: 1.0,
            },
        );

        assert!((daily.actual - 0.075).abs() < f64::EPSILON);
        assert!((daily.unrestricted - 0.15).abs() < f64::EPSILON);
    }
}
