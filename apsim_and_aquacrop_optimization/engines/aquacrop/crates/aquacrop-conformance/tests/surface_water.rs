//! Ponded surface-water fluxes in the daily engine sequence.

use aquacrop_conformance::OfficialTestcase;
use aquacrop_core::climate::DailyWeather;
use aquacrop_core::crop::{CropCycle, CropSeason};
use aquacrop_core::engine::{InitialConditions, SimulationConfig};
use aquacrop_core::io::{
    read_crop_file, read_management_file, read_program_parameters, read_soil_profile,
};
use assertables::{assert_eq_f64, assert_in_delta};

#[test]
fn ponded_water_evaporates_before_soil_water_and_concentrates_its_salt()
-> Result<(), aquacrop_core::Error> {
    let project = OfficialTestcase::new();
    let root = project.source_root();
    let mut crop = read_crop_file(root.join("DATA/AlfOttawaGDD.CRO"))?;
    let CropCycle::GrowingDegreeDays { calendar_days, .. } = crop.cycle else {
        return Err(aquacrop_core::Error::InvalidCropConfiguration(
            "official crop should use GDD",
        ));
    };
    crop.cycle = CropCycle::CalendarDays { calendar_days };
    let soil = read_soil_profile(root.join("DATA/Ottawa.SOL"))?;
    let mut management = read_management_file(root.join("DATA/Ottawa.MAN"))?;
    management.bund_height = 3.0;
    management.fertility_stress = 0;
    let parameters = read_program_parameters(root.join("PARAM/Ottawa.PPn"))?;
    let crop = crop.for_season(CropSeason::new(parameters.gdd_method))?;
    let initial_water = 2_000.0;
    let initial_conductivity = 2.0;
    let config = SimulationConfig {
        season_year: 1,
        crop,
        soil,
        management,
        irrigation: None,
        parameters: parameters.simulation,
        weather: vec![DailyWeather {
            minimum_temperature: 15.0,
            maximum_temperature: 25.0,
            reference_et: 5.0,
            rain: 0.0,
            carbon_dioxide: 369.41,
        }],
        external_irrigation: None,
        groundwater: None,
        calibration_climate: None,
        simulation_start: 1.into(),
        simulation_stop: 1.into(),
        crop_start: 2.into(),
        crop_stop: 2.into(),
    }
    .prepare()?;
    let _field_capacity_run = config
        .clone()
        .initialize(InitialConditions::at_field_capacity())?;
    let mut simulation = config.initialize(InitialConditions {
        compartments: None,
        surface_water: initial_water,
        surface_conductivity: initial_conductivity,
        stored_assimilates: 0.0,
    })?;
    let daily = simulation
        .step()?
        .expect("the first daily result should exist");
    let completed = simulation.finalize();
    let final_state = completed.checkpoint;

    assert!(daily.evaporation > 0.0);
    assert_eq_f64!(daily.transpiration, 0.0_f64);
    assert!(final_state.surface_water > 0.0);
    assert!(final_state.surface_conductivity > initial_conductivity);
    assert_in_delta!(daily.water_balance_error, 0.0, 1.0e-8);
    assert_in_delta!(
        final_state.surface_conductivity * final_state.surface_water,
        initial_conductivity * (initial_water - daily.infiltration - daily.runoff),
        1.0e-8
    );
    Ok(())
}
