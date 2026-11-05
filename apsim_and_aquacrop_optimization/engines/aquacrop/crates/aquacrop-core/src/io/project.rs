use std::fs;
use std::path::Path;
use std::str::FromStr;

use crate::{AquaCropVersion, Error};

use super::InputError;
use super::parse::{first_token, parse_token, required_line};

const PROJECT_HEADER_LINES: usize = 2;
const PROJECT_RUN_LINES: usize = 47;

/// Inclusive range of AquaCrop day numbers.
#[derive(Debug, PartialEq)]
pub struct DayNumberRange {
    /// First day number in the range.
    pub first: i32,
    /// Last day number in the range.
    pub last: i32,
}

/// One three-line file reference from an AquaCrop project run.
#[derive(Debug)]
pub struct ProjectFileReference {
    /// Descriptive section line, including any leading spacing from the source file.
    pub info: String,
    /// Filename or special value such as `(None)` or `KeepSWC`.
    pub filename: String,
    /// Directory or the associated special-value description.
    pub directory: String,
}

/// Input references and date ranges for one run in an AquaCrop project.
#[derive(Debug)]
pub struct ProjectRun {
    /// Cultivation year, where 1 is the seeding or planting year.
    pub cultivation_year: i8,
    /// Simulation period.
    pub simulation_period: DayNumberRange,
    /// Cropping period, including a possibly premature last day.
    pub crop_period: DayNumberRange,
    /// Climate definition file.
    pub climate: ProjectFileReference,
    /// Minimum/maximum temperature file.
    pub temperature: ProjectFileReference,
    /// Reference evapotranspiration file.
    pub reference_et: ProjectFileReference,
    /// Rainfall file.
    pub rain: ProjectFileReference,
    /// Atmospheric carbon-dioxide file.
    pub co2: ProjectFileReference,
    /// Crop calendar file.
    pub calendar: ProjectFileReference,
    /// Crop parameter file.
    pub crop: ProjectFileReference,
    /// Irrigation management file.
    pub irrigation: ProjectFileReference,
    /// Field management file.
    pub management: ProjectFileReference,
    /// Soil profile file.
    pub soil: ProjectFileReference,
    /// Groundwater table file.
    pub groundwater: ProjectFileReference,
    /// Initial soil-water-content file or carry-over directive.
    pub initial_conditions: ProjectFileReference,
    /// Off-season conditions file.
    pub off_season: ProjectFileReference,
    /// Field observations file.
    pub observations: ProjectFileReference,
}

/// An AquaCrop `.PRM` or `.PRO` project file.
#[derive(Debug)]
pub struct Project {
    /// Project description.
    pub description: String,
    /// Version declared inside the project file.
    pub version: AquaCropVersion,
    /// Simulation runs in file order.
    pub runs: Vec<ProjectRun>,
}

/// Reads and parses an AquaCrop `.PRM` or `.PRO` project file.
///
/// # Errors
///
/// Returns [`Error`] when the file cannot be read or its fixed run layout is invalid.
pub fn read_project_file(path: impl AsRef<Path>) -> Result<Project, Error> {
    parse_project_file(&fs::read_to_string(path).map_err(InputError::from)?)
}

/// Parses an AquaCrop `.PRM` or `.PRO` project file.
///
/// # Errors
///
/// Returns [`Error`] when the common header or a 47-line run block is invalid.
pub fn parse_project_file(input: &str) -> Result<Project, Error> {
    let lines: Vec<_> = input.lines().collect();
    let description = required_line(&lines, 1, "project description")?
        .trim_end()
        .to_owned();
    let version_token = first_token(
        required_line(&lines, 2, "AquaCrop version")?,
        2,
        "AquaCrop version",
    )?;
    let version =
        AquaCropVersion::from_str(version_token).map_err(|_| InputError::InvalidValue {
            line: 2,
            field: "AquaCrop version",
            value: version_token.to_owned(),
        })?;

    let run_lines = lines.get(PROJECT_HEADER_LINES..).unwrap_or_default();
    let mut chunks = run_lines.chunks_exact(PROJECT_RUN_LINES);
    let mut runs = Vec::with_capacity(chunks.len());
    for (run_index, chunk) in chunks.by_ref().enumerate() {
        let first_line = PROJECT_HEADER_LINES + run_index * PROJECT_RUN_LINES + 1;
        runs.push(parse_run(chunk, first_line)?);
    }
    let remainder = chunks.remainder();
    if !remainder.is_empty() || runs.is_empty() {
        return Err(InputError::IncompleteSection {
            section: "project run",
            complete_items: runs.len(),
            expected_lines: PROJECT_RUN_LINES,
            actual_lines: remainder.len(),
        }
        .into());
    }

    Ok(Project {
        description,
        version,
        runs,
    })
}

fn parse_run(lines: &[&str], first_line: usize) -> Result<ProjectRun, Error> {
    let mut parser = RunParser::new(lines, first_line);
    let cultivation_year = parser.value("cultivation year")?;
    let simulation_period = DayNumberRange {
        first: parser.value("first simulation day")?,
        last: parser.value("last simulation day")?,
    };
    let crop_period = DayNumberRange {
        first: parser.value("first crop day")?,
        last: parser.value("last crop day")?,
    };

    Ok(ProjectRun {
        cultivation_year,
        simulation_period,
        crop_period,
        climate: parser.file_reference("climate")?,
        temperature: parser.file_reference("temperature")?,
        reference_et: parser.file_reference("reference ET")?,
        rain: parser.file_reference("rain")?,
        co2: parser.file_reference("CO2")?,
        calendar: parser.file_reference("calendar")?,
        crop: parser.file_reference("crop")?,
        irrigation: parser.file_reference("irrigation")?,
        management: parser.file_reference("field management")?,
        soil: parser.file_reference("soil")?,
        groundwater: parser.file_reference("groundwater")?,
        initial_conditions: parser.file_reference("initial conditions")?,
        off_season: parser.file_reference("off-season conditions")?,
        observations: parser.file_reference("observations")?,
    })
}

struct RunParser<'a> {
    lines: &'a [&'a str],
    first_line: usize,
    position: usize,
}

impl<'a> RunParser<'a> {
    fn new(lines: &'a [&'a str], first_line: usize) -> Self {
        Self {
            lines,
            first_line,
            position: 0,
        }
    }

    fn value<T>(&mut self, field: &'static str) -> Result<T, Error>
    where
        T: FromStr,
    {
        let line_number = self.line_number();
        let value = parse_token(self.lines[self.position], line_number, field)?;
        self.position += 1;
        Ok(value)
    }

    fn file_reference(&mut self, section: &'static str) -> Result<ProjectFileReference, Error> {
        let info = self.text_line(section)?.trim_end().to_owned();
        let filename = self.list_directed_text("project filename")?;
        let directory = self.list_directed_text("project directory")?;
        Ok(ProjectFileReference {
            info,
            filename,
            directory,
        })
    }

    fn list_directed_text(&mut self, field: &'static str) -> Result<String, Error> {
        let line_number = self.line_number();
        let line = self.text_line(field)?;
        parse_list_directed_character(line, line_number, field)
    }

    fn text_line(&mut self, field: &'static str) -> Result<&'a str, Error> {
        let line_number = self.line_number();
        let line = self
            .lines
            .get(self.position)
            .copied()
            .ok_or(InputError::MissingLine {
                line: line_number,
                field,
            })?;
        self.position += 1;
        Ok(line)
    }

    fn line_number(&self) -> usize {
        self.first_line + self.position
    }
}

fn parse_list_directed_character(
    line: &str,
    line_number: usize,
    field: &'static str,
) -> Result<String, Error> {
    let value = line.trim_start();
    let Some(first_character) = value.chars().next() else {
        return Err(InputError::MissingLine {
            line: line_number,
            field,
        }
        .into());
    };
    if matches!(first_character, '\'' | '"') {
        let quoted = &value[first_character.len_utf8()..];
        let Some(end) = quoted.find(first_character) else {
            return Err(InputError::InvalidValue {
                line: line_number,
                field,
                value: value.to_owned(),
            }
            .into());
        };
        Ok(quoted[..end].to_owned())
    } else {
        Ok(first_token(value, line_number, field)?.to_owned())
    }
}

#[cfg(test)]
mod tests {
    use std::assert_matches;

    use super::*;

    #[test]
    fn list_directed_character_removes_fortran_quotes() {
        let value = parse_list_directed_character("   './DATA/'", 1, "directory")
            .expect("the quoted list-directed value should parse");
        assert_eq!(value, "./DATA/");
    }

    #[test]
    fn list_directed_character_stops_unquoted_value_at_space() {
        let value = parse_list_directed_character(
            "Keep soil water profile of previous run",
            1,
            "directory",
        )
        .expect("the unquoted list-directed value should parse");
        assert_eq!(value, "Keep");
    }

    #[test]
    fn project_rejects_incomplete_run_block() {
        let input = "Example\n7.3 : version\n1 : cultivation year\n";

        assert_matches!(
            parse_project_file(input),
            Err(Error::Input(InputError::IncompleteSection {
                expected_lines: PROJECT_RUN_LINES,
                actual_lines: 1,
                ..
            }))
        );
    }
}
