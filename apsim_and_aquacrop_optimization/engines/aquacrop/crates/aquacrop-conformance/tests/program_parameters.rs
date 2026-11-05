//! Program-parameter checks for the official project configuration.

use aquacrop_conformance::OfficialTestcase;
use aquacrop_core::climate::GrowingDegreeDayMethod;
use aquacrop_core::io::read_program_parameters;
use aquacrop_core::parameters::{EffectiveRainfallMethod, SimulationParameters};
use assertables::assert_eq_f64;

#[test]
fn official_program_parameters_preserve_daily_process_controls() {
    let path = OfficialTestcase::new()
        .source_root()
        .join("PARAM/Ottawa.PPn");
    let parameters =
        read_program_parameters(path).expect("the official program parameters should parse");

    assert_eq!(parameters.evaporation_decline_factor, 4);
    assert_eq!(parameters.initial_root_depth, 70);
    assert_eq_f64!(parameters.maximum_root_expansion, 5.0_f64);
    assert_eq!(parameters.root_stress_shape, -6);
    assert_eq!(parameters.upper_soil_depth, 10);
    assert_eq!(parameters.maximum_evaporation_depth, 30);
    assert!(parameters.antecedent_moisture_correction);
    assert_eq!(
        parameters.gdd_method,
        GrowingDegreeDayMethod::ClampUpperTemperature
    );
    assert_eq!(
        parameters.effective_rainfall_method,
        EffectiveRainfallMethod::UsdaSoilConservationService
    );
    assert_eq!(parameters.showers_per_decade, 2);
}

#[test]
fn official_program_parameters_match_computational_default() {
    let path = OfficialTestcase::new()
        .source_root()
        .join("PARAM/Ottawa.PPn");
    let expected = SimulationParameters::default();
    let parameters =
        read_program_parameters(path).expect("the official program parameters should parse");

    assert_eq!(
        parameters.evaporation_decline_factor,
        expected.evaporation_decline_factor
    );
    assert_eq_f64!(
        parameters.wet_bare_soil_coefficient,
        expected.wet_bare_soil_coefficient
    );
    assert_eq!(
        parameters.minimum_canopy_for_harvest_index,
        expected.minimum_canopy_for_harvest_index
    );
    assert_eq!(parameters.initial_root_depth, expected.initial_root_depth);
    assert_eq_f64!(
        parameters.maximum_root_expansion,
        expected.maximum_root_expansion
    );
    assert_eq!(parameters.root_stress_shape, expected.root_stress_shape);
    assert_eq!(parameters.germination_water, expected.germination_water);
    assert_eq_f64!(
        parameters.depletion_et_adjustment_factor,
        expected.depletion_et_adjustment_factor
    );
    assert_eq!(
        parameters.deficient_aeration_delay,
        expected.deficient_aeration_delay
    );
    assert_eq_f64!(parameters.senescence_exponent, expected.senescence_exponent);
    assert_eq!(
        parameters.early_senescence_threshold_reduction,
        expected.early_senescence_threshold_reduction
    );
    assert_eq!(parameters.upper_soil_depth, expected.upper_soil_depth);
    assert_eq!(
        parameters.maximum_evaporation_depth,
        expected.maximum_evaporation_depth
    );
    assert_eq_f64!(parameters.runoff_depth, expected.runoff_depth);
    assert_eq!(
        parameters.antecedent_moisture_correction,
        expected.antecedent_moisture_correction
    );
    assert_eq!(parameters.salt_diffusion, expected.salt_diffusion);
    assert_eq!(parameters.salt_solubility, expected.salt_solubility);
    assert_eq!(
        parameters.capillary_rise_shape,
        expected.capillary_rise_shape
    );
    assert_eq_f64!(
        parameters.default_minimum_temperature,
        expected.default_minimum_temperature
    );
    assert_eq_f64!(
        parameters.default_maximum_temperature,
        expected.default_maximum_temperature
    );
    assert_eq!(
        parameters.gdd_method,
        GrowingDegreeDayMethod::ClampUpperTemperature
    );
    assert_eq!(
        parameters.effective_rainfall_method,
        EffectiveRainfallMethod::UsdaSoilConservationService
    );
    assert_eq!(parameters.effective_rainfall, expected.effective_rainfall);
    assert_eq!(parameters.showers_per_decade, expected.showers_per_decade);
    assert_eq!(
        parameters.aggregated_rainfall_evaporation_reduction,
        expected.aggregated_rainfall_evaporation_reduction
    );
}
