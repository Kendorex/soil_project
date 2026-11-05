use std::fmt::Write as _;
use std::fs;
use std::io::ErrorKind;
use std::path::{Path, PathBuf};

use crate::climate::prepare_calibration_climate;
use crate::crop::CropSeason;
use crate::engine::{
    Groundwater, InitialConditions, Simulation, SimulationCheckpoint, SimulationConfig,
};
use crate::io::read_soil_profile;
use crate::output::SimulationResult;
use crate::{Error, irrigation::IrrigationSchedule};

use super::climate::align_daily_weather;
use super::{
    ClimateRecordFrequency, DailyOutputSection, GroundwaterTable, ParticularOutputSection,
    ProjectFileReference, ProjectRun, read_co2_file, read_crop_file, read_groundwater_file,
    read_irrigation_file, read_management_file, read_output_requests, read_program_parameters,
    read_project_file, read_scalar_climate_file, read_temperature_file,
    write_project_daily_results, write_project_evaluation_results, write_project_harvest_results,
    write_project_seasonal_results,
};

/// Results of every run loaded from one AquaCrop project file.
#[derive(Debug)]
pub struct ProjectSimulation {
    /// Simulation results in project-file order.
    pub runs: Vec<SimulationResult>,
    /// State after the final run, retained for linked-run compatibility.
    pub final_checkpoint: SimulationCheckpoint,
}

/// Executes every run in an AquaCrop project, including linked-state carry-over.
///
/// `root` is the project-directory root containing the standard AquaCrop
/// subdirectories, and `project_file` is relative to that root. Numeric results
/// retain the units documented by [`crate::engine::DailyResult`] and
/// [`crate::engine::FinalResult`].
///
/// # Errors
///
/// Returns [`Error`] when project loading or simulation fails, an initial-state
/// directive is unsupported, or `KeepSWC` has no preceding run to supply state.
pub fn run_project(
    root: impl AsRef<Path>,
    project_file: impl AsRef<Path>,
) -> Result<ProjectSimulation, Error> {
    let root = root.as_ref();
    let project_file = project_file.as_ref();
    let project = read_project_file(root.join(project_file))?;
    let mut completed_runs = Vec::with_capacity(project.runs.len());
    let mut previous = None;

    for (run_index, run) in project.runs.iter().enumerate() {
        let initial = linked_initial_conditions(run, previous.take())?;
        let mut simulation = load_project_run(root, project_file, run_index, initial)?;
        let mut daily = Vec::new();
        while let Some(result) = simulation.step()? {
            daily.push(result);
        }
        let completed = simulation.finalize();
        let (result, checkpoint) = SimulationResult::from_completed(daily, completed);
        completed_runs.push(result);
        previous = Some(checkpoint);
    }

    let final_checkpoint = previous.ok_or(Error::InvalidSimulationConfiguration(
        "project does not contain a simulation run",
    ))?;
    Ok(ProjectSimulation {
        runs: completed_runs,
        final_checkpoint,
    })
}

/// Runs a project in an AquaCrop directory and creates its requested result files.
///
/// `root` contains the standard AquaCrop subdirectories and `project_file` is
/// relative to it. All numeric input and output values retain their documented
/// core units.
///
/// # Errors
///
/// Returns [`Error`] when the project cannot be simulated, output selections
/// cannot be read, or a requested result file cannot be created.
pub fn run_project_with_outputs(
    root: impl AsRef<Path>,
    project_file: impl AsRef<Path>,
) -> Result<ProjectSimulation, Error> {
    let root = root.as_ref();
    let project_file = project_file.as_ref();
    let requests = read_output_requests(root)?;
    let simulation = run_project(root, project_file)?;
    fs::create_dir_all(root.join("OUTP")).map_err(super::InputError::from)?;
    write_requested_outputs(root, project_file, &simulation, &requests)?;
    write_run_status(root, &[project_file.to_path_buf()], &requests)?;
    Ok(simulation)
}

/// Runs every project selected by an AquaCrop directory.
///
/// Projects are read from `root/LIST/ListProjects.txt`. When that file is
/// absent, all `.PRO` and `.PRM` files directly in `root/LIST` are selected in
/// filename order, matching the reference executable. Numeric values retain
/// the units documented by [`ProjectSimulation`].
///
/// # Errors
///
/// Returns [`Error`] when the directory cannot be inspected, a selected
/// project cannot be simulated, or a requested output cannot be written.
pub fn run_project_directory(root: impl AsRef<Path>) -> Result<Vec<ProjectSimulation>, Error> {
    let root = root.as_ref();
    let requests = read_output_requests(root)?;
    let project_files = selected_project_files(root)?;
    fs::create_dir_all(root.join("OUTP")).map_err(super::InputError::from)?;
    let mut simulations = Vec::with_capacity(project_files.len());
    for project_file in &project_files {
        let simulation = run_project(root, project_file)?;
        write_requested_outputs(root, project_file, &simulation, &requests)?;
        simulations.push(simulation);
    }
    write_run_status(root, &project_files, &requests)?;
    Ok(simulations)
}

fn write_requested_outputs(
    root: &Path,
    project_file: &Path,
    simulation: &ProjectSimulation,
    requests: &super::OutputRequests,
) -> Result<(), Error> {
    write_project_seasonal_results(root, project_file, simulation)?;
    if [
        DailyOutputSection::WaterBalance,
        DailyOutputSection::PreparedCrop,
        DailyOutputSection::SoilWater,
        DailyOutputSection::Salinity,
        DailyOutputSection::CompartmentWater,
        DailyOutputSection::CompartmentSalinity,
        DailyOutputSection::Climate,
    ]
    .iter()
    .any(|section| requests.daily.contains(section))
    {
        write_project_daily_results(root, project_file, simulation)?;
    }
    if requests
        .particular
        .contains(&ParticularOutputSection::Harvests)
    {
        write_project_harvest_results(root, project_file, simulation)?;
    }
    if requests
        .particular
        .contains(&ParticularOutputSection::Evaluation)
    {
        write_project_evaluation_results(root, project_file, simulation)?;
    }
    Ok(())
}

fn write_run_status(
    root: &Path,
    project_files: &[PathBuf],
    requests: &super::OutputRequests,
) -> Result<(), Error> {
    let aggregation = match requests.aggregation {
        None => "None created",
        Some(crate::output::OutputAggregation::Daily) => "daily results",
        Some(crate::output::OutputAggregation::TenDaily) => "10-daily results",
        Some(crate::output::OutputAggregation::Monthly) => "monthly results",
    };
    let mut list = format!("Intermediate results: {aggregation}\n\n");
    let daily = [
        (DailyOutputSection::WaterBalance, "1. - soil water balance"),
        (
            DailyOutputSection::PreparedCrop,
            "2. - crop development and production",
        ),
        (
            DailyOutputSection::SoilWater,
            "3. - soil water content in the soil profile and root zone",
        ),
        (
            DailyOutputSection::Salinity,
            "4. - soil salinity in the soil profile and root zone",
        ),
        (
            DailyOutputSection::CompartmentWater,
            "5. - soil water content at various depths of the soil profile",
        ),
        (
            DailyOutputSection::CompartmentSalinity,
            "6. - soil salinity at various depths of the soil profile",
        ),
        (DailyOutputSection::Climate, "7. - climate input parameters"),
        (
            DailyOutputSection::Irrigation,
            "8. - irrigation events and intervals",
        ),
    ];
    if daily
        .iter()
        .any(|(section, _)| requests.daily.contains(section))
    {
        list.push_str("Daily output results:\n");
        for (section, description) in daily {
            if requests.daily.contains(&section) {
                list.push_str(description);
                list.push('\n');
            }
        }
    } else {
        list.push_str("Daily output results: None created\n");
    }
    list.push('\n');
    let harvests = requests
        .particular
        .contains(&ParticularOutputSection::Harvests);
    let evaluation = requests
        .particular
        .contains(&ParticularOutputSection::Evaluation);
    if harvests || evaluation {
        list.push_str("Particular results:\n");
        if harvests {
            list.push_str(
                "1. - biomass and yield at multiple cuttings(for herbaceous forage crops)\n",
            );
        }
        if evaluation {
            list.push_str("2. - evaluation of simulation results (when Field Data)\n");
        }
    } else {
        list.push_str("Particular results: None created\n");
    }
    for (index, project_file) in project_files.iter().enumerate() {
        let filename = project_file
            .file_name()
            .and_then(|value| value.to_str())
            .ok_or(Error::InvalidSimulationConfiguration(
                "project filename is not valid UTF-8",
            ))?;
        write!(
            list,
            "\n\n{:8}. - {filename} : Project loaded - with its program parameters",
            index + 1
        )
        .map_err(|_| Error::InvalidSimulationConfiguration("failed to format project status"))?;
    }
    list.push('\n');
    fs::write(root.join("OUTP/ListProjectsLoaded.OUT"), list).map_err(super::InputError::from)?;
    fs::write(root.join("OUTP/AllDone.OUT"), "All done\n").map_err(super::InputError::from)?;
    Ok(())
}

fn selected_project_files(root: &Path) -> Result<Vec<PathBuf>, Error> {
    let list_directory = root.join("LIST");
    let selection_file = list_directory.join("ListProjects.txt");
    match fs::read_to_string(&selection_file) {
        Ok(input) => Ok(input
            .lines()
            .filter_map(|line| line.split_whitespace().next())
            .map(|filename| Path::new("LIST").join(filename))
            .collect()),
        Err(error) if error.kind() == ErrorKind::NotFound => {
            let mut projects = Vec::new();
            for entry in fs::read_dir(&list_directory).map_err(super::InputError::from)? {
                let entry = entry.map_err(super::InputError::from)?;
                if !entry
                    .file_type()
                    .map_err(super::InputError::from)?
                    .is_file()
                {
                    continue;
                }
                let path = entry.path();
                let Some(extension) = path.extension().and_then(|value| value.to_str()) else {
                    continue;
                };
                if extension.eq_ignore_ascii_case("PRO") || extension.eq_ignore_ascii_case("PRM") {
                    projects.push(Path::new("LIST").join(entry.file_name()));
                }
            }
            projects.sort();
            Ok(projects)
        }
        Err(error) => Err(super::InputError::from(error).into()),
    }
}

/// Loads one run from an AquaCrop project directory into the daily simulation engine.
///
/// `root` is the project-directory root containing `DATA`, `LIST`, `PARAM`, and
/// `SIMUL`. `project_file` is relative to that root, and `run_index` is zero-based.
/// Numeric input values retain the units documented by their parsed core types.
///
/// # Errors
///
/// Returns [`Error`] when a referenced input cannot be read, the requested run is
/// absent, the climate inputs are not daily, or their date ranges do not cover the run.
pub fn load_project_run(
    root: impl AsRef<Path>,
    project_file: impl AsRef<Path>,
    run_index: usize,
    initial: InitialConditions,
) -> Result<Simulation, Error> {
    let root = root.as_ref();
    let project_file = project_file.as_ref();
    let project_path = root.join(project_file);
    let project = read_project_file(&project_path)?;
    let run = project
        .runs
        .get(run_index)
        .ok_or(Error::InvalidSimulationConfiguration(
            "project does not contain the requested run",
        ))?;

    let crop = read_crop_file(resolve_reference(root, &run.crop)?)?;
    let soil = read_soil_profile(resolve_reference(root, &run.soil)?)?;
    let management = read_management_file(resolve_reference(root, &run.management)?)?;
    let irrigation = optional_irrigation(root, &run.irrigation)?;
    let groundwater = optional_groundwater(root, &run.groundwater)?;
    let parameter_name = project_path
        .file_stem()
        .and_then(|stem| stem.to_str())
        .ok_or(Error::InvalidSimulationConfiguration(
            "project filename has no valid parameter-file stem",
        ))?;
    let program_parameters =
        read_program_parameters(root.join("PARAM").join(format!("{parameter_name}.PPn")))?;

    let temperatures = read_temperature_file(resolve_reference(root, &run.temperature)?)?;
    let reference_et = read_scalar_climate_file(resolve_reference(root, &run.reference_et)?)?;
    let rain = read_scalar_climate_file(resolve_reference(root, &run.rain)?)?;
    ensure_daily(temperatures.record.frequency)?;
    ensure_daily(reference_et.record.frequency)?;
    ensure_daily(rain.record.frequency)?;

    let carbon_dioxide = read_co2_file(resolve_reference(root, &run.co2)?)?;
    let crop_temperature_start =
        usize::try_from(run.crop_period.first - temperatures.record.first_date.day_number())
            .map_err(|_| {
                Error::InvalidSimulationConfiguration(
                    "temperature series begins after the crop period",
                )
            })?;
    let crop_temperatures = temperatures
        .observations
        .get(crop_temperature_start..)
        .ok_or(Error::InvalidSimulationConfiguration(
            "temperature series does not cover the crop period",
        ))?;
    let crop = crop.for_season(CropSeason::new_with_temperatures(
        program_parameters.gdd_method,
        crop_temperatures,
    ))?;
    let weather = align_daily_weather(
        run.simulation_period.first,
        run.crop_period.first,
        run.crop_period.last,
        &temperatures,
        &reference_et,
        &rain,
        &carbon_dioxide.series,
    )?;
    let calibration_climate = prepare_calibration_climate(
        temperatures.record.first_date,
        &temperatures.observations,
        &carbon_dioxide.series,
    )?;

    // A groundwater table only ever produces a day-independent gap when it declares mode 0 (no
    // observations at all) -- `GroundwaterTable::at` otherwise always resolves a day to a value,
    // extrapolating flat before/after its declared range. `collect::<Option<Vec<_>>>` therefore
    // yields the whole aligned series when the table has real observations, and `None` uniformly
    // when it does not, matching `SimulationConfig::groundwater`'s "every weather day, or none at
    // all" contract exactly.
    let groundwater = groundwater.and_then(|table| {
        (0..weather.len())
            .map(|offset| {
                let day = run.simulation_period.first + i32::try_from(offset).unwrap_or(i32::MAX);
                table.at(day)
            })
            .collect::<Option<Vec<(f64, f64)>>>()
    });
    let groundwater = groundwater.map(|series| {
        series
            .into_iter()
            .map(|(depth, conductivity)| Groundwater {
                depth,
                conductivity,
            })
            .collect()
    });

    let config = SimulationConfig {
        season_year: i32::from(run.cultivation_year),
        crop,
        soil,
        management,
        irrigation,
        parameters: program_parameters.simulation,
        weather,
        external_irrigation: None,
        groundwater,
        calibration_climate: Some(calibration_climate),
        simulation_start: run.simulation_period.first.into(),
        simulation_stop: run.simulation_period.last.into(),
        crop_start: run.crop_period.first.into(),
        crop_stop: run.crop_period.last.into(),
    };

    config.prepare()?.initialize(initial)
}

fn resolve_reference(root: &Path, reference: &ProjectFileReference) -> Result<PathBuf, Error> {
    if reference.filename.starts_with('(') || reference.filename == "KeepSWC" {
        return Err(Error::InvalidSimulationConfiguration(
            "required project input is not a filesystem reference",
        ));
    }
    Ok(root.join(&reference.directory).join(&reference.filename))
}

fn optional_irrigation(
    root: &Path,
    reference: &ProjectFileReference,
) -> Result<Option<IrrigationSchedule>, Error> {
    if reference.filename == "(None)" {
        Ok(None)
    } else {
        read_irrigation_file(resolve_reference(root, reference)?).map(Some)
    }
}

fn optional_groundwater(
    root: &Path,
    reference: &ProjectFileReference,
) -> Result<Option<GroundwaterTable>, Error> {
    if reference.filename == "(None)" {
        Ok(None)
    } else {
        read_groundwater_file(resolve_reference(root, reference)?).map(Some)
    }
}

fn linked_initial_conditions(
    run: &ProjectRun,
    previous: Option<SimulationCheckpoint>,
) -> Result<InitialConditions, Error> {
    match run.initial_conditions.filename.as_str() {
        "(None)" => Ok(InitialConditions {
            compartments: None,
            surface_water: 0.0,
            surface_conductivity: 0.0,
            stored_assimilates: previous.map_or(0.0, |state| state.stored_assimilates),
        }),
        "KeepSWC" => {
            let previous = previous.ok_or(Error::InvalidSimulationConfiguration(
                "KeepSWC requires a preceding project run",
            ))?;
            Ok(InitialConditions {
                compartments: Some(previous.compartments),
                surface_water: previous.surface_water,
                surface_conductivity: previous.surface_conductivity,
                stored_assimilates: previous.stored_assimilates,
            })
        }
        _ => Err(Error::InvalidSimulationConfiguration(
            "initial-condition files are not implemented",
        )),
    }
}

fn ensure_daily(frequency: ClimateRecordFrequency) -> Result<(), Error> {
    if frequency == ClimateRecordFrequency::Daily {
        Ok(())
    } else {
        Err(Error::InvalidSimulationConfiguration(
            "aggregated climate expansion is not implemented",
        ))
    }
}
