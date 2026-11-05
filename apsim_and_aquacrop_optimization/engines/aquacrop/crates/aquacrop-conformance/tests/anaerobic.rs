//! Deficient-aeration stress in the daily transpiration path.

use aquacrop_conformance::{OfficialTestcase, TestResult};
use aquacrop_core::climate::DailyWeather;
use aquacrop_core::crop::{CropSeason, PlantingMethod};
use aquacrop_core::engine::{InitialConditions, SimulationConfig};
use aquacrop_core::io::{
    read_crop_file, read_management_file, read_program_parameters, read_soil_profile,
};
use assertables::assert_in_delta;

#[test]
fn saturated_root_zone_applies_the_source_oxygen_stress_ramp() -> TestResult {
    let project = OfficialTestcase::new();
    let root = project.source_root();
    let mut crop = read_crop_file(root.join("DATA/AlfOttawaGDD.CRO"))?;
    crop.parameters.planting_method = PlantingMethod::Transplant;
    crop.cycle
        .calendar_days_mut()
        .expect("the converted crop should use calendar days")
        .emergence = 0;
    let maximum_rooting_depth = crop.parameters.rooting.maximum_depth;
    let mut soil = read_soil_profile(root.join("DATA/Ottawa.SOL"))?;
    for layer in &mut soil.layers {
        layer.saturated_hydraulic_conductivity = 0.0;
    }
    let initial_soil = soil.clone().prepare()?;
    let mut management = read_management_file(root.join("DATA/Ottawa.MAN"))?;
    management.fertility_stress = 0;
    let parameters = read_program_parameters(root.join("PARAM/Ottawa.PPn"))?;
    let crop = crop.for_season(CropSeason::new(parameters.gdd_method))?;
    let weather = (1..=3)
        .map(|_| DailyWeather {
            minimum_temperature: 15.0,
            maximum_temperature: 25.0,
            reference_et: 0.1,
            rain: 0.0,
            carbon_dioxide: 369.41,
        })
        .collect();
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
        calibration_climate: None,
        simulation_start: 1.into(),
        simulation_stop: 3.into(),
        crop_start: 1.into(),
        crop_stop: 3.into(),
    };
    let mut compartments =
        initial_soil.compartments_at_field_capacity_for_depth(maximum_rooting_depth);
    for compartment in &mut compartments {
        compartment.theta = (initial_soil.layers[compartment.layer_index].saturation - 1.0) / 100.0;
    }
    let mut simulation = config.prepare()?.initialize(InitialConditions {
        compartments: Some(compartments.into_iter().map(Into::into).collect()),
        surface_water: 0.0,
        surface_conductivity: 0.0,
        stored_assimilates: 0.0,
    })?;

    let first = simulation
        .step()?
        .expect("the first daily result should exist");
    let second = simulation
        .step()?
        .expect("the second daily result should exist");
    let third = simulation
        .step()?
        .expect("the third daily result should exist");

    assert!((0.0..1.0).contains(&first.anaerobic_stress_coefficient));
    assert!(second.anaerobic_stress_coefficient < first.anaerobic_stress_coefficient);
    assert!(third.anaerobic_stress_coefficient < second.anaerobic_stress_coefficient);
    assert_in_delta!(first.water_balance_error, 0.0, 1.0e-8);
    assert_in_delta!(second.water_balance_error, 0.0, 1.0e-8);
    assert_in_delta!(third.water_balance_error, 0.0, 1.0e-8);

    Ok(())
}
