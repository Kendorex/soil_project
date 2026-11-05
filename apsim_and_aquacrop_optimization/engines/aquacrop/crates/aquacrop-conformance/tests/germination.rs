//! Germination delay and crop-calendar integration checks.

use aquacrop_conformance::{OfficialTestcase, TestResult};
use aquacrop_core::biomass::REFERENCE_CO2;
use aquacrop_core::climate::DailyWeather;
use aquacrop_core::crop::CropSeason;
use aquacrop_core::engine::{InitialConditions, SimulationConfig};
use aquacrop_core::io::{
    read_crop_file, read_management_file, read_program_parameters, read_soil_profile,
};
use assertables::assert_eq_f64;

#[test]
fn dry_seedbed_delays_the_crop_cycle_until_water_permits_germination() -> TestResult {
    let project = OfficialTestcase::new();
    let root = project.source_root();
    let crop = read_crop_file(root.join("SIMUL/DEFAULT.CRO"))?;
    let maximum_rooting_depth = crop.parameters.rooting.maximum_depth;
    let soil = read_soil_profile(root.join("DATA/Ottawa.SOL"))?;
    let initial_soil = soil.clone().prepare()?;
    let mut management = read_management_file(root.join("DATA/Ottawa.MAN"))?;
    management.fertility_stress = 0;
    let parameters = read_program_parameters(root.join("PARAM/Ottawa.PPn"))?;
    let maturity = crop
        .cycle
        .calendar_days()
        .expect("the default crop should use calendar days")
        .maturity;
    let weather = (1..=maturity + 2)
        .map(|day_number| DailyWeather {
            minimum_temperature: 10.0,
            maximum_temperature: 30.0,
            reference_et: 5.0,
            rain: if day_number == 3 { 100.0 } else { 0.0 },
            carbon_dioxide: REFERENCE_CO2,
        })
        .collect();
    let crop = crop.for_season(CropSeason::new(parameters.gdd_method))?;
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
        simulation_stop: maturity.into(),
        crop_start: 1.into(),
        crop_stop: maturity.into(),
    };
    let mut compartments =
        initial_soil.compartments_at_field_capacity_for_depth(maximum_rooting_depth);
    for compartment in &mut compartments {
        compartment.theta = initial_soil.layers[compartment.layer_index].wilting_point / 100.0;
    }
    let mut simulation = config.prepare()?.initialize(InitialConditions {
        compartments: Some(compartments.into_iter().map(Into::into).collect()),
        surface_water: 0.0,
        surface_conductivity: 0.0,
        stored_assimilates: 0.0,
    })?;

    let first = simulation.step()?.expect("first dry day should be present");
    let second = simulation
        .step()?
        .expect("second dry day should be present");
    let third = simulation
        .step()?
        .expect("wet germination day should be present");

    assert!(!first.germinated);
    assert_eq!(first.delayed_days, 1);
    assert_eq!(first.day_after_planting, Some(0));
    assert_eq_f64!(first.cumulative_growing_degree_days, 0.0_f64);
    assert!(!second.germinated);
    assert_eq!(second.delayed_days, 2);
    assert_eq!(second.day_after_planting, Some(0));
    assert!(third.germinated);
    assert_eq!(third.delayed_days, 0);
    assert_eq!(third.day_after_planting, Some(1));
    assert!(third.cumulative_growing_degree_days > 0.0);

    let mut simulated_days = 3;
    while simulation.step()?.is_some() {
        simulated_days += 1;
    }
    assert_eq!(simulated_days, maturity + 2);
    assert_eq!(
        simulation.finalize().result.last_day_number,
        Some(maturity + 2)
    );

    Ok(())
}
