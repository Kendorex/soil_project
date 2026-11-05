use std::fs;
use std::path::Path;
use std::str::{FromStr, Lines};

use crate::compat::roundc_i32;
use crate::irrigation::{
    IrrigationDepthCriterion, IrrigationMethod, IrrigationMode, IrrigationRecord,
    IrrigationSchedule, IrrigationTimeCriterion,
};
use crate::{AquaCropVersion, Error};

use super::InputError;
use super::sentinel::NegativeNineSentinel;

/// Reads and parses an AquaCrop `.IRR` irrigation file.
///
/// # Errors
///
/// Returns [`Error`] when the file cannot be read or a version-dependent field is invalid.
pub fn read_irrigation_file(path: impl AsRef<Path>) -> Result<IrrigationSchedule, Error> {
    parse_irrigation_file(&fs::read_to_string(path).map_err(InputError::from)?)
}

/// Parses an AquaCrop `.IRR` irrigation file and its schedule records.
///
/// # Errors
///
/// Returns [`Error`] when a required header value or numeric schedule record is malformed.
pub fn parse_irrigation_file(input: &str) -> Result<IrrigationSchedule, Error> {
    let mut lines = IrrigationLines::new(input);
    lines.text("irrigation description")?;
    let version_text = lines.token("AquaCrop version")?;
    let version =
        AquaCropVersion::from_str(version_text).map_err(|_| InputError::InvalidValue {
            line: 2,
            field: "AquaCrop version",
            value: version_text.to_owned(),
        })?;
    let version_code = i32::from(version.major) * 10 + i32::from(version.minor);
    let method = match lines.parse::<i32>("irrigation method")? {
        1 => IrrigationMethod::Sprinkler,
        2 => IrrigationMethod::Basin,
        3 => IrrigationMethod::Border,
        4 => IrrigationMethod::Furrow,
        _ => IrrigationMethod::Drip,
    };
    let wetted_surface = lines.parse("wetted surface")?;
    let mode_code = lines.parse::<i32>("irrigation mode")?;
    let mode = match mode_code {
        0 => IrrigationMode::Rainfed,
        1 => IrrigationMode::Manual {
            first_day_number: if version_code >= 70 {
                lines
                    .parse::<i32>("first irrigation-list day number")?
                    .without_negative_nine()
            } else {
                None
            },
        },
        2 => {
            let time_criterion = match lines.parse::<i32>("irrigation time criterion")? {
                1 => IrrigationTimeCriterion::FixedInterval,
                2 => IrrigationTimeCriterion::RootZoneDepletion,
                4 => IrrigationTimeCriterion::WaterBetweenBunds,
                _ => IrrigationTimeCriterion::ReadilyAvailableWaterDepletion,
            };
            let depth_criterion = if lines.parse::<i32>("irrigation depth criterion")? == 1 {
                IrrigationDepthCriterion::ToFieldCapacity
            } else {
                IrrigationDepthCriterion::FixedDepth
            };
            let end_exclusion = if version_code >= 73 {
                lines
                    .parse::<i32>("last days without irrigation")?
                    .without_negative_nine()
            } else {
                None
            };
            IrrigationMode::Generated {
                time_criterion,
                depth_criterion,
                end_exclusion,
            }
        }
        _ => IrrigationMode::NetRequirement {
            readily_available_water_depletion: lines.parse("net-irrigation RAW depletion")?,
        },
    };
    let records = parse_irrigation_records(lines.remaining(), mode_code, version_code)?;

    Ok(IrrigationSchedule {
        method,
        wetted_surface,
        mode,
        records,
    })
}

fn parse_irrigation_records<'a>(
    lines: impl Iterator<Item = (usize, &'a str)>,
    mode_code: i32,
    version_code: i32,
) -> Result<Vec<IrrigationRecord>, InputError> {
    let mut records = Vec::new();
    for (line_number, line) in lines {
        let mut tokens = line.split_whitespace();
        let Some(first) = tokens.next() else {
            continue;
        };
        if first.parse::<f64>().is_err() {
            continue;
        }
        match mode_code {
            1 => records.push(parse_manual_record(
                first,
                &mut tokens,
                line_number,
                version_code,
            )?),
            2 => records.push(parse_generated_record(
                first,
                &mut tokens,
                line_number,
                version_code,
            )?),
            _ => {}
        }
    }
    Ok(records)
}

fn parse_manual_record<'a>(
    first: &str,
    tokens: &mut impl Iterator<Item = &'a str>,
    line_number: usize,
    version_code: i32,
) -> Result<IrrigationRecord, InputError> {
    let day = parse_rounded(first, line_number, "manual-irrigation day")?;
    let depth_token = next_record_token(tokens, line_number, "manual-irrigation depth")?;
    let depth = parse_rounded(depth_token, line_number, "manual-irrigation depth")?;
    let electrical_conductivity = if version_code < 32 {
        None
    } else {
        let token = next_record_token(tokens, line_number, "irrigation-water conductivity")?;
        Some(parse_record_token(
            token,
            line_number,
            "irrigation-water conductivity",
        )?)
    };
    Ok(IrrigationRecord::Manual {
        day,
        depth,
        electrical_conductivity,
    })
}

fn parse_generated_record<'a>(
    first: &str,
    tokens: &mut impl Iterator<Item = &'a str>,
    line_number: usize,
    version_code: i32,
) -> Result<IrrigationRecord, InputError> {
    let from_day = parse_record_token(first, line_number, "generated-rule first day")?;
    let time_token = next_record_token(tokens, line_number, "generated-rule time parameter")?;
    let time_parameter =
        parse_record_token(time_token, line_number, "generated-rule time parameter")?;
    let depth_token = next_record_token(tokens, line_number, "generated-rule depth parameter")?;
    let depth_parameter =
        parse_record_token::<i32>(depth_token, line_number, "generated-rule depth parameter")?
            .without_negative_nine();
    let electrical_conductivity = if version_code < 32 {
        None
    } else {
        let token = next_record_token(tokens, line_number, "irrigation-water conductivity")?;
        Some(parse_record_token(
            token,
            line_number,
            "irrigation-water conductivity",
        )?)
    };
    Ok(IrrigationRecord::Generated {
        from_day,
        time_parameter,
        depth_parameter,
        electrical_conductivity,
    })
}

fn next_record_token<'a>(
    tokens: &mut impl Iterator<Item = &'a str>,
    line_number: usize,
    field: &'static str,
) -> Result<&'a str, InputError> {
    tokens.next().ok_or(InputError::MissingLine {
        line: line_number,
        field,
    })
}

fn parse_record_token<T>(
    token: &str,
    line_number: usize,
    field: &'static str,
) -> Result<T, InputError>
where
    T: FromStr,
{
    token.parse().map_err(|_| InputError::InvalidValue {
        line: line_number,
        field,
        value: token.to_owned(),
    })
}

fn parse_rounded(token: &str, line_number: usize, field: &'static str) -> Result<i32, InputError> {
    parse_record_token::<f64>(token, line_number, field).map(roundc_i32)
}

struct IrrigationLines<'a> {
    lines: Lines<'a>,
    line_number: usize,
}

impl<'a> IrrigationLines<'a> {
    fn new(input: &'a str) -> Self {
        Self {
            lines: input.lines(),
            line_number: 0,
        }
    }

    fn text(&mut self, field: &'static str) -> Result<&'a str, InputError> {
        self.line_number += 1;
        self.lines.next().ok_or(InputError::MissingLine {
            line: self.line_number,
            field,
        })
    }

    fn token(&mut self, field: &'static str) -> Result<&'a str, InputError> {
        let line = self.text(field)?;
        line.split_whitespace()
            .next()
            .ok_or(InputError::MissingLine {
                line: self.line_number,
                field,
            })
    }

    fn parse<T>(&mut self, field: &'static str) -> Result<T, InputError>
    where
        T: FromStr,
    {
        let token = self.token(field)?;
        parse_record_token(token, self.line_number, field)
    }

    fn remaining(self) -> impl Iterator<Item = (usize, &'a str)> {
        let start = self.line_number + 1;
        self.lines
            .enumerate()
            .map(move |(offset, line)| (start + offset, line))
    }
}
