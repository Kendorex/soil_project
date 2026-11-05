//! Conformance checks for the vendored official project file.

use aquacrop_conformance::OfficialTestcase;
use aquacrop_core::AquaCropVersion;
use aquacrop_core::io::{DayNumberRange, read_project_file};

#[test]
fn official_project_file_declares_three_aquacrop_7_3_runs() {
    let path = OfficialTestcase::new()
        .source_root()
        .join("LIST/Ottawa.PRM");
    let project = read_project_file(path).expect("the official project file should parse");

    assert_eq!(project.version, AquaCropVersion::new(7, 3));
    assert_eq!(project.runs.len(), 3);
}

#[test]
fn official_project_first_run_preserves_periods_and_input_references() {
    let path = OfficialTestcase::new()
        .source_root()
        .join("LIST/Ottawa.PRM");
    let project = read_project_file(path).expect("the official project file should parse");
    let run = project
        .runs
        .first()
        .expect("the official project should contain a first run");

    assert_eq!(run.cultivation_year, 1);
    assert_eq!(
        run.simulation_period,
        DayNumberRange {
            first: 41_414,
            last: 41_577,
        }
    );
    assert_eq!(
        run.crop_period,
        DayNumberRange {
            first: 41_414,
            last: 41_577,
        }
    );
    assert_eq!(run.climate.filename, "Ottawa.CLI");
    assert_eq!(run.climate.directory, "./DATA/");
    assert_eq!(run.soil.filename, "Ottawa.SOL");
    assert_eq!(run.irrigation.filename, "(None)");
}

#[test]
fn official_project_later_runs_request_previous_soil_water_profile() {
    let path = OfficialTestcase::new()
        .source_root()
        .join("LIST/Ottawa.PRM");
    let project = read_project_file(path).expect("the official project file should parse");
    let second = project
        .runs
        .get(1)
        .expect("the official project should contain a second run");
    let third = project
        .runs
        .get(2)
        .expect("the official project should contain a third run");

    assert_eq!(second.initial_conditions.filename, "KeepSWC");
    assert_eq!(third.initial_conditions.filename, "KeepSWC");
}
