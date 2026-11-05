//! Writes the BMI wrapper trajectory for external comparison.

use std::fs;

use aquacrop_bmi::BmiModel;
use aquacrop_conformance::OfficialTestcase;
use serde_json::json;

fn read_fixture(relative: &str) -> Result<String, Box<dyn std::error::Error>> {
    Ok(fs::read_to_string(
        OfficialTestcase::new().source_root().join(relative),
    )?)
}

fn scenario_json() -> Result<String, Box<dyn std::error::Error>> {
    let scenario = json!({
        "season_year": 1,
        "crop_text": read_fixture("DATA/AlfOttawaGDD.CRO")?,
        "soil_text": read_fixture("DATA/Ottawa.SOL")?,
        "management_text": read_fixture("DATA/Ottawa.MAN")?,
        "temperature_text": read_fixture("DATA/Ottawa.Tnx")?,
        "rain_text": read_fixture("DATA/Ottawa.PLU")?,
        "eto_text": read_fixture("DATA/Ottawa.ETo")?,
        "co2_text": read_fixture("SIMUL/MaunaLoa.CO2")?,
        "simulation_start": {"year": 2014, "month": 5, "day": 21},
        "simulation_end": {"year": 2014, "month": 10, "day": 31},
        "crop_start": {"year": 2014, "month": 5, "day": 21},
        "crop_end": {"year": 2014, "month": 10, "day": 31},
    });
    Ok(scenario.to_string())
}

const SCALAR_OUTPUTS: &[&str] = &[
    "plant_cover~projective",
    "plant_biomass~above-ground",
    "plant_yield~standard",
    "soil_water_actual",
    "plant_stress_water",
    "plant_stress_temperature",
    "plant_stress_aeration",
    "plant_stress_salinity",
    "plant_root_depth",
    "air_transpiration",
    "air_evapotranspiration~plants",
    "plant_biomass_potential",
    "soil_water_actual_layer-1",
    "soil_water_actual_layer-2",
    "soil_water_actual_layer-3",
    "soil_water_actual_layer-4",
    "soil_water_actual_layer-5",
    "soil_water_actual_layer-6",
    "soil_water_actual_layer-7",
    "soil_water_actual_layer-8",
    "soil_water_actual_layer-9",
    "soil_water_actual_layer-10",
    "soil_water_drainage~deep",
    "water_runoff~surface",
    "soil_infiltration~rate",
    "air_evaporation",
    "air_transpiration~actual",
    "soil_water_capillary",
    "air_precipitation",
    "air_temperature_minimum~day",
    "air_temperature_maximal~day",
    "air_evapotranspiration~reference",
    "atmosphere_co2-concentration",
];

const VECTOR_OUTPUTS: &[&str] = &[
    "soil__water_content_in_compartments",
    "soil_depth~compartment",
];

#[test]
fn dump_rust_bmi_daily_trajectory_for_gate5() -> Result<(), Box<dyn std::error::Error>> {
    let mut model = BmiModel::new();
    model.initialize(&scenario_json()?)?;
    let end_time = model.end_time()?;

    let mut days = Vec::new();
    let mut day_index = 0;
    while model.current_time()? < end_time {
        model.update()?;
        day_index += 1;
        let mut record = serde_json::Map::new();
        record.insert("day_index".to_owned(), json!(day_index));
        record.insert("current_time".to_owned(), json!(model.current_time()?));
        for name in SCALAR_OUTPUTS {
            let mut buffer = [0.0_f64];
            match model.get_value(name, &mut buffer) {
                Ok(()) => {
                    record.insert((*name).to_owned(), json!(buffer[0]));
                }
                Err(error) => {
                    record.insert((*name).to_owned(), json!({"error": error.to_string()}));
                }
            }
        }
        for name in VECTOR_OUTPUTS {
            let size = model.grid_size(name)?;
            let mut buffer = vec![0.0_f64; size];
            match model.get_value(name, &mut buffer) {
                Ok(()) => {
                    record.insert((*name).to_owned(), json!(buffer));
                }
                Err(error) => {
                    record.insert((*name).to_owned(), json!({"error": error.to_string()}));
                }
            }
        }
        days.push(serde_json::Value::Object(record));
    }

    let output = json!({ "end_time": end_time, "days": days });
    fs::write(
        std::env::temp_dir().join("gate5_rust_daily.json"),
        serde_json::to_string(&output)?,
    )?;
    Ok(())
}
