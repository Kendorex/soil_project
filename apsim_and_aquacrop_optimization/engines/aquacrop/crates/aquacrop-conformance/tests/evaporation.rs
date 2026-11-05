//! Stateful stage-I and stage-II soil-evaporation checks.

use aquacrop_conformance::{OfficialTestcase, TestResult, profile_salt, profile_water};
use aquacrop_core::evaporation::{SoilEvaporationState, evaporate_soil};
use aquacrop_core::salinity::initialize_salt_from_extract_conductivity;
use assertables::assert_in_delta;

#[test]
fn stage_one_evaporation_conserves_profile_water() -> TestResult {
    let profile = OfficialTestcase::new().prepared_soil_profile()?;
    let mut compartments = profile.compartments_at_field_capacity();
    let initial_water = profile_water(&profile, &compartments);
    let mut state = SoilEvaporationState::without_previous_day();
    state.prepare_stage_one(
        f64::from(profile.readily_evaporable_water),
        5.0,
        0.0,
        0.0,
        0.0,
    );
    let evaporation = evaporate_soil(&profile, &mut compartments, &mut state, 4.0, 0.30, 4.0)?;
    let final_water = profile_water(&profile, &compartments);

    assert_in_delta!(evaporation.actual, 4.0, 1.0e-12);
    assert_in_delta!(state.surface_water, 1.0, 1.0e-12);
    assert_in_delta!(initial_water, final_water + evaporation.actual, 1.0e-9);

    Ok(())
}

#[test]
fn stage_two_substeps_conserve_water_and_respect_depth_limit() -> TestResult {
    let profile = OfficialTestcase::new().prepared_soil_profile()?;
    let mut compartments = profile.compartments_at_field_capacity();
    for (index, compartment) in compartments.iter_mut().enumerate() {
        let layer = &profile.layers[compartment.layer_index];
        if index == 0 {
            compartment.theta = layer.wilting_point / 200.0 + 0.04;
        }
        initialize_salt_from_extract_conductivity(
            compartment,
            layer,
            1.0 + f64::from(u32::try_from(index)?),
            100.0,
        );
    }
    let initial_water = profile_water(&profile, &compartments);
    let initial_salt = profile_salt(&profile, &compartments);
    let mut state = SoilEvaporationState::without_previous_day();
    let evaporation = evaporate_soil(&profile, &mut compartments, &mut state, 8.0, 0.30, 4.0)?;
    let final_water = profile_water(&profile, &compartments);
    let final_salt = profile_salt(&profile, &compartments);

    assert!(evaporation.actual > 0.0);
    assert!(evaporation.actual <= 8.0);
    assert!(state.stage_two_start.is_some());
    assert!((0.15..=0.30).contains(&state.extraction_depth));
    assert!(evaporation.upward_salt >= 0.0);
    assert_in_delta!(initial_water, final_water + evaporation.actual, 1.0e-9);
    assert_in_delta!(initial_salt, final_salt, 1.0e-9);

    Ok(())
}
