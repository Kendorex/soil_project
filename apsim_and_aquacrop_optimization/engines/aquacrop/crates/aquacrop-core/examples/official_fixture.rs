//! Runs the official Ottawa fixture through the direct Rust API.
//!
//! Unlike the AquaCrop file-I/O workflow, this example reads its configuration
//! from YAML and its weather and atmospheric CO2 observations from CSV. It
//! prepares the direct simulation inputs, runs the daily engine, and prints a
//! seasonal report.

use std::fs::File;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

use aquacrop_core::calendar::CalendarDate;
use aquacrop_core::climate::{
    AtmosphericCo2, Co2Observation, DailyWeather, prepare_calibration_climate,
};
use aquacrop_core::crop::{Crop, CropSeason};
use aquacrop_core::engine::{InitialConditions, SimulationConfig};
use aquacrop_core::management::Management;
use aquacrop_core::output::SimulationResult;
use aquacrop_core::parameters::SimulationParameters;
use aquacrop_core::soil::SoilProfile;
use serde::Deserialize;

const DATA_DIR: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/examples/data");

type DynResult<T> = Result<T, Box<dyn std::error::Error>>;

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct ProjectConfig {
    season_year: i32,
    start_date: CalendarDate,
    stop_date: CalendarDate,
    parameters: SimulationParameters,
    crop: Crop,
    soil: SoilProfile,
    management: Management,
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(err) => {
            eprintln!("{err}");
            ExitCode::FAILURE
        }
    }
}

fn run() -> DynResult<()> {
    let data_dir = Path::new(DATA_DIR);

    let project_file = File::open(data_dir.join("ottawa.yaml"))?;
    let ProjectConfig {
        season_year,
        start_date,
        stop_date,
        parameters,
        crop,
        soil,
        management,
    } = yaml_serde::from_reader(project_file)?;

    let first_day_number = start_date.day_number();
    let last_day_number = stop_date.day_number();

    let atmospheric_co2 = read_co2(data_dir.join("mauna_loa.csv"))?;

    let carbon_dioxide = atmospheric_co2
        .concentration_for_period(first_day_number, last_day_number)?
        .ok_or("failed to calculate CO2 concentration for the simulation period")?;

    let weather = read_weather(data_dir.join("ottawa_weather.csv"), carbon_dioxide)?;

    let calibration_climate = prepare_calibration_climate(start_date, &weather, &atmospheric_co2)?;

    let season_crop = crop.for_season(CropSeason::default())?;

    let simulation_config = SimulationConfig {
        season_year,
        crop: season_crop.clone(),
        soil,
        management: management.clone(),
        irrigation: None,
        parameters,
        weather,
        external_irrigation: None,
        groundwater: None,
        calibration_climate: calibration_climate.into(),
        simulation_start: first_day_number.into(),
        simulation_stop: last_day_number.into(),
        crop_start: first_day_number.into(),
        crop_stop: last_day_number.into(),
    };

    let mut simulation = simulation_config
        .prepare()?
        .initialize(InitialConditions::at_field_capacity())?;

    let mut daily_results = Vec::new();
    while let Some(result) = simulation.step()? {
        daily_results.push(result);
    }

    let completed = simulation.finalize();
    let (result, _checkpoint) = SimulationResult::from_completed(daily_results, completed);
    let seasonal = result.seasonal_report(&season_crop, &management);

    println!("simulated days: {}", result.daily.len());
    println!("dry biomass: {:.3} t/ha", seasonal.biomass);
    println!("dry yield: {:.3} t/ha", seasonal.dry_yield);
    println!("rain: {:.1} mm", seasonal.rain);
    println!("reference ET: {:.1} mm", seasonal.reference_et);
    println!("daily reports: {}", result.daily_reports(Some(20)).len());

    Ok(())
}

fn read_co2(path: PathBuf) -> DynResult<AtmosphericCo2> {
    let file = File::open(path)?;

    csv::Reader::from_reader(file)
        .records()
        .map(|result| {
            let record = result?;
            let year = record[0].parse()?;
            let concentration = record[1].parse()?;
            Ok(Co2Observation {
                year,
                concentration,
            })
        })
        .collect::<Result<Vec<_>, _>>()
        .map(AtmosphericCo2::new)
}

fn read_weather(path: PathBuf, carbon_dioxide: f64) -> DynResult<Vec<DailyWeather>> {
    let file = File::open(path)?;

    csv::Reader::from_reader(file)
        .records()
        .map(|result| {
            let rescord = result?;
            let minimum_temperature = rescord[0].parse()?;
            let maximum_temperature = rescord[1].parse()?;
            let reference_et = rescord[2].parse()?;
            let rain = rescord[3].parse()?;

            Ok(DailyWeather {
                minimum_temperature,
                maximum_temperature,
                reference_et,
                rain,
                carbon_dioxide,
            })
        })
        .collect()
}
