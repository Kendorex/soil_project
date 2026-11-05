use std::fs;
use std::path::Path;
use std::str::{FromStr, Lines};

use crate::Error;
use crate::calendar::CalendarDate;
use crate::compat::roundc_i32;

use super::InputError;

const MODE_NONE: i32 = 0;
const MODE_CONSTANT: i32 = 1;

/// One dated groundwater observation, in the units the reference model stores internally.
#[derive(Debug, Clone, Copy)]
pub struct GroundwaterObservation {
    /// Absolute AquaCrop day number the observation applies to.
    ///
    /// For a constant-mode table (or a variable-mode table with a single
    /// observation) this value is unused, since [`GroundwaterTable::at`] always
    /// returns the sole observation regardless of day.
    pub day_number: i32,
    /// Depth below the soil surface, in metres, rounded to the nearest centimetre
    /// (matching the reference's integer-centimetre internal representation).
    pub depth: f64,
    /// Electrical conductivity, in dS/m.
    pub conductivity: f64,
}

/// A parsed AquaCrop `.GWT` groundwater-table file.
///
/// An empty `observations` list represents mode 0 ("no groundwater table"); the
/// reference model's constant mode and single-observation variable mode both
/// collapse to one entry, since both return that entry's value for every day.
#[derive(Debug)]
pub struct GroundwaterTable {
    /// Description from the first line.
    pub description: String,
    /// Observations in strictly increasing day-number order.
    pub observations: Vec<GroundwaterObservation>,
}

impl GroundwaterTable {
    /// Returns the depth (metres) and conductivity (dS/m) for a day.
    ///
    /// Days before the first observation or after the last use the nearest
    /// endpoint's value; days between two observations are interpolated
    /// linearly, matching the reference's `GetZandECgwt`. Returns `None` when
    /// the table has no observations (mode 0, or "(None)").
    #[must_use]
    pub fn at(&self, day_number: i32) -> Option<(f64, f64)> {
        let first = self.observations.first()?;
        if day_number <= first.day_number {
            return Some((first.depth, first.conductivity));
        }
        for pair in self.observations.windows(2) {
            let lower = &pair[0];
            let upper = &pair[1];
            if day_number <= upper.day_number {
                let day_fraction = f64::from(day_number - lower.day_number)
                    / f64::from(upper.day_number - lower.day_number);
                let depth_cm = roundc_i32(
                    lower.depth * 100.0 + (upper.depth - lower.depth) * 100.0 * day_fraction,
                );
                let conductivity =
                    lower.conductivity + (upper.conductivity - lower.conductivity) * day_fraction;
                return Some((f64::from(depth_cm) / 100.0, conductivity));
            }
        }
        self.observations
            .last()
            .map(|observation| (observation.depth, observation.conductivity))
    }
}

/// Reads and parses an AquaCrop `.GWT` groundwater-table file.
///
/// # Errors
///
/// Returns [`Error`] when the file cannot be read or parsed, or when it
/// describes a cyclical (year-independent) variable table (see
/// [`parse_groundwater_file`]).
pub fn read_groundwater_file(path: impl AsRef<Path>) -> Result<GroundwaterTable, Error> {
    parse_groundwater_file(&fs::read_to_string(path).map_err(InputError::from)?)
}

/// Parses an AquaCrop `.GWT` groundwater-table file.
///
/// Supports mode 0 (no table), mode 1 (constant depth/conductivity), and any
/// other mode (variable, day-dated depth/conductivity observations
/// interpolated between points) when the first-observation year is a real
/// calendar year.
///
/// # Errors
///
/// Returns [`Error`] when a required line or field is missing or malformed, or
/// when the first-observation year is `1901` — the reference's sentinel for a
/// cyclical, year-independent table repeated every calendar year. That mode is
/// not yet supported; wiring it up is a distinct, separately scoped follow-up.
pub fn parse_groundwater_file(input: &str) -> Result<GroundwaterTable, Error> {
    let mut lines = GroundwaterLines::new(input);
    let description = lines.text("groundwater description")?.trim_end().to_owned();
    lines.text("AquaCrop version")?;
    let mode = lines.parse::<i32>("groundwater table mode")?;

    if mode == MODE_NONE {
        return Ok(GroundwaterTable {
            description,
            observations: Vec::new(),
        });
    }

    let first_day_number = if mode == MODE_CONSTANT {
        None
    } else {
        let day = lines.parse::<i32>("first-observation day")?;
        let month = lines.parse::<i32>("first-observation month")?;
        let year = lines.parse::<i32>("first-observation year")?;
        if year == 1901 {
            return Err(Error::InvalidSimulationConfiguration(
                "cyclical (year-independent) variable groundwater tables are not yet supported",
            ));
        }
        Some(CalendarDate::new(year, month, day)?.day_number())
    };

    // Three lines of column header/separator decoration, unconditionally skipped
    // by the reference regardless of mode.
    lines.text("groundwater header")?;
    lines.text("groundwater header")?;
    lines.text("groundwater header")?;

    let mut observations = Vec::new();
    for (line_number, line) in lines.remaining() {
        let mut tokens = line.split_whitespace();
        let Some(day_field) = tokens.next() else {
            continue;
        };
        let Ok(day_offset) = day_field.parse::<f64>() else {
            continue;
        };
        let depth_token = next_token(&mut tokens, line_number, "groundwater depth")?;
        let depth_raw: f64 = parse_token(depth_token, line_number, "groundwater depth")?;
        let conductivity_token = next_token(&mut tokens, line_number, "groundwater conductivity")?;
        let conductivity: f64 =
            parse_token(conductivity_token, line_number, "groundwater conductivity")?;
        let depth = f64::from(roundc_i32(depth_raw * 100.0)) / 100.0;
        let day_number = first_day_number.map_or(0, |first_day_number| {
            first_day_number + roundc_i32(day_offset) - 1
        });
        observations.push(GroundwaterObservation {
            day_number,
            depth,
            conductivity,
        });
        if mode == MODE_CONSTANT {
            break;
        }
    }

    Ok(GroundwaterTable {
        description,
        observations,
    })
}

fn next_token<'a>(
    tokens: &mut impl Iterator<Item = &'a str>,
    line_number: usize,
    field: &'static str,
) -> Result<&'a str, InputError> {
    tokens.next().ok_or(InputError::MissingLine {
        line: line_number,
        field,
    })
}

fn parse_token<T>(token: &str, line_number: usize, field: &'static str) -> Result<T, InputError>
where
    T: FromStr,
{
    token.parse().map_err(|_| InputError::InvalidValue {
        line: line_number,
        field,
        value: token.to_owned(),
    })
}

struct GroundwaterLines<'a> {
    lines: Lines<'a>,
    line_number: usize,
}

impl<'a> GroundwaterLines<'a> {
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
        parse_token(token, self.line_number, field)
    }

    fn remaining(self) -> impl Iterator<Item = (usize, &'a str)> {
        let start = self.line_number + 1;
        self.lines
            .enumerate()
            .map(move |(offset, line)| (start + offset, line))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const CONSTANT: &str = "Constant table\n     7.1   : AquaCrop Version (August 2023)\n     1     : groundwater table at fixed depth and with constant salinity\n\n   Day    Depth (m)    ECw (dS/m)\n====================================\n     1      1.20          3.00\n";

    const VARIABLE: &str = "Variable table\n     7.1   : AquaCrop Version\n     2     : variable groundwater table\n     1\n     1\n  2014\n\n   Day    Depth (m)    ECw (dS/m)\n====================================\n     1      1.00          2.00\n   181      2.00          4.00\n";

    #[test]
    fn no_table_has_no_observations() {
        let table = parse_groundwater_file("None\n 7.1\n 0\n").expect("mode 0 should parse");
        assert!(table.observations.is_empty());
        assert_eq!(table.at(41414), None);
    }

    #[test]
    fn constant_table_applies_to_every_day() {
        let table = parse_groundwater_file(CONSTANT).expect("constant table should parse");
        assert_eq!(table.at(1), Some((1.2, 3.0)));
        assert_eq!(table.at(41414), Some((1.2, 3.0)));
    }

    #[test]
    fn variable_table_interpolates_between_observations() {
        let table = parse_groundwater_file(VARIABLE).expect("variable table should parse");
        let first_day = CalendarDate::new(2014, 1, 1)
            .expect("2014-01-01 should be a valid calendar date")
            .day_number();
        assert_eq!(table.at(first_day), Some((1.0, 2.0)));
        assert_eq!(table.at(first_day - 10), Some((1.0, 2.0)));
        assert_eq!(table.at(first_day + 180), Some((2.0, 4.0)));
        assert_eq!(table.at(first_day + 1000), Some((2.0, 4.0)));
        let midpoint = table
            .at(first_day + 90)
            .expect("midpoint should interpolate");
        assert!((midpoint.0 - 1.5).abs() < 1e-9);
        assert!((midpoint.1 - 3.0).abs() < 1e-9);
    }

    #[test]
    fn cyclical_year_is_rejected() {
        let input = "Cyclical\n 7.1\n 2\n     1\n     1\n  1901\n\n a\n b\n c\n     1      1.00          2.00\n";
        let error = parse_groundwater_file(input).expect_err("year 1901 should be rejected");
        assert!(matches!(error, Error::InvalidSimulationConfiguration(_)));
    }
}
