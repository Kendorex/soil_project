//! Readers for filesystem-based AquaCrop input formats.

mod calendar;
mod climate;
mod co2;
mod crop;
mod error;
mod groundwater;
mod irrigation;
mod management;
mod output_config;
mod parameters;
mod parse;
mod project;
mod results;
mod runner;
mod sentinel;
mod soil;

pub use calendar::{CropCalendar, CropOnset, parse_crop_calendar, read_crop_calendar};
pub use climate::{
    ClimateFileSet, ClimateRecord, ClimateRecordFrequency, ScalarClimateFile, TemperatureFile,
    parse_climate_file_set, parse_scalar_climate_file, parse_temperature_file,
    read_climate_file_set, read_scalar_climate_file, read_temperature_file,
};
pub use co2::{AtmosphericCo2File, parse_co2_file, read_co2_file};
pub use crop::{parse_crop_file, read_crop_file};
pub use error::InputError;
pub use groundwater::{
    GroundwaterObservation, GroundwaterTable, parse_groundwater_file, read_groundwater_file,
};
pub use irrigation::{parse_irrigation_file, read_irrigation_file};
pub use management::{parse_management_file, read_management_file};
pub use output_config::{
    DailyOutputSection, DailyOutputSelection, OutputRequests, ParticularOutputSection,
    ParticularOutputSelection, read_output_requests,
};
pub use parameters::{
    AquaCropProgramParameters, parse_program_parameters, read_program_parameters,
};
pub use project::{
    DayNumberRange, Project, ProjectFileReference, ProjectRun, parse_project_file,
    read_project_file,
};
pub use results::{
    write_project_daily_results, write_project_evaluation_results, write_project_harvest_results,
    write_project_seasonal_results,
};
pub use runner::{
    ProjectSimulation, load_project_run, run_project, run_project_directory,
    run_project_with_outputs,
};
pub use soil::{parse_soil_profile, read_soil_profile};
