//! Program-level numerical parameters shared by one simulation run.

use crate::climate::GrowingDegreeDayMethod;

/// Method used to estimate effective rainfall from aggregated records.
#[derive(Clone, Debug, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "snake_case"))]
pub enum EffectiveRainfallMethod {
    /// Treat the complete rainfall amount as effective.
    Full,
    /// Apply the USDA Soil Conservation Service procedure.
    UsdaSoilConservationService,
    /// Apply a fixed effective-rainfall percentage.
    Percentage,
}

/// AquaCrop program-level numerical controls.
///
/// [`Default`] returns the standard parameter set for the supported AquaCrop version without
/// requiring the `file-io` feature.
#[derive(Clone, Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct SimulationParameters {
    /// Stage-II evaporation decline factor.
    pub evaporation_decline_factor: i8,
    /// Wet bare-soil evaporation coefficient.
    pub wet_bare_soil_coefficient: f64,
    /// Canopy threshold below which harvest index no longer increases, in percent.
    pub minimum_canopy_for_harvest_index: i8,
    /// Starting depth of the root expansion curve, in percent of minimum depth.
    pub initial_root_depth: i32,
    /// Maximum root-zone expansion, in centimetres per day.
    pub maximum_root_expansion: f64,
    /// Shape factor for water stress on root expansion.
    pub root_stress_shape: i32,
    /// Topsoil water required for germination, in percent of total available water.
    pub germination_water: i8,
    /// Factor applied by the FAO reference-ET adjustment of depletion thresholds.
    pub depletion_et_adjustment_factor: f64,
    /// Days until deficient aeration has its full effect.
    pub deficient_aeration_delay: i32,
    /// Senescence exponent for declining photosynthetic activity.
    pub senescence_exponent: f64,
    /// Reduction of the senescence threshold after early senescence begins, in percent.
    pub early_senescence_threshold_reduction: i8,
    /// Upper soil depth considered for water stress, in centimetres.
    pub upper_soil_depth: i8,
    /// Maximum soil-evaporation extraction depth, in centimetres.
    pub maximum_evaporation_depth: i8,
    /// Profile depth used for antecedent-moisture runoff adjustment, in metres.
    pub runoff_depth: f64,
    /// Whether curve number is adjusted for antecedent moisture.
    pub antecedent_moisture_correction: bool,
    /// Salt diffusion factor, in percent.
    pub salt_diffusion: i8,
    /// Salt solubility, in grams per litre.
    pub salt_solubility: i8,
    /// Shape factor for capillary-rise reduction.
    pub capillary_rise_shape: i8,
    /// Default minimum temperature, in degrees Celsius.
    pub default_minimum_temperature: f64,
    /// Default maximum temperature, in degrees Celsius.
    pub default_maximum_temperature: f64,
    /// Fallback GDD method used for calendar-driven crops.
    pub gdd_method: GrowingDegreeDayMethod,
    /// Method used to estimate effective rainfall.
    pub effective_rainfall_method: EffectiveRainfallMethod,
    /// Effective rainfall retained by the percentage method, in percent.
    pub effective_rainfall: i8,
    /// Representative showers per ten-day period.
    pub showers_per_decade: i8,
    /// Reduction parameter for evaporation under aggregated rainfall.
    pub aggregated_rainfall_evaporation_reduction: i8,
}

impl Default for SimulationParameters {
    fn default() -> Self {
        Self {
            evaporation_decline_factor: 4,
            wet_bare_soil_coefficient: 1.10,
            minimum_canopy_for_harvest_index: 5,
            initial_root_depth: 70,
            maximum_root_expansion: 5.00,
            root_stress_shape: -6,
            germination_water: 20,
            depletion_et_adjustment_factor: 1.0,
            deficient_aeration_delay: 3,
            senescence_exponent: 1.00,
            early_senescence_threshold_reduction: 12,
            upper_soil_depth: 10,
            maximum_evaporation_depth: 30,
            runoff_depth: 0.30,
            antecedent_moisture_correction: true,
            salt_diffusion: 20,
            salt_solubility: 100,
            capillary_rise_shape: 16,
            default_minimum_temperature: 12.0,
            default_maximum_temperature: 28.0,
            gdd_method: GrowingDegreeDayMethod::ClampUpperTemperature,
            effective_rainfall_method: EffectiveRainfallMethod::UsdaSoilConservationService,
            effective_rainfall: 70,
            showers_per_decade: 2,
            aggregated_rainfall_evaporation_reduction: 5,
        }
    }
}

#[cfg(test)]
mod tests {
    use assertables::assert_eq_f64_as_result;

    use super::*;

    #[test]
    fn default_uses_aquacrop_program_parameters() {
        let parameters = SimulationParameters::default();

        assert!(
            parameters.evaporation_decline_factor == 4
                && assert_eq_f64_as_result!(parameters.wet_bare_soil_coefficient, 1.10_f64).is_ok()
                && parameters.minimum_canopy_for_harvest_index == 5
                && parameters.initial_root_depth == 70
                && assert_eq_f64_as_result!(parameters.maximum_root_expansion, 5.00_f64).is_ok()
                && parameters.root_stress_shape == -6
                && parameters.germination_water == 20
                && assert_eq_f64_as_result!(parameters.depletion_et_adjustment_factor, 1.0_f64)
                    .is_ok()
                && parameters.deficient_aeration_delay == 3
                && assert_eq_f64_as_result!(parameters.senescence_exponent, 1.00_f64).is_ok()
                && parameters.early_senescence_threshold_reduction == 12
                && parameters.upper_soil_depth == 10
                && parameters.maximum_evaporation_depth == 30
                && assert_eq_f64_as_result!(parameters.runoff_depth, 0.30_f64).is_ok()
                && parameters.antecedent_moisture_correction
                && parameters.salt_diffusion == 20
                && parameters.salt_solubility == 100
                && parameters.capillary_rise_shape == 16
                && assert_eq_f64_as_result!(parameters.default_minimum_temperature, 12.0_f64)
                    .is_ok()
                && assert_eq_f64_as_result!(parameters.default_maximum_temperature, 28.0_f64)
                    .is_ok()
                && parameters.gdd_method == GrowingDegreeDayMethod::ClampUpperTemperature
                && parameters.effective_rainfall_method
                    == EffectiveRainfallMethod::UsdaSoilConservationService
                && parameters.effective_rainfall == 70
                && parameters.showers_per_decade == 2
                && parameters.aggregated_rainfall_evaporation_reduction == 5
        );
    }
}
