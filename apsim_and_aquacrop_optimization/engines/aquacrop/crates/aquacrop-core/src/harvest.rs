//! Reference harvest-index development and stress-response primitives.

use std::f64::consts::PI;

use crate::compat::{roundc_i8, roundc_i32};
use crate::crop::{CropKind, PlantingMethod, PreparedCrop};
use crate::et::temperature_stress_coefficient;
use crate::stress::stress_coefficient;

const INITIAL_HARVEST_INDEX: i32 = 1;

/// Mutable limits retained while the reference harvest index develops.
#[derive(Debug)]
pub struct HarvestIndexState {
    /// Maximum reference harvest index still attainable, in percent.
    pub attainable_limit: i32,
}

/// Reference harvest index and lag-phase progress for one day.
#[derive(Debug)]
pub struct DailyHarvestIndex {
    /// Reference harvest index for the day, in percent.
    pub percent: f64,
    /// Progress through the logistic lag phase, in percent.
    pub lag_phase: i8,
}

/// Accumulators for water-stress effects after yield formation begins.
#[derive(Debug)]
pub struct PostYieldStressState {
    /// Accumulated leaf-expansion correction weighted by its response period.
    pub leaf_correction_sum: f64,
    /// Accumulated stomatal correction weighted by its response period.
    pub stomatal_correction_sum: f64,
    /// Current positive multiplier from restricted leaf expansion.
    pub leaf_multiplier: f64,
    /// Current negative multiplier from stomatal closure.
    pub stomatal_multiplier: f64,
}

/// Daily coefficients and response periods for post-yield stress.
#[derive(Debug)]
pub struct PostYieldStressInputs {
    /// Current day counted from the start of yield formation.
    pub day_in_yield_formation: i32,
    /// Duration of the leaf-expansion response period, in days.
    pub leaf_response: i32,
    /// Duration of the stomatal response period, in days.
    pub stomatal_response: i32,
    /// Leaf-expansion stress coefficient for the current day.
    pub leaf_stress_coefficient: f64,
    /// Stomatal stress coefficient for the current day.
    pub stomatal_stress_coefficient: f64,
    /// Positive harvest-index impact coefficient, or `None` when disabled.
    pub leaf_impact_coefficient: Option<f64>,
    /// Negative harvest-index impact coefficient, or `None` when disabled.
    pub stomatal_impact_coefficient: Option<f64>,
}

impl HarvestIndexState {
    /// Initializes the attainable limit from the crop's reference value.
    #[must_use]
    pub fn new(crop: &PreparedCrop) -> Self {
        Self {
            attainable_limit: crop.parameters.productivity.reference_harvest_index,
        }
    }
}

impl PostYieldStressState {
    /// Creates neutral post-yield stress accumulators.
    #[must_use]
    pub const fn new() -> Self {
        Self {
            leaf_correction_sum: 0.0,
            stomatal_correction_sum: 0.0,
            leaf_multiplier: 1.0,
            stomatal_multiplier: 1.0,
        }
    }
}

impl Default for PostYieldStressState {
    fn default() -> Self {
        Self::new()
    }
}

/// Derives the daily harvest-index growth rate completed by the reference setup.
///
/// Prepared crop fields retain the units documented on [`PreparedCrop`]; a defined result is in
/// harvest-index percentage points per calendar day. `None` means the crop has
/// no applicable harvest-index buildup period.
#[must_use]
pub fn harvest_index_rate(crop: &PreparedCrop) -> Option<f64> {
    let harvest_index = f64::from(crop.parameters.productivity.reference_harvest_index);
    let calendar = crop.cycle.calendar_days();
    let duration = calendar.harvest_index;
    if matches!(
        &crop.parameters.kind,
        CropKind::Vegetative | CropKind::Forage
    ) {
        if let Some(duration) = duration.filter(|duration| *duration > 0) {
            let effective_duration = duration.min(calendar.maturity);
            Some((harvest_index / f64::from(effective_duration)).min(100.0))
        } else {
            Some(100.0)
        }
    } else {
        duration
            .filter(|duration| *duration > 0)
            .map(|duration| harvest_index / f64::from(duration))
    }
}

/// Calculates the unstressed reference harvest index for one calendar day.
///
/// `day_after_start` is zero on the crop's first day, matching the value passed
/// by the reference daily loop. `delayed_days` is in calendar days, canopy
/// arguments are soil-cover fractions, and `minimum_canopy` is percent
/// soil cover. Prepared crop fields retain the units documented on [`PreparedCrop`].
#[must_use]
pub fn reference_harvest_index_for_day(
    crop: &PreparedCrop,
    state: &mut HarvestIndexState,
    day_after_start: i32,
    delayed_days: i32,
    actual_canopy_cover: f64,
    withered_canopy_cover: f64,
    minimum_canopy: i8,
) -> DailyHarvestIndex {
    let maximum = crop.parameters.productivity.reference_harvest_index;
    let Some(rate) = harvest_index_rate(crop) else {
        return DailyHarvestIndex {
            percent: 0.0,
            lag_phase: 0,
        };
    };
    if crop.parameters.planting_method == PlantingMethod::Regrowth
        && matches!(
            &crop.parameters.kind,
            CropKind::Vegetative | CropKind::Forage
        )
    {
        return DailyHarvestIndex {
            percent: f64::from(maximum),
            lag_phase: 100,
        };
    }
    let time = day_after_start - delayed_days - crop.cycle.calendar_days().flowering;
    if time <= 0 {
        return DailyHarvestIndex {
            percent: 0.0,
            lag_phase: 0,
        };
    }
    let (mut index, lag_phase) = if rate > 99.0 {
        (f64::from(maximum), 100)
    } else {
        let Some(growth) = harvest_index_growth_coefficient(f64::from(maximum), rate) else {
            return DailyHarvestIndex {
                percent: 0.0,
                lag_phase: 0,
            };
        };
        let (switch_day, linear_growth) = day_switch_to_linear(maximum, rate, growth);
        let lag = if time < switch_day {
            roundc_i8(100.0 * (f64::from(time) / f64::from(switch_day)))
        } else {
            100
        };
        let mut value = if time < switch_day
            || matches!(
                &crop.parameters.kind,
                CropKind::Tuber | CropKind::Vegetative | CropKind::Forage
            ) {
            logistic_harvest_index(maximum, growth, time)
        } else {
            logistic_harvest_index(maximum, growth, switch_day)
                + linear_growth * f64::from(time - switch_day)
        };
        if time >= switch_day
            && matches!(
                &crop.parameters.kind,
                CropKind::Tuber | CropKind::Vegetative | CropKind::Forage
            )
            && value >= 0.9799 * f64::from(maximum)
        {
            value = f64::from(maximum);
        }
        value = value.min(f64::from(maximum));
        if value <= f64::from(INITIAL_HARVEST_INDEX) + 0.4 {
            value = 0.0;
        }
        if f64::from(maximum) - value < 0.4 {
            value = f64::from(maximum);
        }
        (value, lag)
    };

    let maximum_time = roundc_i32(f64::from(maximum) / rate);
    if !matches!(
        &crop.parameters.kind,
        CropKind::Vegetative | CropKind::Forage
    ) {
        let initial_canopy = if crop.parameters.planting_method == PlantingMethod::Regrowth {
            crop.parameters.canopy.initial_regrowth_cover
        } else {
            crop.parameters.canopy.initial_seedling_cover
        };
        let mut canopy_threshold = f64::from(minimum_canopy);
        if 100.0 * initial_canopy > f64::from(minimum_canopy) {
            canopy_threshold = 100.0 * (1.1 * initial_canopy);
        }
        if state.attainable_limit == maximum
            && time <= maximum_time
            && actual_canopy_cover + f64::EPSILON <= canopy_threshold / 100.0
            && withered_canopy_cover > f64::EPSILON
            && actual_canopy_cover < withered_canopy_cover
        {
            state.attainable_limit = roundc_i32(index);
        }
    }
    if index > f64::from(state.attainable_limit) {
        index = f64::from(state.attainable_limit);
    }
    DailyHarvestIndex {
        percent: index,
        lag_phase,
    }
}

pub(crate) fn actual_yield_formation_switch(
    crop: &PreparedCrop,
    day_after_start: i32,
    daily_harvest_index: &DailyHarvestIndex,
) -> f64 {
    let calendar = crop.cycle.calendar_days();
    if !matches!(crop.parameters.kind, CropKind::Grain | CropKind::Tuber)
        || daily_harvest_index.percent <= 0.0
    {
        return 0.0;
    }
    let Some(rate) = harvest_index_rate(crop) else {
        return 1.0;
    };
    let duration =
        roundc_i32(f64::from(crop.parameters.productivity.reference_harvest_index) / rate);
    if duration <= 0 {
        return 1.0;
    }
    if crop.parameters.development.determinacy_linked_to_flowering {
        return f64::from(daily_harvest_index.lag_phase) / 100.0;
    }
    let day_after_flowering = day_after_start - calendar.flowering;
    yield_formation_ramp(day_after_flowering, duration)
}

pub(crate) fn unrestricted_yield_formation_switch(
    crop: &PreparedCrop,
    virtual_canopy_day: i32,
) -> f64 {
    let calendar = crop.cycle.calendar_days();
    let Some(rate) = harvest_index_rate(crop) else {
        return 0.0;
    };
    if !matches!(crop.parameters.kind, CropKind::Grain | CropKind::Tuber)
        || crop
            .parameters
            .productivity
            .yield_formation_water_productivity
            >= 100
        || rate <= 0.0
        || virtual_canopy_day < calendar.flowering
    {
        return 0.0;
    }
    let duration =
        roundc_i32(f64::from(crop.parameters.productivity.reference_harvest_index) / rate);
    let day_after_flowering = virtual_canopy_day - calendar.flowering;
    if duration > 0 {
        yield_formation_ramp(day_after_flowering, duration)
    } else {
        1.0
    }
}

pub(crate) fn calibration_yield_formation_switch(
    crop: &PreparedCrop,
    crop_day: i32,
    days_to_flowering: i32,
    harvest_index_rate: f64,
) -> f64 {
    if !matches!(crop.parameters.kind, CropKind::Grain | CropKind::Tuber)
        || crop
            .parameters
            .productivity
            .yield_formation_water_productivity
            >= 100
        || crop_day <= days_to_flowering
    {
        return 0.0;
    }
    let duration = roundc_i32(
        f64::from(crop.parameters.productivity.reference_harvest_index) / harvest_index_rate,
    );
    let switch_days = if crop.parameters.development.determinacy_linked_to_flowering {
        let Some(growth) = harvest_index_growth_coefficient(
            f64::from(crop.parameters.productivity.reference_harvest_index),
            harvest_index_rate,
        ) else {
            return 0.0;
        };
        day_switch_to_linear(
            crop.parameters.productivity.reference_harvest_index,
            harvest_index_rate,
            growth,
        )
        .0
    } else {
        roundc_i32(f64::from(duration) / 3.0)
    };
    calibration_yield_formation_ramp(crop_day - days_to_flowering, duration, switch_days)
}

fn calibration_yield_formation_ramp(
    day_after_flowering: i32,
    duration: i32,
    switch_days: i32,
) -> f64 {
    if duration > 0 && switch_days > 0 {
        (f64::from(day_after_flowering) / f64::from(switch_days)).min(1.0)
    } else {
        1.0
    }
}

fn yield_formation_ramp(day_after_flowering: i32, duration: i32) -> f64 {
    let first_third = f64::from(duration) / 3.0;
    if f64::from(day_after_flowering) < first_third {
        f64::from(day_after_flowering) / first_third
    } else {
        1.0
    }
}

/// Relative-biomass range over which pre-yield stress can increase harvest index.
///
/// `increase` is the permitted harvest-index increase in percent; the
/// returned relative-biomass range is dimensionless.
#[must_use]
pub fn pre_yield_biomass_range(increase: i32) -> f64 {
    if increase <= 0 {
        0.0
    } else {
        (f64::from(increase).ln() / 0.0562 / 100.0).min(1.0)
    }
}

/// Harvest-index multiplier caused by biomass restriction before yield formation.
///
/// `relative_biomass` and `range` are dimensionless fractions,
/// `increase` is percent, and the result is dimensionless.
#[must_use]
pub fn pre_yield_harvest_index_multiplier(relative_biomass: f64, range: f64, increase: i8) -> f64 {
    let start = 1.0 - range;
    let end = 1.0;
    let maximum = start + (2.0 / 3.0) * (end - start);
    if relative_biomass <= start {
        1.0
    } else if relative_biomass <= maximum {
        1.0 + (1.0 + (PI * (1.5 - (relative_biomass - start) / (maximum - start))).sin())
            * (f64::from(increase) / 200.0)
    } else if relative_biomass <= end {
        1.0 + (1.0 + (PI * (0.5 + (relative_biomass - maximum) / (end - maximum))).sin())
            * (f64::from(increase) / 200.0)
    } else {
        1.0
    }
}

/// Fraction of the flowering distribution active on one day.
///
/// Both arguments are calendar-day counts within flowering; the result is a
/// dimensionless fraction.
#[must_use]
pub fn flowering_fraction(day_in_flowering: i32, flowering_length_days: i32) -> f64 {
    if flowering_length_days <= 1 {
        return 1.0;
    }
    let current = cumulative_flowering_fraction(day_in_flowering, flowering_length_days);
    let previous = cumulative_flowering_fraction(day_in_flowering - 1, flowering_length_days);
    if (previous - current).abs() < 1.0e-12 {
        0.0
    } else {
        #[expect(
            clippy::manual_midpoint,
            reason = "the reference adds successive fractions before dividing by two"
        )]
        let average = (previous + current) / 2.0;
        100.0 * average / f64::from(flowering_length_days)
    }
}

/// Combined water, cold, and heat coefficient for pollination.
///
/// Water depletion and a defined threshold are dimensionless fractions.
/// Defined temperature thresholds and both observations are in degrees Celsius;
/// missing thresholds disable their corresponding stress. The result is dimensionless.
#[must_use]
pub fn pollination_stress_coefficient(
    relative_water_depletion: f64,
    water_depletion_threshold: Option<f64>,
    minimum_temperature: f64,
    maximum_temperature: f64,
    cold_onset: Option<f64>,
    heat_onset: Option<f64>,
) -> f64 {
    let water = water_depletion_threshold.map_or(1.0, |threshold| {
        stress_coefficient(relative_water_depletion, threshold, 1.0, 0.0)
    });
    let cold = temperature_stress_coefficient(
        cold_onset.map(|onset| onset - 5.0),
        cold_onset,
        minimum_temperature,
    );
    let heat = temperature_stress_coefficient(
        heat_onset.map(|onset| onset + 5.0),
        heat_onset,
        maximum_temperature,
    );
    water.min(cold).min(heat)
}

/// Accumulates the two post-yield water-stress responses for one day.
///
/// State and input fields retain their field-documented units: response times
/// are calendar-day counts, stress coefficients and impacts are dimensionless.
pub fn advance_post_yield_stress(state: &mut PostYieldStressState, inputs: &PostYieldStressInputs) {
    if inputs.day_in_yield_formation <= 0 {
        return;
    }
    if inputs.leaf_response > 0
        && inputs.day_in_yield_formation <= inputs.leaf_response
        && let Some(impact) = inputs.leaf_impact_coefficient
    {
        let daily = 1.0 + (1.0 - inputs.leaf_stress_coefficient) / impact;
        state.leaf_correction_sum += daily / f64::from(inputs.leaf_response);
        state.leaf_multiplier = f64::from(inputs.leaf_response)
            / f64::from(inputs.day_in_yield_formation)
            * state.leaf_correction_sum;
    }
    if inputs.stomatal_response > 0
        && inputs.day_in_yield_formation <= inputs.stomatal_response
        && let Some(impact) = inputs.stomatal_impact_coefficient
    {
        let daily = if inputs.stomatal_stress_coefficient > 0.001 {
            (0.10 * inputs.stomatal_stress_coefficient.ln()).exp()
                * (1.0 - (1.0 - inputs.stomatal_stress_coefficient) / impact)
        } else {
            0.0
        };
        state.stomatal_correction_sum += daily / f64::from(inputs.stomatal_response);
        state.stomatal_multiplier = f64::from(inputs.stomatal_response)
            / f64::from(inputs.day_in_yield_formation)
            * state.stomatal_correction_sum;
    }
}

/// Combines leaf and stomatal effects over response periods of unequal length.
///
/// Both response arguments are calendar-day counts. Enable flags and state
/// multipliers are dimensionless; the result is dimensionless.
#[must_use]
pub fn post_yield_harvest_index_multiplier(
    state: &PostYieldStressState,
    leaf_response: i32,
    stomatal_response: i32,
    leaf_effect_enabled: bool,
    stomatal_effect_enabled: bool,
) -> f64 {
    if leaf_response == 0 && stomatal_response == 0 {
        1.0
    } else if stomatal_response == 0 || !stomatal_effect_enabled {
        state.leaf_multiplier
    } else if leaf_response == 0 || !leaf_effect_enabled {
        state.stomatal_multiplier
    } else if leaf_response <= stomatal_response {
        state.stomatal_multiplier
            * (f64::from(leaf_response) * state.leaf_multiplier
                + f64::from(stomatal_response - leaf_response))
            / f64::from(stomatal_response)
    } else {
        state.leaf_multiplier
            * (f64::from(stomatal_response) * state.stomatal_multiplier
                + f64::from(leaf_response - stomatal_response))
            / f64::from(leaf_response)
    }
}

/// Applies pre- and post-yield stress while enforcing the configured increase cap.
///
/// Both multiplier arguments are dimensionless and
/// `maximum_increase` is percent; the result is dimensionless.
#[must_use]
pub fn total_harvest_index_multiplier(
    pre_yield_multiplier: f64,
    post_yield_multiplier: f64,
    maximum_increase: i8,
) -> f64 {
    (pre_yield_multiplier * post_yield_multiplier).min(1.0 + f64::from(maximum_increase) / 100.0)
}

/// Calculates dry yield from biomass and the attainable daily harvest index.
///
/// `biomass` and the result are tonnes per hectare, both harvest-index
/// arguments are percentages, and `harvest_index_multiplier` is dimensionless.
#[must_use]
pub fn dry_yield(
    biomass: f64,
    reference_harvest_index: f64,
    attainable_harvest_index: f64,
    harvest_index_multiplier: f64,
) -> f64 {
    biomass * harvest_index_multiplier * reference_harvest_index.min(attainable_harvest_index)
        / 100.0
}

fn harvest_index_growth_coefficient(maximum: f64, rate: f64) -> Option<f64> {
    if maximum <= f64::from(INITIAL_HARVEST_INDEX) {
        return None;
    }
    let time = maximum / rate;
    let mut growth = 0.002;
    let mut index = (f64::from(INITIAL_HARVEST_INDEX) * maximum)
        / (f64::from(INITIAL_HARVEST_INDEX)
            + (maximum - f64::from(INITIAL_HARVEST_INDEX)) * (-growth * time).exp());
    while index <= 0.98 * maximum {
        growth += 0.001;
        index = (f64::from(INITIAL_HARVEST_INDEX) * maximum)
            / (f64::from(INITIAL_HARVEST_INDEX)
                + (maximum - f64::from(INITIAL_HARVEST_INDEX)) * (-growth * time).exp());
    }
    if index >= maximum {
        growth -= 0.001;
    }
    Some(growth)
}

fn day_switch_to_linear(maximum: i32, rate: f64, growth: f64) -> (i32, f64) {
    let maximum_time = roundc_i32(f64::from(maximum) / rate);
    let mut day = 0;
    let mut previous = f64::from(INITIAL_HARVEST_INDEX);
    if maximum_time > 0 {
        loop {
            day += 1;
            let index = logistic_harvest_index(maximum, growth, day);
            let final_index = index + f64::from(maximum_time - day) * (index - previous);
            previous = index;
            if final_index > f64::from(maximum) || day >= maximum_time {
                break;
            }
        }
    }
    let switch_day = day - 1;
    let switch_index = if switch_day > 0 {
        logistic_harvest_index(maximum, growth, switch_day)
    } else {
        0.0
    };
    let linear_growth = (f64::from(maximum) - switch_index) / f64::from(maximum_time - switch_day);
    (switch_day, linear_growth)
}

fn logistic_harvest_index(maximum: i32, growth: f64, time: i32) -> f64 {
    f64::from(INITIAL_HARVEST_INDEX * maximum)
        / (f64::from(INITIAL_HARVEST_INDEX)
            + f64::from(maximum - INITIAL_HARVEST_INDEX) * (-growth * f64::from(time)).exp())
}

fn cumulative_flowering_fraction(day: i32, flowering_length_days: i32) -> f64 {
    if day <= 0 {
        0.0
    } else {
        let time = 100.0 * (f64::from(day) / f64::from(flowering_length_days));
        if time > 100.0 {
            1.0
        } else {
            (0.005_58 * (0.63 * time.ln()).exp() - 0.000_969 * time - 0.003_83).max(0.0)
        }
    }
}

#[cfg(test)]
mod tests {
    use assertables::assert_eq_f64;

    use super::{
        PostYieldStressInputs, PostYieldStressState, advance_post_yield_stress,
        calibration_yield_formation_ramp, flowering_fraction, post_yield_harvest_index_multiplier,
        pre_yield_biomass_range, pre_yield_harvest_index_multiplier,
        total_harvest_index_multiplier, yield_formation_ramp,
    };

    #[test]
    fn pre_yield_response_reaches_the_configured_maximum_at_asymmetric_peak() {
        let range = pre_yield_biomass_range(20);
        let start = 1.0 - range;
        let peak = start + (2.0 / 3.0) * range;

        assert!((pre_yield_harvest_index_multiplier(peak, range, 20) - 1.2).abs() < 1.0e-12);
    }

    #[test]
    fn pre_yield_response_is_neutral_outside_the_biomass_range() {
        let range = pre_yield_biomass_range(20);

        assert_eq_f64!(pre_yield_harvest_index_multiplier(1.01, range, 20), 1.0_f64);
    }

    #[test]
    fn one_day_flowering_assigns_the_complete_distribution_to_that_day() {
        assert_eq_f64!(flowering_fraction(1, 1), 1.0_f64);
    }

    #[test]
    fn neutral_post_yield_stress_keeps_the_multiplier_at_one() {
        let mut state = PostYieldStressState::new();
        advance_post_yield_stress(
            &mut state,
            &PostYieldStressInputs {
                day_in_yield_formation: 1,
                leaf_response: 10,
                stomatal_response: 20,
                leaf_stress_coefficient: 1.0,
                stomatal_stress_coefficient: 1.0,
                leaf_impact_coefficient: Some(3.0),
                stomatal_impact_coefficient: Some(3.0),
            },
        );

        assert!(
            (post_yield_harvest_index_multiplier(&state, 10, 20, true, true) - 1.0).abs() < 1.0e-12
        );
    }

    #[test]
    fn total_multiplier_respects_the_configured_increase_cap() {
        assert!((total_harvest_index_multiplier(1.2, 1.1, 15) - 1.15).abs() < 1.0e-12);
    }

    #[test]
    fn yield_formation_water_productivity_reaches_full_strength_after_first_third() {
        assert_eq_f64!(yield_formation_ramp(10, 30), 1.0_f64);
    }

    #[test]
    fn fertility_calibration_caps_yield_formation_progress_at_one() {
        assert_eq_f64!(calibration_yield_formation_ramp(11, 30, 10), 1.0_f64);
    }
}
