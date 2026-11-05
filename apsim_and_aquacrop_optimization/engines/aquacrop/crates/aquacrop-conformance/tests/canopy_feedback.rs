//! Canopy feedback from failed transpiration.

use aquacrop_conformance::{OfficialTestcase, TestResult};
use aquacrop_core::climate::DailyWeather;
use aquacrop_core::crop::{CropSeason, PlantingMethod};
use aquacrop_core::engine::{InitialConditions, SimulationConfig};
use aquacrop_core::io::{
    read_crop_file, read_management_file, read_program_parameters, read_soil_profile,
};
use assertables::assert_eq_f64;

#[test]
fn zero_transpiration_cancels_daily_canopy_growth() -> TestResult {
    let project = OfficialTestcase::new();
    let root = project.source_root();
    let mut crop = read_crop_file(root.join("SIMUL/DEFAULT.CRO"))?;
    crop.parameters.planting_method = PlantingMethod::Transplant;
    crop.cycle
        .calendar_days_mut()
        .expect("the default crop should use calendar days")
        .emergence = 0;
    crop.parameters.water_stress.anaerobic_point_volume = 5;
    crop.parameters.canopy.seedling_size =
        0.01 * 100_000_000.0 / f64::from(crop.parameters.canopy.planting_density);
    let maximum_rooting_depth = crop.parameters.rooting.maximum_depth;
    let mut soil = read_soil_profile(root.join("DATA/Ottawa.SOL"))?;
    for layer in &mut soil.layers {
        layer.field_capacity = layer.saturation;
    }
    let initial_soil = soil.clone().prepare()?;
    let mut management = read_management_file(root.join("DATA/Ottawa.MAN"))?;
    management.fertility_stress = 0;
    let mut parameters = read_program_parameters(root.join("PARAM/Ottawa.PPn"))?;
    parameters.deficient_aeration_delay = 1;
    parameters.wet_bare_soil_coefficient = 0.0;
    let crop = crop.for_season(CropSeason::new(parameters.gdd_method))?;
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
        crop_start: 1.into(),
        crop_stop: 1.into(),
    };
    let mut compartments =
        initial_soil.compartments_at_field_capacity_for_depth(maximum_rooting_depth);
    for compartment in &mut compartments {
        compartment.theta = initial_soil.layers[compartment.layer_index].saturation / 100.0;
    }
    let mut simulation = config.prepare()?.initialize(InitialConditions {
        compartments: Some(compartments.into_iter().map(Into::into).collect()),
        surface_water: 0.0,
        surface_conductivity: 0.0,
        stored_assimilates: 0.0,
    })?;
    let result = simulation
        .step()?
        .expect("the first daily result should exist");

    assert!(result.potential_transpiration > 0.0);
    assert_eq_f64!(result.transpiration, 0.0_f64);
    assert_eq_f64!(result.canopy_cover, 0.0_f64);

    Ok(())
}
