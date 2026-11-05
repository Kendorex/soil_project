use std::fs::{self, File};
use std::io::{BufWriter, Write};
use std::path::{Path, PathBuf};

use crate::Error;
use crate::calendar::CalendarDate;
use crate::engine::{CropStage, DailyResult};
use crate::output::{
    DailyReport, EvaluationPair, FieldObservation, ObservedValue, SeasonalReport, SimulationResult,
    SoilProfileGeometry, daily_reports, evaluate_simulation, fresh_yield, rounded_output_value,
    soil_profile_geometry, statistics,
};

use super::runner::ProjectSimulation;
use super::sentinel::{
    NEGATIVE_NINE_F64_SENTINEL, NEGATIVE_NINE_I32_SENTINEL, NEGATIVE_NINE_POINT_NINE_F64_SENTINEL,
    NegativeNineSentinel,
};
use super::{InputError, Project, read_crop_file, read_project_file};

#[derive(PartialEq, Eq)]
enum ProjectKind {
    Single,
    Multiple,
}

fn fixed_output(value: f64, precision: u32) -> String {
    let rounded = rounded_output_value(value, precision, f64::round_ties_even);
    format!("{rounded:.precision$}", precision = precision as usize)
}

fn fixed_output_away(value: f64, precision: u32) -> String {
    let rounded = rounded_output_value(value, precision, f64::round);
    format!("{rounded:.precision$}", precision = precision as usize)
}

fn project_kind(path: &Path) -> Result<ProjectKind, Error> {
    let extension = path.extension().and_then(|value| value.to_str()).ok_or(
        Error::InvalidSimulationConfiguration("project filename has no valid extension"),
    )?;
    if extension.eq_ignore_ascii_case("PRO") {
        Ok(ProjectKind::Single)
    } else if extension.eq_ignore_ascii_case("PRM") {
        Ok(ProjectKind::Multiple)
    } else {
        Err(Error::InvalidSimulationConfiguration(
            "project filename must end in PRO or PRM",
        ))
    }
}

const fn project_suffix(kind: &ProjectKind) -> &'static str {
    match kind {
        ProjectKind::Single => "PRO",
        ProjectKind::Multiple => "PRM",
    }
}

fn create_output(path: &Path) -> Result<BufWriter<File>, Error> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(InputError::Io)?;
    }
    let file = File::create(path).map_err(InputError::Io)?;
    Ok(BufWriter::new(file))
}

/// Writes the multiple-cutting result file for a completed parameter project.
///
/// `root` is the project-directory root, `project_file` is relative to that
/// root, and numeric values in `simulation` retain their core result units.
///
/// # Errors
///
/// Returns [`Error`] when the project or crop inputs cannot be read, the
/// supplied result count differs from the project run count, a date is
/// invalid, or the result file cannot be written.
pub fn write_project_harvest_results(
    root: impl AsRef<Path>,
    project_file: impl AsRef<Path>,
    simulation: &ProjectSimulation,
) -> Result<PathBuf, Error> {
    let root = root.as_ref();
    let project_file = project_file.as_ref();
    let project = read_project_file(root.join(project_file))?;
    if project.runs.len() != simulation.runs.len() {
        return Err(Error::InvalidSimulationConfiguration(
            "project result count differs from the project run count",
        ));
    }
    write_harvest_results(root, project_file, &project, simulation)
}

/// Writes the seasonal result file for a completed parameter project.
///
/// `root` is the project-directory root, `project_file` is relative to that
/// root, and numeric values in `simulation` retain their core result units.
///
/// # Errors
///
/// Returns [`Error`] when the project or crop inputs cannot be read, the
/// supplied result count differs from the project run count, a date is
/// invalid, or the result file cannot be written.
pub fn write_project_seasonal_results(
    root: impl AsRef<Path>,
    project_file: impl AsRef<Path>,
    simulation: &ProjectSimulation,
) -> Result<PathBuf, Error> {
    let root = root.as_ref();
    let project_file = project_file.as_ref();
    let project = read_project_file(root.join(project_file))?;
    if project.runs.len() != simulation.runs.len() {
        return Err(Error::InvalidSimulationConfiguration(
            "project result count differs from the project run count",
        ));
    }
    write_seasonal_results(root, project_file, &project, simulation)
}

/// Writes the daily result file for a completed parameter project.
///
/// `root` is the project-directory root, `project_file` is relative to that
/// root, and numeric values in `simulation` retain their core result units.
/// The current writer emits the seven standard daily sections.
///
/// # Errors
///
/// Returns [`Error`] when the project, crop, or soil inputs cannot be read,
/// the supplied result count differs from the project run count, a date is
/// invalid, or the result file cannot be written.
pub fn write_project_daily_results(
    root: impl AsRef<Path>,
    project_file: impl AsRef<Path>,
    simulation: &ProjectSimulation,
) -> Result<PathBuf, Error> {
    let root = root.as_ref();
    let project_file = project_file.as_ref();
    let project = read_project_file(root.join(project_file))?;
    if project.runs.len() != simulation.runs.len() {
        return Err(Error::InvalidSimulationConfiguration(
            "project result count differs from the project run count",
        ));
    }
    write_daily_results(root, project_file, &project, simulation)
}

/// Writes one field-observation evaluation file for every completed project run.
///
/// `root` is the project-directory root and `project_file` is relative to it.
/// Observed and simulated canopy cover use percent, biomass uses tonnes per
/// hectare, and soil-water content uses millimetres.
///
/// # Errors
///
/// Returns [`Error`] when project or observation inputs cannot be read, an
/// observation is malformed, dates are invalid, or an output cannot be written.
pub fn write_project_evaluation_results(
    root: impl AsRef<Path>,
    project_file: impl AsRef<Path>,
    simulation: &ProjectSimulation,
) -> Result<Vec<PathBuf>, Error> {
    let root = root.as_ref();
    let project_file = project_file.as_ref();
    let project = read_project_file(root.join(project_file))?;
    if project.runs.len() != simulation.runs.len() {
        return Err(Error::InvalidSimulationConfiguration(
            "project result count differs from the project run count",
        ));
    }
    let stem = project_file
        .file_stem()
        .and_then(|value| value.to_str())
        .ok_or(Error::InvalidSimulationConfiguration(
            "project filename has no valid output stem",
        ))?;
    let kind = project_kind(project_file)?;
    let suffix = project_suffix(&kind);
    let mut paths = Vec::with_capacity(project.runs.len());
    for (index, (run, result)) in project.runs.iter().zip(&simulation.runs).enumerate() {
        if run.observations.filename.starts_with('(') {
            continue;
        }
        let (sample_depth, observations) = read_field_observations(
            &root
                .join(&run.observations.directory)
                .join(&run.observations.filename),
        )?;
        let run_number = (kind == ProjectKind::Multiple).then_some(index + 1);
        let evaluation_suffix = run_number.map_or_else(
            || format!("{suffix}evaluation.OUT"),
            |number| format!("{suffix}{number}evaluation.OUT"),
        );
        let path = root.join("OUTP").join(format!("{stem}{evaluation_suffix}"));
        let mut output = create_output(&path)?;
        write_evaluation_output(&mut output, run_number, result, sample_depth, &observations)?;
        output.flush().map_err(InputError::Io)?;
        paths.push(path);
    }
    Ok(paths)
}

fn write_harvest_results(
    root: &Path,
    project_path: &Path,
    project: &Project,
    simulation: &ProjectSimulation,
) -> Result<PathBuf, Error> {
    let stem = project_path
        .file_stem()
        .and_then(|value| value.to_str())
        .ok_or(Error::InvalidSimulationConfiguration(
            "project filename has no valid output stem",
        ))?;
    let kind = project_kind(project_path)?;
    let suffix = project_suffix(&kind);
    let output_path = root
        .join("OUTP")
        .join(format!("{stem}{suffix}harvests.OUT"));
    let mut output = create_output(&output_path)?;
    write_harvest_header(&mut output)?;

    for (index, (run, result)) in project.runs.iter().zip(&simulation.runs).enumerate() {
        let crop = read_crop_file(root.join(&run.crop.directory).join(&run.crop.filename))?;
        let start = CalendarDate::from_day_number(run.crop_period.first)?;
        writeln!(output).map_err(InputError::Io)?;
        if kind == ProjectKind::Multiple {
            writeln!(output, "   Run:{:4}", index + 1).map_err(InputError::Io)?;
        }
        writeln!(
            output,
            "    Nr   Day  Month Year   DAP Interval  Biomass    Sum(B)   Dry-Yield  Sum(Y) Fresh-Yield  Sum(Y)"
        )
        .map_err(InputError::Io)?;
        writeln!(
            output,
            "                                 days     ton/ha    ton/ha    ton/ha    ton/ha    ton/ha    ton/ha"
        )
        .map_err(InputError::Io)?;
        writeln!(
            output,
            "{:6}{:6}{:6}{:6}{:34.3}{:20.3}{:20.3}",
            0,
            start.day(),
            start.month(),
            start.year(),
            0.0,
            0.0,
            0.0
        )
        .map_err(InputError::Io)?;

        for daily in &result.daily {
            let Some(cutting) = &daily.cutting else {
                continue;
            };
            let date = CalendarDate::from_day_number(daily.day_number)?;
            write!(
                output,
                "{:6}{:6}{:6}{:6}{:6}{:6}{:12.3}{:10.3}{:10.3}",
                cutting.number,
                date.day(),
                date.month(),
                date.year(),
                daily
                    .day_after_planting
                    .unwrap_or(NEGATIVE_NINE_I32_SENTINEL),
                cutting.interval,
                cutting.biomass,
                cutting.cumulative_biomass,
                cutting.dry_yield,
            )
            .map_err(InputError::Io)?;
            write_yield_columns(
                &mut output,
                cutting.cumulative_dry_yield,
                cutting.dry_yield,
                crop.parameters.development.dry_matter,
            )?;
        }

        let end = CalendarDate::from_day_number(run.simulation_period.last + 1)?;
        let final_result = &result.final_result;
        write!(
            output,
            "{:6}{:6}{:6}{:6}{:34.3}",
            9999,
            end.day(),
            end.month(),
            end.year(),
            final_result.biomass
        )
        .map_err(InputError::Io)?;
        if crop.parameters.development.dry_matter.is_none() {
            writeln!(output, "{:20.3}", final_result.dry_yield).map_err(InputError::Io)?;
        } else if let Some(dry_matter) = crop.parameters.development.dry_matter {
            writeln!(
                output,
                "{:20.3}{:>20}",
                final_result.dry_yield,
                fixed_output(fresh_yield(final_result.dry_yield, dry_matter), 3)
            )
            .map_err(InputError::Io)?;
        }
    }

    output.flush().map_err(InputError::Io)?;
    Ok(output_path)
}

fn write_harvest_header(output: &mut impl Write) -> Result<(), Error> {
    writeln!(
        output,
        "AquaCrop {} - Output created by aquacrop-rs\nBiomass and Yield at Multiple cuttings",
        crate::AQUACROP_COMPATIBLE_VERSION
    )
    .map_err(InputError::Io)?;
    Ok(())
}

fn write_daily_results(
    root: &Path,
    project_path: &Path,
    project: &Project,
    simulation: &ProjectSimulation,
) -> Result<PathBuf, Error> {
    let stem = project_path
        .file_stem()
        .and_then(|value| value.to_str())
        .ok_or(Error::InvalidSimulationConfiguration(
            "project filename has no valid output stem",
        ))?;
    let kind = project_kind(project_path)?;
    let suffix = project_suffix(&kind);
    let output_path = root.join("OUTP").join(format!("{stem}{suffix}day.OUT"));
    let mut output = create_output(&output_path)?;
    writeln!(
        output,
        "AquaCrop {} - Output created by aquacrop-rs",
        crate::AQUACROP_COMPATIBLE_VERSION
    )
    .map_err(InputError::Io)?;

    for (index, (run, result)) in project.runs.iter().zip(&simulation.runs).enumerate() {
        let crop_file = root.join(&run.crop.directory).join(&run.crop.filename);
        let soil_file = root.join(&run.soil.directory).join(&run.soil.filename);
        let crop = read_crop_file(crop_file)?;
        let soil = super::read_soil_profile(soil_file)?;
        let profile = soil_profile_geometry(&crop, soil)?;
        let values = daily_reports(&result.daily, crop.parameters.development.dry_matter);
        write_daily_titles(
            &mut output,
            (kind == ProjectKind::Multiple).then_some(index + 1),
            &profile,
        )?;
        for (daily, values) in result.daily.iter().zip(&values) {
            write_daily_result(&mut output, daily, values)?;
        }
    }

    output.flush().map_err(InputError::Io)?;
    Ok(output_path)
}

fn write_daily_titles(
    output: &mut impl Write,
    run_number: Option<usize>,
    profile: &SoilProfileGeometry,
) -> Result<(), Error> {
    let profile_depth = profile.profile_depth;
    let maximum_rooting_depth = profile.maximum_rooting_depth;
    writeln!(output).map_err(InputError::Io)?;
    if let Some(run_number) = run_number {
        writeln!(output, "   Run:{run_number:4}").map_err(InputError::Io)?;
    }
    write!(
        output,
        "   Day Month  Year   DAP Stage   WC({profile_depth:4.2})   Rain     Irri   Surf   Infilt   RO    Drain       CR    Zgwt       Ex       E     E/Ex     Trx       Tr  Tr/Trx    ETx      ET  ET/ETx      GD       Z     StExp  StSto  StSen StSalt StWeed   CC      CCw     StTr  Kc(Tr)     Trx       Tr      TrW  Tr/Trx   WP    Biomass     HI    Y(dry)  Y(fresh)  Brelative    WPet      Bin     Bout  WC({profile_depth:4.2}) Wr({maximum_rooting_depth:4.2})     Z       Wr    Wr(SAT)    Wr(FC)   Wr(exp)   Wr(sto)   Wr(sen)   Wr(PWP)    SaltIn    SaltOut   SaltUp   Salt({profile_depth:4.2})  SaltZ     Z       ECe    ECsw   StSalt  Zgwt    ECgw"
    )
    .map_err(InputError::Io)?;
    for index in 1..=profile.compartments.len() {
        if index == 1 {
            output.write_all(b"       WC01").map_err(InputError::Io)?;
        } else {
            write!(output, "       WC{index:>2}").map_err(InputError::Io)?;
        }
    }
    for index in 1..=profile.compartments.len() {
        if index == 1 {
            output.write_all(b"      ECe01").map_err(InputError::Io)?;
        } else {
            write!(output, "      ECe{index:>2}").map_err(InputError::Io)?;
        }
    }
    writeln!(
        output,
        "     Rain       ETo      Tmin      Tavg      Tmax      CO2"
    )
    .map_err(InputError::Io)?;

    output
        .write_all(b"                                      mm      mm       mm     mm     mm     mm       mm       mm      m        mm       mm     %        mm       mm    %        mm      mm       %  degC-day     m       %      %      %      %      %      %       %       %       -        mm       mm       mm    %     g/m2    ton/ha      %    ton/ha   ton/ha       %       kg/m3   ton/ha   ton/ha      mm       mm       m       mm        mm        mm        mm        mm        mm         mm    ton/ha    ton/ha    ton/ha    ton/ha    ton/ha     m      dS/m    dS/m      %     m      dS/m")
        .map_err(InputError::Io)?;
    for _ in 0..2 {
        for node in &profile.compartment_nodes {
            write!(output, "{:>11}", fixed_output_away(*node, 2)).map_err(InputError::Io)?;
        }
    }
    writeln!(
        output,
        "       mm        mm     degC      degC      degC       ppm"
    )
    .map_err(InputError::Io)?;
    Ok(())
}

#[expect(
    clippy::too_many_lines,
    reason = "the daily row preserves the reference output field order"
)]
fn write_daily_result(
    output: &mut impl Write,
    daily: &DailyResult,
    values: &DailyReport,
) -> Result<(), Error> {
    let date = CalendarDate::from_day_number(daily.day_number)?;
    let stage = stage_code(&daily.stage);
    let day_after_planting = (stage != 0)
        .then_some(daily.day_after_planting)
        .flatten()
        .unwrap_or(NEGATIVE_NINE_I32_SENTINEL);
    write!(
        output,
        "{:6}{:6}{:6}{:6}{:6}",
        date.day(),
        date.month(),
        date.year(),
        day_after_planting,
        stage
    )
    .map_err(InputError::Io)?;
    let groundwater_depth = daily
        .groundwater
        .as_ref()
        .map_or(NEGATIVE_NINE_POINT_NINE_F64_SENTINEL, |value| value.depth);
    write!(
        output,
        "{:10.1}{:8.1}{:9.1}{:7.1}{:7.1}{:7.1}{:9.1}{:9.1}{:8.2}",
        daily.profile_water,
        daily.rain,
        daily.irrigation,
        daily.surface_water,
        daily.infiltration,
        daily.runoff,
        daily.drainage,
        daily.capillary_rise,
        groundwater_depth,
    )
    .map_err(InputError::Io)?;
    write!(
        output,
        "{:9.1}{:9.1}{:7}{:9.1}{:9.1}{:6}{:9.1}{:8.1}{:8}",
        daily.potential_evaporation,
        daily.evaporation,
        values.evaporation_ratio,
        daily.potential_transpiration,
        daily.transpiration,
        values.transpiration_ratio,
        values.potential_evapotranspiration,
        values.evapotranspiration,
        values.evapotranspiration_ratio,
    )
    .map_err(InputError::Io)?;

    let harvest_index = daily
        .harvest_index
        .unwrap_or(NEGATIVE_NINE_POINT_NINE_F64_SENTINEL);
    let fresh = values.fresh_yield.map_or_else(
        || fixed_output(NEGATIVE_NINE_POINT_NINE_F64_SENTINEL, 3),
        |fresh_yield| fixed_output(fresh_yield, 3),
    );
    write!(
        output,
        "{:9.1}{:8.2}{:7}{:7}{:7}{:7}{:7}{:8.1}{:8.1}{:7}{:9.2}{:9.1}{:9.1}{:9.1}{:6}{:8.1}{:10.3}{:8.1}{:9.3}{:>9}{:8}{:12.2}{:9.3}{:9.3}",
        daily.growing_degree_days,
        daily.rooting_depth,
        values.leaf_stress.unwrap_or(NEGATIVE_NINE_I32_SENTINEL),
        values.stomatal_stress.unwrap_or(NEGATIVE_NINE_I32_SENTINEL),
        values.senescence_stress.unwrap_or(NEGATIVE_NINE_I32_SENTINEL),
        values.salinity_stress.unwrap_or(NEGATIVE_NINE_I32_SENTINEL),
        values.weed_stress.unwrap_or(NEGATIVE_NINE_I32_SENTINEL),
        values.crop_canopy_percent,
        values.canopy_percent,
        values.temperature_stress,
        values
            .crop_coefficient
            .unwrap_or(f64::from(NEGATIVE_NINE_I32_SENTINEL)),
        daily.potential_transpiration,
        daily.transpiration,
        daily.crop_transpiration,
        values.transpiration_ratio,
        daily.water_productivity,
        daily.biomass,
        harvest_index,
        daily.dry_yield,
        fresh,
        values.relative_biomass.unwrap_or(NEGATIVE_NINE_I32_SENTINEL),
        values.yield_water_productivity,
        daily.mobilized_assimilates,
        daily.stored_assimilates,
    )
    .map_err(InputError::Io)?;

    let maximum = daily.maximum_root_zone_water.as_ref();
    let root = daily.root_zone_water.as_ref();
    write!(
        output,
        "{:10.1}{:9.1}{:8.2}{:8.1}{:10.1}{:10.1}{:10.1}{:10.1}{:10.1}{:10.1}",
        daily.profile_water,
        maximum.map_or(NEGATIVE_NINE_POINT_NINE_F64_SENTINEL, |value| value.actual),
        daily.rooting_depth,
        root.map_or(NEGATIVE_NINE_POINT_NINE_F64_SENTINEL, |value| value.actual),
        root.map_or(NEGATIVE_NINE_POINT_NINE_F64_SENTINEL, |value| value
            .saturation),
        root.map_or(NEGATIVE_NINE_POINT_NINE_F64_SENTINEL, |value| value
            .field_capacity),
        root.map_or(NEGATIVE_NINE_POINT_NINE_F64_SENTINEL, |value| value
            .leaf_threshold),
        root.map_or(NEGATIVE_NINE_POINT_NINE_F64_SENTINEL, |value| value
            .stomatal_threshold),
        root.map_or(NEGATIVE_NINE_POINT_NINE_F64_SENTINEL, |value| value
            .senescence_threshold),
        root.map_or(NEGATIVE_NINE_POINT_NINE_F64_SENTINEL, |value| value
            .wilting_point),
    )
    .map_err(InputError::Io)?;

    let root_salinity = daily.root_zone_salinity.as_ref();
    let groundwater_conductivity = daily
        .groundwater
        .as_ref()
        .map_or(NEGATIVE_NINE_F64_SENTINEL, |value| value.conductivity);
    write!(
        output,
        "{:9.3}{:10.3}{:10.3}{:10.3}{:10.3}{:8.2}{:9.2}{:8.2}{:7}{:8.2}{:8.2}",
        values.infiltrated_salt,
        values.drained_salt,
        values.capillary_salt,
        values.profile_salt,
        values
            .root_salt
            .unwrap_or(f64::from(NEGATIVE_NINE_I32_SENTINEL)),
        daily.rooting_depth,
        root_salinity.map_or(NEGATIVE_NINE_F64_SENTINEL, |value| {
            value.extract_conductivity
        }),
        root_salinity.map_or(NEGATIVE_NINE_F64_SENTINEL, |value| {
            value.water_conductivity
        }),
        values.root_salinity_stress,
        groundwater_depth,
        groundwater_conductivity,
    )
    .map_err(InputError::Io)?;
    for water_percent in &values.compartment_water_percent {
        write!(output, "{water_percent:11.1}").map_err(InputError::Io)?;
    }
    for compartment in &daily.compartments {
        write!(output, "{:11.1}", compartment.extract_conductivity).map_err(InputError::Io)?;
    }
    writeln!(
        output,
        "{:9.1}{:10.1}{:10.1}{:10.1}{:10.1}{:10.2}",
        daily.rain,
        daily.reference_et,
        daily.minimum_temperature,
        values.average_temperature,
        daily.maximum_temperature,
        daily.carbon_dioxide,
    )
    .map_err(InputError::Io)?;
    Ok(())
}

const fn stage_code(stage: &CropStage) -> i32 {
    // Outside-cycle and no-growth stages use distinct output codes.
    match stage {
        CropStage::OutsideCropCycle => 0,
        CropStage::NoGrowth => NEGATIVE_NINE_I32_SENTINEL,
        CropStage::Establishment => 1,
        CropStage::Vegetative => 2,
        CropStage::Flowering => 3,
        CropStage::YieldFormation => 4,
    }
}

#[expect(
    clippy::too_many_lines,
    reason = "the seasonal row preserves the reference output field order"
)]
fn write_seasonal_results(
    root: &Path,
    project_path: &Path,
    project: &Project,
    simulation: &ProjectSimulation,
) -> Result<PathBuf, Error> {
    let stem = project_path
        .file_stem()
        .and_then(|value| value.to_str())
        .ok_or(Error::InvalidSimulationConfiguration(
            "project filename has no valid output stem",
        ))?;
    let kind = project_kind(project_path)?;
    let suffix = project_suffix(&kind);
    let project_name = project_path
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or(Error::InvalidSimulationConfiguration(
            "project filename is not valid text",
        ))?;
    let output_path = root.join("OUTP").join(format!("{stem}{suffix}season.OUT"));
    let mut output = create_output(&output_path)?;
    writeln!(
        output,
        "AquaCrop {} - Output created by aquacrop-rs\n",
        crate::AQUACROP_COMPATIBLE_VERSION
    )
    .map_err(InputError::Io)?;
    writeln!(
        output,
        "    RunNr     Day1   Month1    Year1     Rain      ETo       GD     CO2      Irri   Infilt   Runoff    Drain   Upflow        E     E/Ex       Tr      TrW   Tr/Trx    SaltIn   SaltOut    SaltUp  SaltProf     Cycle   SaltStr  FertStr  WeedStr  TempStr   ExpStr   StoStr  BioMass  Brelative   HI    Y(dry)  Y(fresh)    WPet      Bin     Bout     DayN   MonthN    YearN"
    )
    .map_err(InputError::Io)?;
    writeln!(
        output,
        "                                           mm       mm  degC.day    ppm        mm       mm       mm       mm       mm       mm        %       mm       mm        %    ton/ha    ton/ha    ton/ha    ton/ha      days       %        %        %        %        %        %    ton/ha        %       %    ton/ha   ton/ha    kg/m3   ton/ha   ton/ha"
    )
    .map_err(InputError::Io)?;

    for (index, (run, result)) in project.runs.iter().zip(&simulation.runs).enumerate() {
        let crop = read_crop_file(root.join(&run.crop.directory).join(&run.crop.filename))?;
        let management = super::read_management_file(
            root.join(&run.management.directory)
                .join(&run.management.filename),
        )?;
        let start = CalendarDate::from_day_number(run.simulation_period.first)?;
        let end = CalendarDate::from_day_number(run.simulation_period.last)?;
        let values = SeasonalReport::from_simulation_inputs(
            result,
            crop.parameters.development.dry_matter,
            &management,
        );

        write!(
            output,
            "{:>9}{:9}{:9}{:9}{:9.1}{:9.1}{:9.1}{:9.2}",
            format!("Tot({})", index + 1),
            start.day(),
            start.month(),
            start.year(),
            values.rain,
            values.reference_et,
            values.growing_degree_days,
            values.carbon_dioxide,
        )
        .map_err(InputError::Io)?;
        write!(
            output,
            "{:9.1}{:9.1}{:9.1}{:9.1}{:9.1}{:9.1}{:9}{:9.1}{:9.1}{:9}",
            values.irrigation,
            values.infiltration,
            values.runoff,
            values.drainage,
            values.capillary_rise,
            values.evaporation,
            values
                .evaporation_ratio
                .unwrap_or(NEGATIVE_NINE_I32_SENTINEL),
            values.transpiration,
            values.crop_transpiration,
            values
                .transpiration_ratio
                .unwrap_or(NEGATIVE_NINE_I32_SENTINEL),
        )
        .map_err(InputError::Io)?;
        write!(
            output,
            "{:10.3}{:10.3}{:10.3}{:10.3}",
            values.infiltrated_salt,
            values.drained_salt,
            values.capillary_salt,
            values.profile_salt,
        )
        .map_err(InputError::Io)?;
        write!(
            output,
            "{:9}{:9}{:9}{:9}{:9}{:9}{:9}",
            values.stress_days,
            values.salinity_stress,
            values.fertility_stress,
            values.weed_stress,
            values.temperature_stress,
            values.leaf_expansion_stress,
            values.stomatal_stress,
        )
        .map_err(InputError::Io)?;
        write!(
            output,
            "{:10.3}{:9}{:9.1}{:9.3}{:9.3}{:9.2}{:9.3}{:9.3}",
            values.biomass,
            values
                .relative_biomass
                .unwrap_or(NEGATIVE_NINE_I32_SENTINEL),
            values
                .harvest_index
                .unwrap_or(NEGATIVE_NINE_POINT_NINE_F64_SENTINEL),
            values.dry_yield,
            values
                .fresh_yield
                .unwrap_or(NEGATIVE_NINE_POINT_NINE_F64_SENTINEL),
            values.water_productivity,
            values.mobilized_assimilates,
            values.stored_assimilates,
        )
        .map_err(InputError::Io)?;
        writeln!(
            output,
            "{:9}{:9}{:9}  {project_name}",
            end.day(),
            end.month(),
            end.year()
        )
        .map_err(InputError::Io)?;
    }

    output.flush().map_err(InputError::Io)?;
    Ok(output_path)
}

fn write_yield_columns(
    output: &mut impl Write,
    cumulative: f64,
    interval: f64,
    dry_matter: Option<i8>,
) -> Result<(), Error> {
    if let Some(dry_matter) = dry_matter {
        writeln!(
            output,
            "{:10.3}{:>10}{:>10}",
            cumulative,
            fixed_output(fresh_yield(interval, dry_matter), 3),
            fixed_output(fresh_yield(cumulative, dry_matter), 3)
        )
        .map_err(InputError::Io)?;
    } else {
        writeln!(output, "{cumulative:10.3}").map_err(InputError::Io)?;
    }
    Ok(())
}

fn read_field_observations(path: &Path) -> Result<(f64, Vec<FieldObservation>), Error> {
    let input = fs::read_to_string(path).map_err(InputError::from)?;
    let lines: Vec<_> = input.lines().collect();
    let first_token = |line: usize| {
        lines
            .get(line)
            .and_then(|value| value.split_whitespace().next())
            .ok_or(Error::InvalidSimulationConfiguration(
                "field-observation header is incomplete",
            ))
    };
    let header_value = |line: usize| -> Result<f64, Error> {
        first_token(line)?.parse().map_err(|_| {
            Error::InvalidSimulationConfiguration("field-observation header is invalid")
        })
    };
    let sample_depth = header_value(2)?;
    let first = CalendarDate::new(
        first_token(5)?.parse().map_err(|_| {
            Error::InvalidSimulationConfiguration("field-observation year is invalid")
        })?,
        first_token(4)?.parse().map_err(|_| {
            Error::InvalidSimulationConfiguration("field-observation month is invalid")
        })?,
        first_token(3)?.parse().map_err(|_| {
            Error::InvalidSimulationConfiguration("field-observation day is invalid")
        })?,
    )?;
    let separator = lines
        .iter()
        .position(|line| line.starts_with("===="))
        .ok_or(Error::InvalidSimulationConfiguration(
            "field-observation data separator is missing",
        ))?;
    let mut observations = Vec::new();
    for line in &lines[separator + 1..] {
        if line.trim().is_empty() {
            continue;
        }
        let values: Vec<_> = line.split_whitespace().collect();
        if values.len() < 7 {
            return Err(Error::InvalidSimulationConfiguration(
                "field-observation row is incomplete",
            ));
        }
        let value = |index: usize| -> Result<f64, Error> {
            values[index].parse().map_err(|_| {
                Error::InvalidSimulationConfiguration("field-observation value is invalid")
            })
        };
        let observation =
            |value_index: usize, deviation_index: usize| -> Result<Option<ObservedValue>, Error> {
                let Some(observed) = value(value_index)?.without_negative_nine() else {
                    return Ok(None);
                };
                Ok(Some(ObservedValue {
                    value: observed,
                    deviation: value(deviation_index)?,
                }))
            };
        let offset: i32 = values[0].parse().map_err(|_| {
            Error::InvalidSimulationConfiguration("field-observation day offset is invalid")
        })?;
        observations.push(FieldObservation {
            day_number: first.day_number() + offset - 1,
            canopy: observation(1, 2)?,
            biomass: observation(3, 4)?,
            soil_water: observation(5, 6)?,
        });
    }
    Ok((sample_depth, observations))
}

fn write_evaluation_output(
    output: &mut impl Write,
    run_number: Option<usize>,
    result: &SimulationResult,
    sample_depth: f64,
    observations: &[FieldObservation],
) -> Result<(), Error> {
    let evaluation = evaluate_simulation(result, sample_depth, observations)?;
    writeln!(
        output,
        "AquaCrop {} - Output created by aquacrop-rs\nEvaluation of simulation results - Statistics",
        crate::AQUACROP_COMPATIBLE_VERSION
    )
    .map_err(InputError::Io)?;
    if let Some(run_number) = run_number {
        writeln!(output, "** Run number:{run_number}").map_err(InputError::Io)?;
    }
    output.write_all(b"\n\n").map_err(InputError::Io)?;
    write_evaluation_section(output, &EvaluationKind::Canopy, &evaluation.canopy)?;
    writeln!(output).map_err(InputError::Io)?;
    writeln!(output).map_err(InputError::Io)?;
    write_evaluation_section(output, &EvaluationKind::Biomass, &evaluation.biomass)?;
    writeln!(output).map_err(InputError::Io)?;
    writeln!(output).map_err(InputError::Io)?;
    write_evaluation_section(output, &EvaluationKind::SoilWater, &evaluation.soil_water)
}

#[derive(PartialEq, Eq)]
enum EvaluationKind {
    Canopy,
    Biomass,
    SoilWater,
}

#[expect(
    clippy::too_many_lines,
    reason = "the reference report layout is kept in source order"
)]
fn write_evaluation_section(
    output: &mut impl Write,
    kind: &EvaluationKind,
    pairs: &[EvaluationPair],
) -> Result<(), Error> {
    let (heading, units, observed_label, simulated_label) = match kind {
        EvaluationKind::Canopy => (
            "CANOPY COVER --------------------------------------",
            "--------- Canopy Cover (%) ---------",
            "Average of observed Canopy Cover ..............",
            "Average of simulated Canopy Cover .............",
        ),
        EvaluationKind::Biomass => (
            "BIOMASS PRODUCTION --------------------------------",
            "--------- Biomass (ton/ha) ---------",
            "Average of observed Biomass production ........",
            "Average of simulated Biomass production .......",
        ),
        EvaluationKind::SoilWater => (
            "SOIL WATER CONTENT --------------------------------",
            "------ Soil water content (mm) -----",
            "Average of observed Soil Water Content ........",
            "Average of simulated Soil Water Content .......",
        ),
    };
    writeln!(output, "  ASSESSMENT OF {heading}").map_err(InputError::Io)?;
    if pairs.len() <= 1 {
        writeln!(output, "  No statistic analysis (insufficient data)").map_err(InputError::Io)?;
        writeln!(
            output,
            "  ----------------------------------------------------------------"
        )
        .map_err(InputError::Io)?;
        return Ok(());
    }
    writeln!(output, "              {units}\n    Nr        Observed    +/- St Dev     Simulated    Date\n  ----------------------------------------------------------------").map_err(InputError::Io)?;
    for (index, pair) in pairs.iter().enumerate() {
        let month = month_name(pair.date.month());
        if *kind == EvaluationKind::Biomass {
            writeln!(
                output,
                "{:6}{:16.3}{:14.3}{:14.3}      {:2} {month} {}",
                index + 1,
                pair.observed,
                pair.deviation,
                pair.simulated,
                pair.date.day(),
                pair.date.year()
            )
            .map_err(InputError::Io)?;
        } else {
            writeln!(
                output,
                "{:6}{:14.1}{:14.1}{:14.1}      {:2} {month} {}",
                index + 1,
                pair.observed,
                pair.deviation,
                pair.simulated,
                pair.date.day(),
                pair.date.year()
            )
            .map_err(InputError::Io)?;
        }
    }
    let statistics = statistics(pairs);
    writeln!(
        output,
        "\n  Valid observations/simulations sets (n) ....... : {:5}",
        pairs.len()
    )
    .map_err(InputError::Io)?;
    if *kind == EvaluationKind::Biomass {
        writeln!(
            output,
            "  {observed_label} : {:9.3}   ton/ha",
            statistics.observed_average
        )
        .map_err(InputError::Io)?;
        writeln!(
            output,
            "  {simulated_label} : {:9.3}   ton/ha",
            statistics.simulated_average
        )
        .map_err(InputError::Io)?;
    } else {
        let suffix = if *kind == EvaluationKind::Canopy {
            "   %"
        } else {
            "   mm"
        };
        writeln!(
            output,
            "  {observed_label} : {:7.1}{suffix}",
            statistics.observed_average
        )
        .map_err(InputError::Io)?;
        writeln!(
            output,
            "  {simulated_label} : {:7.1}{suffix}",
            statistics.simulated_average
        )
        .map_err(InputError::Io)?;
    }
    writeln!(
        output,
        "\n  Pearson Correlation Coefficient (r) ........... : {:8.2}",
        statistics.correlation
    )
    .map_err(InputError::Io)?;
    let rmse_suffix = match kind {
        EvaluationKind::Canopy => "   % CC",
        EvaluationKind::Biomass => "   ton/ha",
        EvaluationKind::SoilWater => "   mm",
    };
    if *kind == EvaluationKind::Biomass {
        writeln!(
            output,
            "  Root mean square error (RMSE) ................. : {:9.3}{rmse_suffix}",
            statistics.root_mean_square_error
        )
        .map_err(InputError::Io)?;
    } else {
        writeln!(
            output,
            "  Root mean square error (RMSE) ................. : {:7.1}{rmse_suffix}",
            statistics.root_mean_square_error
        )
        .map_err(InputError::Io)?;
    }
    writeln!(
        output,
        "  Normalized root mean square error  CV(RMSE).... : {:7.1}   %",
        statistics.normalized_root_mean_square_error
    )
    .map_err(InputError::Io)?;
    writeln!(
        output,
        "  Nash-Sutcliffe model efficiency coefficient (EF): {:8.2}",
        statistics.efficiency
    )
    .map_err(InputError::Io)?;
    writeln!(
        output,
        "  Willmotts index of agreement (d) .............. : {:8.2}",
        statistics.agreement
    )
    .map_err(InputError::Io)?;
    writeln!(
        output,
        "  ----------------------------------------------------------------"
    )
    .map_err(InputError::Io)?;
    Ok(())
}

const fn month_name(month: i32) -> &'static str {
    match month {
        1 => "January",
        2 => "February",
        3 => "March",
        4 => "April",
        5 => "May",
        6 => "June",
        7 => "July",
        8 => "August",
        9 => "September",
        10 => "October",
        11 => "November",
        12 => "December",
        _ => "",
    }
}
