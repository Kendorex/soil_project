//! Salt-cell representation and mass-conservation checks.

use aquacrop_conformance::{OfficialTestcase, TestResult, profile_salt};
use aquacrop_core::biomass::salinity_canopy_effects;
use aquacrop_core::climate::{
    CalibrationClimate, DailyWeather, GrowingDegreeDayMethod, reference_temperature_climatology,
};
use aquacrop_core::compat::roundc_i32;
use aquacrop_core::crop::{Crop, CropSeason, SeasonCrop};
use aquacrop_core::engine::{InitialConditions, Simulation, SimulationConfig};
use aquacrop_core::io::{
    read_co2_file, read_crop_file, read_management_file, read_program_parameters,
    read_project_file, read_soil_profile, read_temperature_file,
};
use aquacrop_core::salinity::{
    SalinityResponse, SaltTransportParameters, active_salinity_cell, concentrate_salts,
    determine_root_zone_salinity, horizontal_groundwater_inflow, initialize_groundwater_profile,
    initialize_salt_from_extract_conductivity, reconcile_solution_and_deposit,
    saturated_extract_conductivity, soil_water_conductivity, total_salt,
    transport_salt_upward_after_evaporation, transport_salts,
};
use aquacrop_core::water_balance::{
    InfiltrationParameters, InfiltrationState, RainfallAggregation, infiltrate,
};
use assertables::assert_in_delta;

const REFERENCE_SALT_SOLUBILITY: f64 = 100.0;

#[test]
fn official_crop_salinity_effects_match_the_pinned_source_routine() -> TestResult {
    let crop = OfficialTestcase::new().prepared_gdd_crop()?;
    let effects = salinity_canopy_effects(&crop, 50);

    assert_eq!(effects.growth_reduction, 28);
    assert_eq!(effects.maximum_cover_reduction, 50);
    assert_eq!(effects.stomatal_reduction, 50);
    assert_in_delta!(effects.canopy_decline, 0.035_331_632_653, 5.0e-13);
    Ok(())
}

#[test]
fn saline_root_zone_reduces_transpiration_without_breaking_daily_balances() -> TestResult {
    let mut fresh = salinity_simulation(0.0)?;
    let mut saline = salinity_simulation(6.0)?;
    let mut reduced_transpiration = false;
    loop {
        let fresh_day = fresh.step()?;
        let saline_day = saline.step()?;
        let (Some(fresh_day), Some(saline_day)) = (fresh_day, saline_day) else {
            break;
        };
        assert_in_delta!(fresh_day.water_balance_error, 0.0, 1.0e-8);
        assert_in_delta!(saline_day.water_balance_error, 0.0, 1.0e-8);
        assert_in_delta!(saline_day.salt_balance_error, 0.0, 1.0e-8);
        if saline_day.root_zone_salinity.is_some()
            && saline_day.transpiration + 1.0e-10 < fresh_day.transpiration
        {
            reduced_transpiration = true;
        }
    }
    assert!(reduced_transpiration);
    Ok(())
}

fn salinity_simulation(conductivity: f64) -> Result<Simulation, aquacrop_core::Error> {
    let project = OfficialTestcase::new();
    let root = project.source_root();
    let project = read_project_file(root.join("LIST/Ottawa.PRM"))?;
    let run = project
        .runs
        .first()
        .ok_or(aquacrop_core::Error::InvalidSimulationConfiguration(
            "official project must contain one run",
        ))?;
    let crop = read_crop_file(root.join("DATA/AlfOttawaGDD.CRO"))?;
    let maximum_rooting_depth = crop.parameters.rooting.maximum_depth;
    let soil = read_soil_profile(root.join("DATA/Ottawa.SOL"))?;
    let initial_soil = soil.clone().prepare()?;
    let mut management = read_management_file(root.join("DATA/Ottawa.MAN"))?;
    management.fertility_stress = 0;
    let parameters = read_program_parameters(root.join("PARAM/Ottawa.PPn"))?;
    let temperatures = read_temperature_file(root.join("DATA/Ottawa.Tnx"))?;
    let reference_temperatures = reference_temperature_climatology(
        temperatures.record.first_date.day_number(),
        &temperatures.observations,
    )?;
    let reference_year = roundc_i32(
        f64::from(temperatures.record.first_date.year() + temperatures.record.last_date.year())
            / 2.0,
    );
    let co2 = read_co2_file(root.join("SIMUL/MaunaLoa.CO2"))?;
    let reference_carbon_dioxide = co2.series.concentration_for_year(reference_year).ok_or(
        aquacrop_core::Error::InvalidSimulationConfiguration(
            "reference year must contain CO2 data",
        ),
    )?;
    let first = run.crop_period.first;
    let last = run.crop_period.last;
    let offset =
        usize::try_from(first - temperatures.record.first_date.day_number()).map_err(|_| {
            aquacrop_core::Error::InvalidSimulationConfiguration(
                "temperature series begins after the crop",
            )
        })?;
    let weather = daily_weather(
        &temperatures.observations[offset..],
        reference_carbon_dioxide,
    );
    let crop = season_crop(
        crop,
        parameters.gdd_method,
        &temperatures.observations[offset..],
    )?;
    let config = SimulationConfig {
        season_year: 1,
        crop,
        soil,
        management,
        irrigation: None,
        parameters: parameters.simulation,
        weather,
        external_irrigation: None,
        groundwater: None,
        calibration_climate: Some(CalibrationClimate {
            reference_temperatures,
            reference_carbon_dioxide,
        }),
        simulation_start: first.into(),
        simulation_stop: last.into(),
        crop_start: first.into(),
        crop_stop: last.into(),
    };
    let mut compartments =
        initial_soil.compartments_at_field_capacity_for_depth(maximum_rooting_depth);
    if conductivity > 0.0 {
        for compartment in &mut compartments {
            let layer = &initial_soil.layers[compartment.layer_index];
            initialize_salt_from_extract_conductivity(
                compartment,
                layer,
                conductivity,
                REFERENCE_SALT_SOLUBILITY,
            );
        }
    }
    config.prepare()?.initialize(InitialConditions {
        compartments: Some(compartments.into_iter().map(Into::into).collect()),
        surface_water: 0.0,
        surface_conductivity: 0.0,
        stored_assimilates: 0.0,
    })
}

fn season_crop(
    crop: Crop,
    gdd_method: GrowingDegreeDayMethod,
    temperatures: &[(f64, f64)],
) -> Result<SeasonCrop, aquacrop_core::Error> {
    crop.for_season(CropSeason::new_with_temperatures(gdd_method, temperatures))
}

fn daily_weather(temperatures: &[(f64, f64)], carbon_dioxide: f64) -> Vec<DailyWeather> {
    temperatures
        .iter()
        .map(|temperature| DailyWeather {
            minimum_temperature: temperature.0,
            maximum_temperature: temperature.1,
            reference_et: 5.0,
            rain: 0.0,
            carbon_dioxide,
        })
        .collect()
}

#[test]
fn extract_conductivity_round_trips_through_reference_cell_distribution() -> TestResult {
    let profile = OfficialTestcase::new().prepared_soil_profile()?;
    let actual = {
        let mut compartments = profile.compartments_at_field_capacity();
        let compartment = &mut compartments[0];
        let layer = &profile.layers[compartment.layer_index];
        initialize_salt_from_extract_conductivity(
            compartment,
            layer,
            4.0,
            REFERENCE_SALT_SOLUBILITY,
        );
        saturated_extract_conductivity(compartment, layer, REFERENCE_SALT_SOLUBILITY)
    };

    assert!((actual - 4.0).abs() <= 1.0e-12);

    Ok(())
}

#[test]
fn solution_and_deposit_reconciliation_conserves_mass() {
    let mut dissolved = 15.0;
    let mut deposited = 10.0;
    reconcile_solution_and_deposit(0.20, &mut dissolved, &mut deposited, 100.0);

    assert_in_delta!(dissolved + deposited, 25.0, f64::EPSILON);
    assert_in_delta!(dissolved, 20.0, f64::EPSILON);
    assert_in_delta!(deposited, 5.0, f64::EPSILON);
}

#[test]
fn concentration_moves_dry_cell_salt_without_changing_profile_mass() -> TestResult {
    let profile = OfficialTestcase::new().prepared_soil_profile()?;
    let mut compartments = profile.compartments_at_field_capacity();
    let compartment = &mut compartments[0];
    let layer = &profile.layers[compartment.layer_index];
    let dry_cell = usize::try_from(layer.salinity_cell_count_with_saturation - 1)?;
    compartment.dissolved_salt[dry_cell] = 12.0;
    compartment.deposited_salt[dry_cell] = 3.0;
    compartment.theta = layer.wilting_point / 100.0;
    let initial_salt = total_salt(compartment, layer);
    concentrate_salts(&profile, &mut compartments, REFERENCE_SALT_SOLUBILITY)?;
    let compartment = &compartments[0];

    let final_salt = total_salt(compartment, layer);
    assert_in_delta!(initial_salt, final_salt, f64::EPSILON);

    let active_cell = active_salinity_cell(compartment, layer);
    assert_eq!(active_cell, 0);

    let dry_cell_salt = compartment.dissolved_salt[dry_cell] + compartment.deposited_salt[dry_cell];
    assert_in_delta!(dry_cell_salt, 0.0, f64::EPSILON);

    let conductivity = soil_water_conductivity(compartment, layer, REFERENCE_SALT_SOLUBILITY);
    assert!(conductivity > 0.0);

    Ok(())
}

#[test]
fn infiltration_drainage_and_diffusion_conserve_salt_mass() -> TestResult {
    let profile = OfficialTestcase::new().prepared_soil_profile()?;

    let mut compartments = profile.compartments_at_field_capacity();
    for compartment in &mut compartments {
        let layer = &profile.layers[compartment.layer_index];
        initialize_salt_from_extract_conductivity(
            compartment,
            layer,
            2.0,
            REFERENCE_SALT_SOLUBILITY,
        );
    }
    let initial_salt = profile_salt(&profile, &compartments);
    let infiltration_parameters = InfiltrationParameters {
        rainfall_aggregation: RainfallAggregation::Daily,
        rooting_depth: 0.30,
        evaporation_depth: 0.30,
        bund_height: 0.0,
    };
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
        &infiltration_parameters,
        &mut infiltration,
    )?;
    let transport_parameters = SaltTransportParameters {
        irrigation_conductivity: 3.0,
        surface_storage_conductivity: 0.0,
        rooting_depth: 0.30,
        evaporation_depth: 0.30,
        solubility: REFERENCE_SALT_SOLUBILITY,
    };
    let transport = transport_salts(
        &profile,
        &mut compartments,
        &infiltration,
        &transport_parameters,
    )?;

    let final_salt = profile_salt(&profile, &compartments);

    assert_in_delta!(
        initial_salt + transport.infiltrated_salt,
        final_salt + transport.drained_salt,
        1.0e-8
    );
    assert!(transport.drained_salt > 0.0);
    assert!(transport.drainage_conductivity > 0.0);
    assert!(compartments.iter().all(|compartment| {
        compartment
            .dissolved_salt
            .iter()
            .chain(&compartment.deposited_salt)
            .all(|salt| *salt >= 0.0)
    }));

    Ok(())
}

#[test]
fn aggregated_subsoil_transport_conserves_existing_salt() -> TestResult {
    let profile = OfficialTestcase::new().prepared_soil_profile()?;
    let mut compartments = profile.compartments_at_field_capacity();
    for (index, compartment) in compartments.iter_mut().enumerate() {
        let layer = &profile.layers[compartment.layer_index];
        initialize_salt_from_extract_conductivity(
            compartment,
            layer,
            1.0 + f64::from(u32::try_from(index)?) / 10.0,
            REFERENCE_SALT_SOLUBILITY,
        );
    }
    let initial_salt = profile_salt(&profile, &compartments);
    let infiltration_parameters = InfiltrationParameters {
        rainfall_aggregation: RainfallAggregation::Aggregated {
            showers_per_decade: 3,
        },
        rooting_depth: 0.60,
        evaporation_depth: 0.30,
        bund_height: 0.0,
    };
    let mut infiltration = InfiltrationState {
        infiltrated_rain: 300.0,
        infiltrated_irrigation: 0.0,
        infiltrated_storage: 0.0,
        subsoil_drainage: 120.0,
        runoff: 0.0,
        surface_storage: 0.0,
        deep_drainage: 0.0,
    };
    infiltrate(
        &profile,
        &mut compartments,
        &infiltration_parameters,
        &mut infiltration,
    )?;
    let transport = transport_salts(
        &profile,
        &mut compartments,
        &infiltration,
        &SaltTransportParameters {
            irrigation_conductivity: 0.0,
            surface_storage_conductivity: 0.0,
            rooting_depth: 0.60,
            evaporation_depth: 0.30,
            solubility: REFERENCE_SALT_SOLUBILITY,
        },
    )?;
    let final_salt = profile_salt(&profile, &compartments);

    assert!(infiltration.subsoil_drainage > 0.0);
    assert_in_delta!(initial_salt, final_salt + transport.drained_salt, 1.0e-8);

    Ok(())
}

#[test]
fn evaporation_transport_moves_salt_upward_without_changing_mass() -> TestResult {
    let profile = OfficialTestcase::new().prepared_soil_profile()?;
    let mut compartments = profile.compartments_at_field_capacity();
    for (index, compartment) in compartments.iter_mut().enumerate() {
        let layer = &profile.layers[compartment.layer_index];
        initialize_salt_from_extract_conductivity(
            compartment,
            layer,
            1.0 + f64::from(u32::try_from(index)?),
            REFERENCE_SALT_SOLUBILITY,
        );
    }
    compartments[0].theta = 0.20;
    let initial_theta: Vec<f64> = compartments
        .iter()
        .map(|compartment| compartment.theta)
        .collect();
    compartments[1].theta -= 0.04;
    let initial_salt = profile_salt(&profile, &compartments);
    let moved =
        transport_salt_upward_after_evaporation(&profile, &mut compartments, &initial_theta, 0.30)?;
    let final_salt = profile_salt(&profile, &compartments);

    assert!(moved > 0.0);
    assert_in_delta!(initial_salt, final_salt, 1.0e-9);

    Ok(())
}

#[test]
fn horizontal_groundwater_inflow_restores_water_and_salinity() -> TestResult {
    let profile = OfficialTestcase::new().prepared_soil_profile()?;
    let mut compartments = profile.compartments_at_field_capacity();
    let profile_depth: f64 = compartments
        .iter()
        .map(|compartment| compartment.thickness)
        .sum();
    let bottom = compartments
        .last()
        .expect("the official soil profile should contain a compartment");
    let aquifer_depth = profile_depth - bottom.thickness / 2.0;
    initialize_groundwater_profile(
        &profile,
        &mut compartments,
        aquifer_depth,
        2.0,
        REFERENCE_SALT_SOLUBILITY,
    )?;

    let bottom = compartments
        .last_mut()
        .expect("the official soil profile should contain a compartment");
    let layer = &profile.layers[bottom.layer_index];
    bottom.theta -= 0.01;
    bottom.dissolved_salt.fill(0.0);
    bottom.deposited_salt.fill(0.0);
    let initial_water = layer.water_storage(bottom.theta, bottom.thickness);
    let initial_salt = total_salt(bottom, layer);

    let inflow = horizontal_groundwater_inflow(
        &profile,
        &mut compartments,
        aquifer_depth,
        2.0,
        REFERENCE_SALT_SOLUBILITY,
    )?;
    let bottom = compartments
        .last()
        .expect("the official soil profile should contain a compartment");

    let final_water = layer.water_storage(bottom.theta, bottom.thickness);
    assert_in_delta!(final_water - initial_water, inflow.water, 1.0e-12);

    let final_salt = total_salt(bottom, layer);
    assert_in_delta!(final_salt - initial_salt, inflow.salt, 1.0e-12);

    assert_in_delta!(bottom.theta, layer.saturation / 100.0, f64::EPSILON);

    let conductivity = saturated_extract_conductivity(bottom, layer, REFERENCE_SALT_SOLUBILITY);
    assert_in_delta!(conductivity, 2.0, 1.0e-12);

    Ok(())
}

#[test]
fn root_zone_salinity_uses_depth_weighted_compartment_conductivity() -> TestResult {
    let profile = OfficialTestcase::new().prepared_soil_profile()?;
    let mut compartments = profile.compartments_at_field_capacity();
    for compartment in &mut compartments {
        let layer = &profile.layers[compartment.layer_index];
        initialize_salt_from_extract_conductivity(
            compartment,
            layer,
            4.0,
            REFERENCE_SALT_SOLUBILITY,
        );
    }
    let salinity = determine_root_zone_salinity(
        &profile,
        &compartments,
        0.35,
        0.30,
        &SalinityResponse {
            minimum: Some(2),
            maximum: Some(10),
            shape: 0.0,
        },
        REFERENCE_SALT_SOLUBILITY,
    )?
    .expect("the root depth should be active");

    assert_in_delta!(salinity.extract_conductivity, 4.0, 1.0e-12);
    assert_in_delta!(
        salinity.water_conductivity,
        salinity.field_capacity_conductivity,
        1.0e-12
    );
    assert_in_delta!(salinity.stress_coefficient, 0.75, 1.0e-12);

    Ok(())
}
