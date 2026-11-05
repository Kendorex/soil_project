//! Reader compatibility checks for official AquaCrop testcase files.

use aquacrop_conformance::{OfficialTestcase, TestResult};
use aquacrop_core::io::{
    read_climate_file_set, read_co2_file, read_crop_calendar, read_scalar_climate_file,
    read_temperature_file,
};

#[test]
fn reads_official_climate_file_formats() -> TestResult {
    let project = OfficialTestcase::new();
    let root = project.source_root();

    read_climate_file_set(root.join("DATA/Ottawa.CLI"))?;
    read_temperature_file(root.join("DATA/Ottawa.Tnx"))?;
    read_scalar_climate_file(root.join("DATA/Ottawa.ETo"))?;
    read_scalar_climate_file(root.join("DATA/Ottawa.PLU"))?;

    Ok(())
}

#[test]
fn reads_official_calendar_and_co2_file_formats() -> TestResult {
    let project = OfficialTestcase::new();
    let root = project.source_root();

    read_crop_calendar(root.join("DATA/21May.CAL"))?;
    read_co2_file(root.join("SIMUL/MaunaLoa.CO2"))?;

    Ok(())
}
