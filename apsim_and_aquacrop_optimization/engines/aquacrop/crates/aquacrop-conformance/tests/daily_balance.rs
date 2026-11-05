//! End-to-end daily water and salt balance closure.

use aquacrop_conformance::{OfficialTestcase, TestResult};
use aquacrop_core::balance::{DailyBalanceFluxes, begin_daily_balance, end_daily_balance};
use aquacrop_core::evaporation::{SoilEvaporationState, evaporate_soil};
use aquacrop_core::salinity::{
    SaltTransportParameters, initialize_salt_from_extract_conductivity, transport_salts,
};
use aquacrop_core::water_balance::{
    InfiltrationParameters, InfiltrationState, RainfallAggregation, infiltrate,
};
use assertables::{assert_eq_f64_as_result, assert_in_delta};

#[test]
fn infiltration_drainage_evaporation_day_closes_water_and_salt() -> TestResult {
    let profile = OfficialTestcase::new().prepared_soil_profile()?;
    let mut compartments = profile.compartments_at_field_capacity();
    for compartment in &mut compartments {
        let layer = &profile.layers[compartment.layer_index];
        initialize_salt_from_extract_conductivity(compartment, layer, 2.0, 100.0);
        compartment.drainage = 12.0;
    }
    let beginning = begin_daily_balance(&profile, &mut compartments, 0.0)?;
    let drainage_was_reset = compartments
        .iter()
        .all(|compartment| assert_eq_f64_as_result!(compartment.drainage, 0.0_f64).is_ok());

    let mut infiltration = InfiltrationState {
        infiltrated_rain: 0.0,
        infiltrated_irrigation: 400.0,
        infiltrated_storage: 0.0,
        subsoil_drainage: 0.0,
        runoff: 0.0,
        surface_storage: 0.0,
        deep_drainage: 0.0,
    };
    infiltrate(
        &profile,
        &mut compartments,
        &InfiltrationParameters {
            rainfall_aggregation: RainfallAggregation::Daily,
            rooting_depth: 0.30,
            evaporation_depth: 0.30,
            bund_height: 0.0,
        },
        &mut infiltration,
    )?;
    let salt = transport_salts(
        &profile,
        &mut compartments,
        &infiltration,
        &SaltTransportParameters {
            irrigation_conductivity: 3.0,
            surface_storage_conductivity: 0.0,
            rooting_depth: 0.30,
            evaporation_depth: 0.30,
            solubility: 100.0,
        },
    )?;
    let mut evaporation_state = SoilEvaporationState::without_previous_day();
    let evaporation = evaporate_soil(
        &profile,
        &mut compartments,
        &mut evaporation_state,
        4.0,
        0.30,
        4.0,
    )?;
    let balance = end_daily_balance(
        &profile,
        &compartments,
        &beginning,
        &DailyBalanceFluxes {
            rain: 0.0,
            irrigation: 400.0,
            drainage: infiltration.deep_drainage,
            runoff: infiltration.runoff,
            evaporation: evaporation.actual,
            transpiration: 0.0,
            capillary_rise: 0.0,
            horizontal_water: 0.0,
            ending_surface_water: infiltration.surface_storage,
            infiltrated_salt: salt.infiltrated_salt,
            drained_salt: salt.drained_salt,
            capillary_salt: 0.0,
            horizontal_salt: 0.0,
        },
    )?;

    assert!(drainage_was_reset);
    assert_in_delta!(balance.water_error, 0.0, 1.0e-9);
    assert_in_delta!(balance.salt_error, 0.0, 1.0e-8);

    Ok(())
}
