//! Conservation and source-formula checks for the soil-water balance.

use aquacrop_conformance::{OfficialTestcase, TestResult, profile_water};
use aquacrop_core::water_balance::{
    InfiltrationParameters, InfiltrationState, RainfallAggregation,
    adjust_field_capacity_for_groundwater, antecedent_curve_numbers, apply_capillary_rise,
    curve_number_for_wetness, drain_profile, infiltrate, relative_wetness_topsoil, runoff,
};
use assertables::{assert_eq_f64, assert_in_delta};

#[test]
fn drainage_conserves_profile_water() -> TestResult {
    let profile = OfficialTestcase::new().prepared_soil_profile()?;
    let mut compartments = profile.compartments_at_field_capacity();
    for compartment in &mut compartments {
        compartment.theta = 0.40;
    }
    let initial_storage: f64 = compartments
        .iter()
        .map(|compartment| {
            profile.layers[compartment.layer_index]
                .water_storage(compartment.theta, compartment.thickness)
        })
        .sum();
    let bottom_drainage = drain_profile(&profile, &mut compartments)?;
    let final_storage: f64 = compartments
        .iter()
        .map(|compartment| {
            profile.layers[compartment.layer_index]
                .water_storage(compartment.theta, compartment.thickness)
        })
        .sum();

    assert_in_delta!(initial_storage, final_storage + bottom_drainage, 1.0e-9);

    Ok(())
}

#[test]
fn official_curve_number_uses_source_antecedent_polynomials() {
    assert_eq!(antecedent_curve_numbers(46), (24, 70));
}

#[test]
fn field_capacity_profile_has_fully_wet_antecedent_class() -> TestResult {
    let profile = OfficialTestcase::new().prepared_soil_profile()?;
    let mut compartments = profile.compartments_at_field_capacity();
    let wetness = relative_wetness_topsoil(&profile, &mut compartments, 0.30)?;

    assert_in_delta!(wetness, 1.0, 1.0e-12);
    assert_eq!(curve_number_for_wetness(46, wetness), 70);

    Ok(())
}

#[test]
fn daily_runoff_matches_curve_number_equation() {
    let rainfall = 30.0;
    let curve_number = 46;
    let storage = 254.0 * (100.0 / f64::from(curve_number) - 1.0);
    let term = rainfall - 0.05 * storage;
    let expected = term.powi(2) / (rainfall + 0.95 * storage);
    let actual = runoff(rainfall, curve_number, 5, &RainfallAggregation::Daily)
        .expect("daily runoff inputs should be valid");

    assert_in_delta!(actual, expected, f64::EPSILON);
}

#[test]
fn aggregated_runoff_applies_reference_10_14_factor() {
    let rainfall = 60.0;
    let showers = 2;
    let curve_number = 46;
    let shower = rainfall * 10.0 / f64::from(showers);
    let storage = 254.0 * (100.0 / f64::from(curve_number) - 1.0);
    let term = shower - 0.05 * storage;
    let event_runoff = term.powi(2) / (shower + 0.95 * storage);
    let expected = (event_runoff * (f64::from(showers) / 10.14)).min(rainfall);
    let actual = runoff(
        rainfall,
        curve_number,
        5,
        &RainfallAggregation::Aggregated {
            showers_per_decade: showers,
        },
    )
    .expect("aggregated runoff inputs should be valid");

    assert_in_delta!(actual, expected, 1.0e-12);
}

#[test]
fn shallow_groundwater_raises_deep_compartment_field_capacity() -> TestResult {
    let profile = OfficialTestcase::new().prepared_soil_profile()?;
    let mut compartments = profile.compartments_at_field_capacity();
    adjust_field_capacity_for_groundwater(&profile, &mut compartments, 0.80)?;

    let value = compartments
        .last()
        .expect("the official soil profile should contain a bottom compartment")
        .adjusted_field_capacity;

    assert_eq_f64!(value, 46.0_f64);

    Ok(())
}

#[test]
fn capillary_rise_conserves_added_water_and_salt() -> TestResult {
    let profile = OfficialTestcase::new().prepared_soil_profile()?;
    let mut compartments = profile.compartments_at_field_capacity();
    for compartment in &mut compartments {
        compartment.theta = 0.20;
    }
    adjust_field_capacity_for_groundwater(&profile, &mut compartments, 0.80)?;
    let initial_water: f64 = compartments
        .iter()
        .map(|compartment| {
            profile.layers[compartment.layer_index]
                .water_storage(compartment.theta, compartment.thickness)
        })
        .sum();
    let rise = apply_capillary_rise(&profile, &mut compartments, 0.80, 1.5, 16)?;
    let final_water: f64 = compartments
        .iter()
        .map(|compartment| {
            profile.layers[compartment.layer_index]
                .water_storage(compartment.theta, compartment.thickness)
        })
        .sum();
    let stored_salt: f64 = compartments
        .iter()
        .flat_map(|compartment| compartment.dissolved_salt)
        .sum();

    assert!(rise.water > 0.0);
    assert_in_delta!(final_water - initial_water, rise.water, 1.0e-9);
    assert_in_delta!(stored_salt, rise.salt, 1.0e-9);

    Ok(())
}

#[test]
fn active_drainage_blocks_capillary_rise() -> TestResult {
    let profile = OfficialTestcase::new().prepared_soil_profile()?;
    let mut compartments = profile.compartments_at_field_capacity();
    if let Some(bottom) = compartments.last_mut() {
        bottom.theta = 0.20;
        bottom.drainage = 0.001;
    }
    adjust_field_capacity_for_groundwater(&profile, &mut compartments, 0.80)?;
    let rise = apply_capillary_rise(&profile, &mut compartments, 0.80, 1.5, 16)?;

    assert_eq_f64!(rise.water, 0.0_f64);
    assert_eq_f64!(rise.salt, 0.0_f64);

    Ok(())
}

#[test]
fn daily_infiltration_conserves_profile_and_surface_water() -> TestResult {
    let profile = OfficialTestcase::new().prepared_soil_profile()?;
    let mut compartments = profile.compartments_at_field_capacity();
    let initial_water = profile_water(&profile, &compartments);
    let parameters = InfiltrationParameters {
        rainfall_aggregation: RainfallAggregation::Daily,
        rooting_depth: 0.30,
        evaporation_depth: 0.30,
        bund_height: 0.0,
    };
    let mut state = InfiltrationState {
        infiltrated_rain: 2_000.0,
        infiltrated_irrigation: 0.0,
        infiltrated_storage: 0.0,
        subsoil_drainage: 0.0,
        runoff: 0.0,
        surface_storage: 0.0,
        deep_drainage: 0.0,
    };
    infiltrate(&profile, &mut compartments, &parameters, &mut state)?;
    let final_water = profile_water(&profile, &compartments);

    assert!(state.runoff > 0.0);
    assert!(state.deep_drainage > 0.0);
    assert_in_delta!(
        initial_water + 2_000.0,
        final_water + state.deep_drainage + state.runoff,
        1.0e-9
    );

    Ok(())
}

#[test]
fn bunds_retain_infiltration_excess_up_to_their_capacity() -> TestResult {
    let profile = OfficialTestcase::new().prepared_soil_profile()?;
    let mut compartments = profile.compartments_at_field_capacity();
    let parameters = InfiltrationParameters {
        rainfall_aggregation: RainfallAggregation::Daily,
        rooting_depth: 0.30,
        evaporation_depth: 0.30,
        bund_height: 0.10,
    };
    let mut state = InfiltrationState {
        infiltrated_rain: 1_300.0,
        infiltrated_irrigation: 0.0,
        infiltrated_storage: 0.0,
        subsoil_drainage: 0.0,
        runoff: 0.0,
        surface_storage: 0.0,
        deep_drainage: 0.0,
    };
    infiltrate(&profile, &mut compartments, &parameters, &mut state)?;

    assert!(state.surface_storage > 0.0);
    assert!(state.surface_storage <= 100.0);

    Ok(())
}

#[test]
fn aggregated_infiltration_conserves_root_and_subsoil_storage() -> TestResult {
    let profile = OfficialTestcase::new().prepared_soil_profile()?;
    let mut compartments = profile.compartments_at_field_capacity();
    let initial_water = profile_water(&profile, &compartments);
    let parameters = InfiltrationParameters {
        rainfall_aggregation: RainfallAggregation::Aggregated {
            showers_per_decade: 3,
        },
        rooting_depth: 0.30,
        evaporation_depth: 0.30,
        bund_height: 0.0,
    };
    let mut state = InfiltrationState {
        infiltrated_rain: 500.0,
        infiltrated_irrigation: 20.0,
        infiltrated_storage: 10.0,
        subsoil_drainage: 200.0,
        runoff: 0.0,
        surface_storage: 0.0,
        deep_drainage: 0.0,
    };
    infiltrate(&profile, &mut compartments, &parameters, &mut state)?;
    let final_water = profile_water(&profile, &compartments);

    assert_in_delta!(
        initial_water + 530.0,
        final_water + state.deep_drainage + state.runoff + state.surface_storage,
        1.0e-9
    );

    Ok(())
}
