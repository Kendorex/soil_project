//! Potential transpiration and soil-evaporation demand.

use crate::compat::{REFERENCE_CO2, ZERO_THRESHOLD, roundc_i32};

/// Inputs to the reference potential ET calculation.
#[derive(Debug)]
pub struct PotentialEtParameters {
    /// Days after planting on the active crop time scale.
    pub days_after_planting: i32,
    /// Accumulated delayed crop days.
    pub delayed_days: i32,
    /// Days from planting to germination.
    pub days_to_germination: i32,
    /// Days from planting to full canopy.
    pub days_to_full_canopy: i32,
    /// Days from planting to senescence.
    pub days_to_senescence: i32,
    /// Days from planting to harvest.
    pub days_to_harvest: i32,
    /// Crop day of the last cutting event.
    pub day_last_cut: i32,
    /// Current green canopy cover as a fraction.
    pub canopy_cover: f64,
    /// Reference evapotranspiration, in mm/day.
    pub reference_et: f64,
    /// Maximum crop transpiration coefficient.
    pub crop_coefficient: f64,
    /// Cumulative coefficient decline at maturity, in percent.
    pub coefficient_decline: f64,
    /// Adjusted maximum canopy cover as a fraction.
    pub maximum_canopy_cover: f64,
    /// Maximum withered canopy cover as a fraction.
    pub withered_canopy_cover: f64,
    /// Withered-canopy reduction of soil evaporation, in percent.
    pub late_evaporation_effect: f64,
    /// Atmospheric carbon-dioxide concentration, in ppm.
    pub carbon_dioxide: f64,
    /// Growing degree days for the current day.
    pub growing_degree_days: f64,
    /// GDD threshold for full transpiration response, when cold stress is enabled.
    pub cold_stress_threshold_gdd: Option<f64>,
    /// Wet bare-soil evaporation coefficient.
    pub wet_bare_soil_coefficient: f64,
    /// Senescent-canopy transpiration exponent.
    pub senescence_exponent: f64,
    /// Whether early senescence currently limits soil evaporation.
    pub evaporation_limit_enabled: bool,
}

/// Potential transpiration and soil evaporation, in millimetres per day.
#[derive(Debug)]
pub struct PotentialEt {
    /// Potential crop transpiration.
    pub transpiration: f64,
    /// Potential soil evaporation.
    pub soil_evaporation: f64,
}

/// Calculates AquaCrop potential transpiration and soil evaporation.
///
/// Parameter fields retain their field-documented units; both returned water
/// fluxes are in millimetres per day.
#[must_use]
pub fn potential_et(parameters: &PotentialEtParameters) -> PotentialEt {
    let virtual_day = parameters.days_after_planting - parameters.delayed_days;
    if (virtual_day < parameters.days_to_germination
        && roundc_i32(100.0 * parameters.canopy_cover) == 0)
        || virtual_day > parameters.days_to_harvest
    {
        return PotentialEt {
            transpiration: 0.0,
            soil_evaporation: parameters.wet_bare_soil_coefficient * parameters.reference_et,
        };
    }

    let adjusted_canopy = micro_advection_canopy(parameters.canopy_cover);
    let mut crop_coefficient = aged_crop_coefficient(parameters, virtual_day);
    if parameters.carbon_dioxide > REFERENCE_CO2 {
        crop_coefficient *=
            1.0 - 0.05 * (parameters.carbon_dioxide - REFERENCE_CO2) / (550.0 - REFERENCE_CO2);
    }
    let cold_stress =
        if adjusted_canopy <= ZERO_THRESHOLD || roundc_i32(parameters.growing_degree_days) < 0 {
            1.0
        } else {
            temperature_stress_coefficient(
                Some(0.0),
                parameters.cold_stress_threshold_gdd,
                parameters.growing_degree_days,
            )
        };
    let mut transpiration =
        adjusted_canopy * cold_stress * crop_coefficient * parameters.reference_et;
    let mut soil_evaporation =
        parameters.wet_bare_soil_coefficient * (1.0 - adjusted_canopy) * parameters.reference_et;
    let maximum_evaporation = parameters.wet_bare_soil_coefficient
        * parameters.reference_et
        * (1.0 - parameters.withered_canopy_cover * parameters.late_evaporation_effect / 100.0);

    if virtual_day > parameters.days_to_senescence && parameters.maximum_canopy_cover > f64::EPSILON
    {
        soil_evaporation =
            late_season_soil_evaporation(parameters, soil_evaporation, maximum_evaporation);
    }
    if parameters.evaporation_limit_enabled && soil_evaporation > maximum_evaporation {
        soil_evaporation = maximum_evaporation;
    }
    if parameters.canopy_cover < parameters.withered_canopy_cover
        && parameters.withered_canopy_cover > 0.01
        && parameters.canopy_cover > 0.001
    {
        transpiration *= (parameters.canopy_cover / parameters.withered_canopy_cover)
            .powf(parameters.senescence_exponent);
    }
    PotentialEt {
        transpiration,
        soil_evaporation,
    }
}

fn aged_crop_coefficient(parameters: &PotentialEtParameters, virtual_day: i32) -> f64 {
    if virtual_day - parameters.day_last_cut > parameters.days_to_full_canopy {
        let relative_time =
            f64::from(virtual_day - parameters.day_last_cut - parameters.days_to_full_canopy)
                / f64::from(parameters.days_to_harvest - parameters.days_to_full_canopy);
        parameters.crop_coefficient
            - (relative_time.exp() - 1.0) / (1.0_f64.exp() - 1.0)
                * (parameters.coefficient_decline / 100.0)
                * parameters.withered_canopy_cover
    } else {
        parameters.crop_coefficient
    }
}

fn late_season_soil_evaporation(
    parameters: &PotentialEtParameters,
    mut evaporation: f64,
    maximum_evaporation: f64,
) -> f64 {
    let multiplier = if parameters.canopy_cover > parameters.maximum_canopy_cover / 2.0 {
        if parameters.canopy_cover > parameters.maximum_canopy_cover {
            0.0
        } else {
            (parameters.maximum_canopy_cover - parameters.canopy_cover)
                / (parameters.maximum_canopy_cover / 2.0)
        }
    } else {
        1.0
    };
    evaporation *= 1.0
        - parameters.maximum_canopy_cover
            * (parameters.late_evaporation_effect / 100.0)
            * multiplier;
    let minimum = (parameters.wet_bare_soil_coefficient
        * (1.0 - 1.72 * parameters.maximum_canopy_cover + parameters.maximum_canopy_cover.powi(2)
            - 0.30 * parameters.maximum_canopy_cover.powi(3))
        * parameters.reference_et)
        .max(0.0);
    evaporation.clamp(minimum, maximum_evaporation)
}

/// Applies the reference micro-advection correction to green canopy cover.
///
/// `canopy_cover` and the result are soil-cover fractions.
#[must_use]
pub fn micro_advection_canopy(canopy_cover: f64) -> f64 {
    let adjusted = 1.72 * canopy_cover - canopy_cover.powi(2) + 0.30 * canopy_cover.powi(3);
    if adjusted < f64::EPSILON {
        0.0
    } else {
        adjusted.min(1.0)
    }
}

/// Temperature stress coefficient used by potential transpiration.
///
/// Defined thresholds and `value` must use the same temperature unit, normally
/// degree Celsius-days for the potential-transpiration calculation. A missing
/// threshold disables temperature stress. The result is dimensionless.
#[must_use]
pub fn temperature_stress_coefficient(lower: Option<f64>, upper: Option<f64>, value: f64) -> f64 {
    let mut coefficient = 1.0;
    if let (Some(lower), Some(upper)) = (lower, upper)
        && (lower - upper).abs() > f64::EPSILON
    {
        let direction = if lower < upper { 1.0 } else { -1.0 };
        if direction * value > direction * lower && direction * value < direction * upper {
            let relative = (value - lower) / (upper - lower);
            let minimum = 0.02_f64;
            let rate = -((minimum - 0.98 * minimum) / (0.98 * (1.0 - minimum))).ln();
            coefficient = minimum / (minimum + (1.0 - minimum) * (-rate * relative).exp())
                - minimum * (1.0 - relative);
            coefficient = coefficient.clamp(0.0, 1.0);
        } else if direction * value <= direction * lower {
            coefficient = 0.0;
        } else if direction * value >= direction * upper {
            coefficient = 1.0;
        }
    }
    coefficient
}

#[cfg(test)]
mod tests {
    use assertables::assert_eq_f64;

    use super::{PotentialEtParameters, micro_advection_canopy, potential_et};
    use crate::compat::REFERENCE_CO2;

    fn parameters() -> PotentialEtParameters {
        PotentialEtParameters {
            days_after_planting: 30,
            delayed_days: 0,
            days_to_germination: 5,
            days_to_full_canopy: 50,
            days_to_senescence: 120,
            days_to_harvest: 180,
            day_last_cut: 0,
            canopy_cover: 0.50,
            reference_et: 5.0,
            crop_coefficient: 1.20,
            coefficient_decline: 11.0,
            maximum_canopy_cover: 0.90,
            withered_canopy_cover: 0.50,
            late_evaporation_effect: 50.0,
            carbon_dioxide: REFERENCE_CO2,
            growing_degree_days: 20.0,
            cold_stress_threshold_gdd: Some(10.0),
            wet_bare_soil_coefficient: 1.10,
            senescence_exponent: 1.0,
            evaporation_limit_enabled: false,
        }
    }

    #[test]
    fn active_canopy_splits_reference_et_into_potential_fluxes() {
        let parameters = parameters();
        let adjusted = micro_advection_canopy(parameters.canopy_cover);
        let result = potential_et(&parameters);

        assert_eq_f64!(result.transpiration, (adjusted * 1.20 * 5.0));
        assert_eq_f64!(result.soil_evaporation, (1.10 * (1.0 - adjusted) * 5.0));
    }

    #[test]
    fn outside_crop_period_uses_wet_bare_soil_evaporation() {
        let mut parameters = parameters();
        parameters.days_after_planting = 181;
        let result = potential_et(&parameters);

        assert_eq_f64!(result.transpiration, 0.0_f64);
        assert_eq_f64!(result.soil_evaporation, 5.5_f64);
    }
}
