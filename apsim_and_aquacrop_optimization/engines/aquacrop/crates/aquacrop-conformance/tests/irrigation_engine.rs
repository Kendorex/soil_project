//! Irrigation schedules in the daily simulation lifecycle.

use aquacrop_conformance::OfficialTestcase;
use aquacrop_core::climate::DailyWeather;
use aquacrop_core::crop::{CropSeason, PlantingMethod};
use aquacrop_core::engine::{InitialConditions, Simulation, SimulationConfig};
use aquacrop_core::io::{
    parse_irrigation_file, read_crop_file, read_management_file, read_program_parameters,
    read_soil_profile,
};
use assertables::{assert_eq_f64, assert_in_delta};

#[test]
fn manual_schedule_supplies_daily_depth_and_conductivity() -> Result<(), aquacrop_core::Error> {
    let schedule =
        parse_irrigation_file("Manual schedule\n7.3\n1\n100\n1\n1\n\nDay depth ECw\n1 30 0.8\n")?;
    let mut simulation = one_day_simulation(schedule, 1)?;

    let result = simulation
        .step()?
        .expect("the first daily result should exist");

    assert_in_delta!(result.irrigation, 30.0, 1.0e-12);
    assert_in_delta!(result.water_balance_error, 0.0, 1.0e-8);
    Ok(())
}

#[test]
fn generated_fixed_interval_applies_depth_before_infiltration() -> Result<(), aquacrop_core::Error>
{
    let schedule = parse_irrigation_file(
        "Generated schedule\n7.3\n1\n100\n2\n1\n2\n0\n\nRules\n1 1 20 0.5\n",
    )?;
    let mut simulation = one_day_simulation(schedule, 1)?;

    let result = simulation
        .step()?
        .expect("the first daily result should exist");

    assert_in_delta!(result.irrigation, 20.0, 1.0e-12);
    assert_in_delta!(result.water_balance_error, 0.0, 1.0e-8);
    Ok(())
}

#[test]
fn generated_schedule_obeys_end_exclusion_inside_daily_engine() -> Result<(), aquacrop_core::Error>
{
    let schedule = parse_irrigation_file(
        "Generated schedule\n7.3\n1\n100\n2\n1\n2\n1\n\nRules\n1 1 20 0.5\n",
    )?;
    let mut simulation = one_day_simulation(schedule, 1)?;

    let result = simulation
        .step()?
        .expect("the first daily result should exist");

    assert_eq_f64!(result.irrigation, 0.0_f64);
    assert_in_delta!(result.water_balance_error, 0.0, 1.0e-8);
    Ok(())
}

#[test]
fn net_requirement_refills_the_root_zone_after_extraction() -> Result<(), aquacrop_core::Error> {
    let schedule = parse_irrigation_file("Net irrigation\n7.3\n1\n100\n3\n50\n")?;
    let soil = OfficialTestcase::new().prepared_soil_profile()?;
    let mut compartments = soil.compartments_at_field_capacity();
    for compartment in &mut compartments {
        let layer = &soil.layers[compartment.layer_index];
        compartment.theta = (layer.field_capacity + layer.wilting_point) / 200.0;
    }
    let mut simulation = simulation_with_initial(
        schedule,
        1,
        1.0,
        InitialConditions {
            compartments: Some(compartments.into_iter().map(Into::into).collect()),
            surface_water: 0.0,
            surface_conductivity: 0.0,
            stored_assimilates: 0.0,
        },
    )?;

    let result = simulation
        .step()?
        .expect("the first daily result should exist");

    assert!(result.irrigation > 0.0);
    assert_in_delta!(result.water_balance_error, 0.0, 1.0e-8);
    Ok(())
}

#[test]
fn net_requirement_is_not_applied_without_potential_transpiration()
-> Result<(), aquacrop_core::Error> {
    let schedule = parse_irrigation_file("Net irrigation\n7.3\n1\n100\n3\n50\n")?;
    let soil = OfficialTestcase::new().prepared_soil_profile()?;
    let mut compartments = soil.compartments_at_field_capacity();
    for compartment in &mut compartments {
        compartment.theta = soil.layers[compartment.layer_index].wilting_point / 100.0;
    }
    let mut simulation = simulation_with_initial(
        schedule,
        1,
        0.0,
        InitialConditions {
            compartments: Some(compartments.into_iter().map(Into::into).collect()),
            surface_water: 0.0,
            surface_conductivity: 0.0,
            stored_assimilates: 0.0,
        },
    )?;
    let result = simulation
        .step()?
        .expect("the first daily result should exist");

    assert_eq_f64!(result.irrigation, 0.0_f64);
    assert_in_delta!(result.water_balance_error, 0.0, 1.0e-8);
    Ok(())
}

#[test]
fn generated_depletion_rule_uses_projected_pre_infiltration_water()
-> Result<(), aquacrop_core::Error> {
    let schedule = parse_irrigation_file(
        "Generated depletion\n7.3\n1\n100\n2\n2\n2\n0\n\nRules\n1 1 15 0.5\n",
    )?;
    let soil = OfficialTestcase::new().prepared_soil_profile()?;
    let mut compartments = soil.compartments_at_field_capacity();
    for compartment in &mut compartments {
        compartment.theta = soil.layers[compartment.layer_index].wilting_point / 100.0;
    }
    let mut simulation = simulation_with_initial(
        schedule,
        1,
        1.0,
        InitialConditions {
            compartments: Some(compartments.into_iter().map(Into::into).collect()),
            surface_water: 0.0,
            surface_conductivity: 0.0,
            stored_assimilates: 0.0,
        },
    )?;

    let result = simulation
        .step()?
        .expect("the first daily result should exist");

    assert_in_delta!(result.irrigation, 15.0, 1.0e-12);
    assert_in_delta!(result.water_balance_error, 0.0, 1.0e-8);
    Ok(())
}

fn one_day_simulation(
    irrigation: aquacrop_core::irrigation::IrrigationSchedule,
    last_crop_day_number: i32,
) -> Result<Simulation, aquacrop_core::Error> {
    simulation_with_initial(
        irrigation,
        last_crop_day_number,
        1.0,
        InitialConditions::at_field_capacity(),
    )
}

fn simulation_with_initial(
    irrigation: aquacrop_core::irrigation::IrrigationSchedule,
    last_crop_day_number: i32,
    reference_et: f64,
    initial: InitialConditions,
) -> Result<Simulation, aquacrop_core::Error> {
    let project = OfficialTestcase::new();
    let root = project.source_root();
    let mut crop = read_crop_file(root.join("DATA/AlfOttawaGDD.CRO"))?;
    crop.parameters.planting_method = PlantingMethod::Transplant;
    let calendar_days =
        crop.cycle
            .calendar_days_mut()
            .ok_or(aquacrop_core::Error::InvalidCropConfiguration(
                "official crop should contain complete calendar days",
            ))?;
    calendar_days.emergence = 0;
    let soil = read_soil_profile(root.join("DATA/Ottawa.SOL"))?;
    let mut management = read_management_file(root.join("DATA/Ottawa.MAN"))?;
    management.fertility_stress = 0;
    let parameters = read_program_parameters(root.join("PARAM/Ottawa.PPn"))?;
    let crop = crop.for_season(CropSeason::new(parameters.gdd_method))?;
    let config = SimulationConfig {
        season_year: 1,
        crop,
        soil,
        management,
        irrigation: Some(irrigation),
        parameters: parameters.simulation,
        weather: vec![DailyWeather {
            minimum_temperature: 15.0,
            maximum_temperature: 25.0,
            reference_et,
            rain: 0.0,
            carbon_dioxide: 369.41,
        }],
        external_irrigation: None,
        groundwater: None,
        calibration_climate: None,
        simulation_start: 1.into(),
        simulation_stop: 1.into(),
        crop_start: 1.into(),
        crop_stop: last_crop_day_number.into(),
    };

    config.prepare()?.initialize(initial)
}
