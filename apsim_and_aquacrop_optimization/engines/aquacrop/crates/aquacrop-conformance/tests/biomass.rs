//! Biomass accumulation checks using the official crop parameters.

use aquacrop_conformance::OfficialTestcase;
use aquacrop_core::biomass::{BiomassInputs, BiomassState, REFERENCE_CO2, advance_biomass};
use assertables::assert_in_delta;

#[test]
fn official_crop_accumulates_crop_total_and_potential_biomass_separately() {
    let crop = OfficialTestcase::new()
        .prepared_gdd_crop()
        .expect("official crop file should parse");
    let mut state = BiomassState::new();
    let productivity = advance_biomass(
        &mut state,
        &crop.parameters.productivity,
        &BiomassInputs {
            reference_et: 5.0,
            actual_transpiration: 5.0,
            crop_transpiration: 3.0,
            unrestricted_transpiration: 5.0,
            co2: REFERENCE_CO2,
            actual_yield_formation_switch: 0.0,
            unrestricted_yield_formation_switch: 0.0,
            fertility_wp_reduction: 0.0,
            normalized_fertility_potential_transpiration: 1.0,
            fertility_biomass_fraction: 0.8,
        },
    );

    assert_in_delta!(productivity.actual, 0.15, f64::EPSILON);
    assert_in_delta!(state.actual, 0.09, f64::EPSILON);
    assert_in_delta!(state.total, 0.15, f64::EPSILON);
    assert_in_delta!(state.unrestricted, 0.15, f64::EPSILON);
    assert_in_delta!(state.potential, 0.12, f64::EPSILON);
}
