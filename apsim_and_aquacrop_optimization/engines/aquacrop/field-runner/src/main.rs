//! File orchestration over the unchanged AquaCrop core, with explicit linked checkpoints.

use std::error::Error;
use std::fs::{self, File};
use std::io::{self, BufReader, BufWriter, Write};
use std::path::{Component, Path, PathBuf};

use aquacrop_core::calendar::CalendarDate;
use aquacrop_core::engine::{DailyResult, InitialConditions, SimulationCheckpoint};
use aquacrop_core::io::{load_project_run, read_project_file, read_soil_profile};
use aquacrop_core::soil::{SALT_CELL_COUNT, SoilCompartmentState, SoilProfile};
use serde::{Deserialize, Serialize};

type RunResult<T> = Result<T, Box<dyn Error>>;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Plan {
    workspace_root: PathBuf,
    field_id: String,
    aqua_root: PathBuf,
    project_file: PathBuf,
    output_dir: PathBuf,
    initial_water: Vec<f64>,
    maximum_compartment_thickness_m: f64,
    runs: Vec<RunPolicy>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RunPolicy {
    id: String,
    reset_assimilates: bool,
}

#[derive(Serialize)]
struct DailyEntry<'a> {
    segment_id: &'a str,
    result: &'a DailyResult,
}

fn invalid(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, message)
}

fn date_text(day: i32) -> RunResult<String> {
    let date = CalendarDate::from_day_number(day)?;
    Ok(format!(
        "{:04}-{:02}-{:02}",
        date.year(),
        date.month(),
        date.day()
    ))
}

fn local_path(root: &Path, relative: &Path) -> RunResult<PathBuf> {
    if relative
        .components()
        .any(|part| !matches!(part, Component::Normal(_)))
    {
        return Err(
            invalid("generated paths must be relative and cannot contain traversal").into(),
        );
    }
    Ok(root.join(relative))
}

fn write_json(path: &Path, value: &impl Serialize) -> RunResult<()> {
    let mut writer = BufWriter::new(File::create(path)?);
    serde_json::to_writer_pretty(&mut writer, value)?;
    writer.write_all(b"\n")?;
    writer.flush()?;
    Ok(())
}

fn initial_compartments(
    soil: &SoilProfile,
    water: &[f64],
    maximum_thickness: f64,
) -> RunResult<Vec<SoilCompartmentState>> {
    if soil.layers.len() != water.len()
        || !maximum_thickness.is_finite()
        || !(0.01..=0.30).contains(&maximum_thickness)
    {
        return Err(invalid("invalid initial-water vector or compartment thickness").into());
    }
    let mut compartments = Vec::new();
    for (layer_index, (layer, &theta)) in soil.layers.iter().zip(water).enumerate() {
        if !theta.is_finite() || theta < 0.0 || theta > layer.saturation / 100.0 {
            return Err(
                invalid("initial water must be between zero and source-layer saturation").into(),
            );
        }
        // Split input geometry only. All process equations remain in aquacrop-core.
        let mut remaining = layer.thickness;
        while remaining > 1e-10 {
            let thickness = remaining.min(maximum_thickness);
            compartments.push(SoilCompartmentState {
                thickness,
                theta,
                layer_index,
                adjusted_field_capacity: layer.field_capacity,
                anaerobic_exposure: 0,
                dissolved_salt: [0.0; SALT_CELL_COUNT],
                deposited_salt: [0.0; SALT_CELL_COUNT],
            });
            remaining -= thickness;
        }
    }
    Ok(compartments)
}

fn continue_from(state: SimulationCheckpoint, reset_assimilates: bool) -> InitialConditions {
    InitialConditions {
        compartments: Some(state.compartments),
        surface_water: state.surface_water,
        surface_conductivity: state.surface_conductivity,
        stored_assimilates: if reset_assimilates {
            0.0
        } else {
            state.stored_assimilates
        },
    }
}

fn execute(plan_path: &Path) -> RunResult<()> {
    let plan: Plan = serde_json::from_reader(BufReader::new(File::open(plan_path)?))?;
    let workspace = plan.workspace_root.canonicalize()?;
    let aqua_root = local_path(&workspace, &plan.aqua_root)?;
    let output = local_path(&workspace, &plan.output_dir)?;
    let project = read_project_file(local_path(&aqua_root, &plan.project_file)?)?;
    if project.runs.len() != plan.runs.len() || project.runs.is_empty() {
        return Err(invalid("project runs and continuation policies must match").into());
    }
    for pair in project.runs.windows(2) {
        if pair[0].simulation_period.last + 1 != pair[1].simulation_period.first {
            return Err(invalid(
                "simulation segments must be consecutive without gaps or overlaps",
            )
            .into());
        }
    }
    let first_run = &project.runs[0];
    let soil = read_soil_profile(
        aqua_root
            .join(&first_run.soil.directory)
            .join(&first_run.soil.filename),
    )?;
    let compartments = initial_compartments(
        &soil,
        &plan.initial_water,
        plan.maximum_compartment_thickness_m,
    )?;
    let mut initial = InitialConditions {
        compartments: Some(compartments),
        ..InitialConditions::at_field_capacity()
    };
    fs::create_dir_all(output.join("checkpoints"))?;
    let mut daily_writer = BufWriter::new(File::create(output.join("daily.jsonl"))?);
    let mut last_day = None;
    let mut total_days = 0;
    for (index, (run, policy)) in project.runs.iter().zip(&plan.runs).enumerate() {
        write_json(
            &output
                .join("checkpoints")
                .join(format!("{index:02}_initial.json")),
            &initial,
        )?;
        let mut simulation = load_project_run(&aqua_root, &plan.project_file, index, initial)?;
        let mut days = 0;
        while let Some(result) = simulation.step()? {
            let expected_day = last_day.map_or(run.simulation_period.first, |day| day + 1);
            if result.day_number != expected_day {
                return Err(
                    invalid("engine produced a gap, duplicate or shifted simulation day").into(),
                );
            }
            last_day = Some(result.day_number);
            days += 1;
            total_days += 1;
            serde_json::to_writer(
                &mut daily_writer,
                &DailyEntry {
                    segment_id: &policy.id,
                    result: &result,
                },
            )?;
            daily_writer.write_all(b"\n")?;
        }
        if days != run.simulation_period.last - run.simulation_period.first + 1 {
            return Err(invalid("engine shortened a segment; review its crop calendar").into());
        }
        let completed = simulation.finalize();
        write_json(
            &output
                .join("checkpoints")
                .join(format!("{index:02}_final.json")),
            &completed,
        )?;
        println!(
            "{} | {} | {} .. {} | {days} days",
            plan.field_id,
            policy.id,
            date_text(run.simulation_period.first)?,
            date_text(run.simulation_period.last)?
        );
        let reset = plan
            .runs
            .get(index + 1)
            .is_some_and(|next| next.reset_assimilates);
        initial = continue_from(completed.checkpoint, reset);
    }
    daily_writer.flush()?;
    write_json(
        &output.join("execution.json"),
        &serde_json::json!({
            "field_id": plan.field_id, "days": total_days, "segments": project.runs.len(),
            "core_version": aquacrop_core::AQUACROP_COMPATIBLE_VERSION,
            "state_transfer": "soil compartments, salts, surface storage, and configured assimilate carry-over",
            "grid": "extended layer-aligned; not the twelve-compartment Fortran reference grid"
        }),
    )?;
    Ok(())
}

fn main() -> RunResult<()> {
    let mut args = std::env::args_os().skip(1);
    let path = args
        .next()
        .ok_or_else(|| invalid("usage: aquacrop-field-runner PLAN.json"))?;
    if args.next().is_some() {
        return Err(invalid("expected exactly one plan path").into());
    }
    execute(Path::new(&path))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_shared_crop_profile_has_a_valid_public_calendar() -> RunResult<()> {
        let root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .ok_or_else(|| invalid("runner has no project parent"))?;
        let directory = root.join("DATA/crops/transferred");
        let mut checked = 0;
        for entry in fs::read_dir(directory)? {
            let path = entry?.path();
            if path.extension().is_some_and(|extension| extension == "CRO") {
                aquacrop_core::io::read_crop_file(path)?
                    .for_season(aquacrop_core::crop::CropSeason::default())?;
                checked += 1;
            }
        }
        assert!(checked >= 8);
        Ok(())
    }

    #[test]
    fn generated_paths_reject_parent_traversal() {
        assert!(local_path(Path::new("workspace"), Path::new("../outside")).is_err());
    }

    #[test]
    fn stand_reset_keeps_water_and_discards_assimilates() {
        let initial = continue_from(
            SimulationCheckpoint {
                compartments: Vec::new(),
                surface_water: 12.0,
                surface_conductivity: 1.2,
                stored_assimilates: 3.0,
            },
            true,
        );
        assert_eq!(
            (
                initial.surface_water,
                initial.surface_conductivity,
                initial.stored_assimilates
            ),
            (12.0, 1.2, 0.0)
        );
    }
}
