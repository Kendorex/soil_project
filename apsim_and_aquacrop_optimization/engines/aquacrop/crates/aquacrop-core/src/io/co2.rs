use std::fs;
use std::path::Path;

use crate::Error;
use crate::climate::{AtmosphericCo2, Co2Observation};

use super::InputError;
use super::parse::{parse_next, required_line};

const CO2_HEADER_LINES: usize = 3;

/// Contents of an AquaCrop atmospheric `.CO2` file.
#[derive(Debug)]
pub struct AtmosphericCo2File {
    /// Description from the first line.
    pub description: String,
    /// Parsed annual concentration series.
    pub series: AtmosphericCo2,
}

/// Reads and parses an AquaCrop atmospheric `.CO2` file.
///
/// # Errors
///
/// Returns [`Error`] when the file cannot be read or contains invalid or unordered records.
pub fn read_co2_file(path: impl AsRef<Path>) -> Result<AtmosphericCo2File, Error> {
    parse_co2_file(&fs::read_to_string(path).map_err(InputError::from)?)
}

/// Parses an AquaCrop atmospheric `.CO2` file.
///
/// # Errors
///
/// Returns [`Error`] when the header is incomplete or a record is invalid or unordered.
pub fn parse_co2_file(input: &str) -> Result<AtmosphericCo2File, Error> {
    let lines: Vec<_> = input.lines().collect();
    required_line(&lines, CO2_HEADER_LINES, "CO2 header")?;
    let mut observations = Vec::with_capacity(lines.len().saturating_sub(CO2_HEADER_LINES));
    for (index, line) in lines.iter().enumerate().skip(CO2_HEADER_LINES) {
        let line_number = index + 1;
        let mut tokens = line.split_whitespace();
        let year = parse_next::<i32>(&mut tokens, line_number, "CO2 year")?;
        let concentration = parse_next::<f64>(&mut tokens, line_number, "CO2 concentration")?;
        if observations
            .last()
            .is_some_and(|previous: &Co2Observation| previous.year >= year)
        {
            return Err(InputError::InvalidValue {
                line: line_number,
                field: "increasing CO2 year",
                value: year.to_string(),
            }
            .into());
        }
        observations.push(Co2Observation {
            year,
            concentration,
        });
    }

    Ok(AtmosphericCo2File {
        description: lines[0].trim_end().to_owned(),
        series: AtmosphericCo2::new(observations),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const SERIES: &str = "Example\nYear CO2\n========\n2000 369.0\n2002 373.0\n";

    #[test]
    fn parser_preserves_description_and_observation_order() {
        let file = parse_co2_file(SERIES).expect("the CO2 fixture should parse");

        assert_eq!(file.description, "Example");

        let observations = file.series.observations();
        assert_eq!(observations.len(), 2);
        assert_eq!(observations[0].year, 2000);
        assert_eq!(observations[1].year, 2002);
    }
}
