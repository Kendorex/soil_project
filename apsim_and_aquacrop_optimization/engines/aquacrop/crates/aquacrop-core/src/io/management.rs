use std::fs;
use std::path::Path;
use std::str::{FromStr, Lines};

use super::sentinel::NegativeNineSentinel;
use crate::management::{CuttingCriterion, CuttingManagement, CuttingRecord, Management};
use crate::{AquaCropVersion, Error};

use super::InputError;

/// Reads and parses an AquaCrop `.MAN` management file.
///
/// # Errors
///
/// Returns [`Error`] when the file cannot be read or a version-dependent field is invalid.
pub fn read_management_file(path: impl AsRef<Path>) -> Result<Management, Error> {
    parse_management_file(&fs::read_to_string(path).map_err(InputError::from)?)
}

/// Parses an AquaCrop `.MAN` management file, including cutting records.
///
/// # Errors
///
/// Returns [`Error`] when a required header field or numeric cutting record is malformed.
pub fn parse_management_file(input: &str) -> Result<Management, Error> {
    let mut lines = ManagementLines::new(input);
    lines.text("management description")?;
    let version_text = lines.token("AquaCrop version")?;
    let version =
        AquaCropVersion::from_str(version_text).map_err(|_| InputError::InvalidValue {
            line: 2,
            field: "AquaCrop version",
            value: version_text.to_owned(),
        })?;
    let version_code = i32::from(version.major) * 10 + i32::from(version.minor);
    let mulch_cover = lines.parse("mulch cover")?;
    let mulch_evaporation_reduction = lines.parse("mulch evaporation reduction")?;
    let fertility_stress = lines.parse("fertility stress")?;
    let bund_height = lines.parse("bund height")?;
    let runoff_enabled = lines.parse::<i8>("runoff prevention")? != 1;

    let curve_number_correction = if version_code < 50 {
        0
    } else {
        lines.parse("curve-number correction")?
    };
    let (weed_cover, weed_cover_delta, weed_canopy_shape) = if version_code < 50 {
        (0, 0, -0.01)
    } else {
        let cover = lines.parse("weed cover")?;
        let delta = if version_code < 51 {
            0
        } else {
            lines.parse("weed-cover change")?
        };
        let shape = lines.parse("weed canopy shape")?;
        (cover, delta, shape)
    };
    let weed_self_thinning_replacement = if version_code < 70 {
        100
    } else {
        lines.parse("weed self-thinning replacement")?
    };

    let cuttings = if version_code < 70 {
        CuttingManagement {
            considered: false,
            canopy_cover_after_cutting: 30,
            first_window_day: 1,
            window_length: None,
            generated: false,
            criterion: CuttingCriterion::NotApplicable,
            harvest_at_maturity: false,
            first_list_day_number: None,
            records: Vec::new(),
        }
    } else {
        let considered = lines.parse::<i8>("multiple-cuttings flag")? != 0;
        let canopy_cover_after_cutting = lines.parse("post-cutting canopy cover")?;
        lines.parse::<i32>("obsolete post-cutting growth increase")?;
        let first_window_day = lines.parse("first cutting-window day")?;
        let window_length = lines
            .parse::<i32>("cutting-window length")?
            .without_negative_nine();
        let generated = lines.parse::<i8>("generated-cuttings flag")? == 1;
        let criterion = parse_cutting_criterion(lines.parse("cutting criterion")?);
        let harvest_at_maturity = lines.parse::<i8>("final-harvest flag")? == 1;
        let first_list_day_number = lines
            .parse::<i32>("first cutting-list day number")?
            .without_negative_nine();
        let records = parse_cutting_records(lines.remaining(), generated)?;
        CuttingManagement {
            considered,
            canopy_cover_after_cutting,
            first_window_day,
            window_length,
            generated,
            criterion,
            harvest_at_maturity,
            first_list_day_number,
            records,
        }
    };

    Ok(Management {
        mulch_cover,
        mulch_evaporation_reduction,
        fertility_stress,
        bund_height,
        runoff_enabled,
        curve_number_correction,
        weed_cover,
        weed_cover_delta,
        weed_canopy_shape,
        weed_self_thinning_replacement,
        cuttings,
    })
}

fn parse_cutting_criterion(code: i8) -> CuttingCriterion {
    match code {
        1 => CuttingCriterion::CalendarDays,
        2 => CuttingCriterion::GrowingDegreeDays,
        3 => CuttingCriterion::DryBiomass,
        4 => CuttingCriterion::DryYield,
        5 => CuttingCriterion::FreshYield,
        _ => CuttingCriterion::NotApplicable,
    }
}

fn parse_cutting_records<'a>(
    lines: impl Iterator<Item = (usize, &'a str)>,
    generated: bool,
) -> Result<Vec<CuttingRecord>, InputError> {
    let mut records = Vec::new();
    for (line_number, line) in lines {
        let mut tokens = line.split_whitespace();
        let Some(first) = tokens.next() else {
            continue;
        };
        let Ok(from_day) = first.parse::<i32>() else {
            continue;
        };
        let target = if generated {
            let token = tokens.next().ok_or(InputError::MissingLine {
                line: line_number,
                field: "generated-cutting target",
            })?;
            Some(token.parse().map_err(|_| InputError::InvalidValue {
                line: line_number,
                field: "generated-cutting target",
                value: token.to_owned(),
            })?)
        } else {
            None
        };
        records.push(CuttingRecord { from_day, target });
    }
    Ok(records)
}

struct ManagementLines<'a> {
    lines: Lines<'a>,
    line_number: usize,
}

impl<'a> ManagementLines<'a> {
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
        token.parse().map_err(|_| InputError::InvalidValue {
            line: self.line_number,
            field,
            value: token.to_owned(),
        })
    }

    fn remaining(self) -> impl Iterator<Item = (usize, &'a str)> {
        let start = self.line_number + 1;
        self.lines
            .enumerate()
            .map(move |(offset, line)| (start + offset, line))
    }
}
