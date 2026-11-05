//! Harvest-index and yield checks using the official crop parameters.

use aquacrop_conformance::{OfficialTestcase, TestResult};
use aquacrop_core::biomass::REFERENCE_CO2;
use aquacrop_core::climate::DailyWeather;
use aquacrop_core::crop::CropSeason;
use aquacrop_core::engine::{FinalResult, InitialConditions, SimulationConfig};
use aquacrop_core::harvest::{HarvestIndexState, dry_yield, reference_harvest_index_for_day};
use aquacrop_core::io::{
    read_crop_file, read_management_file, read_program_parameters, read_soil_profile,
};
use assertables::assert_in_delta;

#[test]
fn official_forage_crop_reaches_its_reference_harvest_index() -> TestResult {
    let crop = OfficialTestcase::new().prepared_gdd_crop()?;
    let mut state = HarvestIndexState::new(&crop);
    let early = reference_harvest_index_for_day(&crop, &mut state, 1, 0, 0.5, 0.5, 5);
    let mature = reference_harvest_index_for_day(&crop, &mut state, 17, 0, 0.5, 0.5, 5);

    assert!(mature.percent > early.percent);
    assert_in_delta!(
        mature.percent,
        f64::from(crop.parameters.productivity.reference_harvest_index),
        f64::EPSILON
    );
    assert_in_delta!(dry_yield(10.0, mature.percent, 80.0, 1.1), 8.8, 1.0e-12);

    Ok(())
}

#[test]
fn cold_pollination_reduces_grain_harvest_index() -> TestResult {
    let favorable = simulate_default_grain(10.0)?;
    let cold = simulate_default_grain(3.0)?;

    let favorable_harvest_index = favorable.dry_yield / favorable.biomass;
    let cold_harvest_index = cold.dry_yield / cold.biomass;
    assert!(cold_harvest_index < favorable_harvest_index);

    Ok(())
}

fn simulate_default_grain(minimum_temperature: f64) -> Result<FinalResult, aquacrop_core::Error> {
    let project = OfficialTestcase::new();
    let root = project.source_root();
    let crop = read_crop_file(root.join("SIMUL/DEFAULT.CRO"))?;
    let soil = read_soil_profile(root.join("DATA/Ottawa.SOL"))?;
    let mut management = read_management_file(root.join("DATA/Ottawa.MAN"))?;
    management.fertility_stress = 0;
    let parameters = read_program_parameters(root.join("PARAM/Ottawa.PPn"))?;
    let last_day_number = crop
        .cycle
        .calendar_days()
        .ok_or(aquacrop_core::Error::InvalidCropConfiguration(
            "default crop should use calendar days",
        ))?
        .maturity;
    let weather = (1..=last_day_number)
        .map(|_| DailyWeather {
            minimum_temperature,
            maximum_temperature: 30.0,
            reference_et: 5.0,
            rain: 5.0,
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
        simulation_stop: last_day_number.into(),
        crop_start: 1.into(),
        crop_stop: last_day_number.into(),
    };
    let mut simulation = config
        .prepare()?
        .initialize(InitialConditions::at_field_capacity())?;
    while simulation.step()?.is_some() {}
    Ok(simulation.finalize().result)
}
