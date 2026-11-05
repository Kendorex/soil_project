use std::fs;
use std::path::Path;
use std::str::FromStr;

use crate::{AquaCropVersion, Error};

use super::InputError;
use super::parse::{first_token, parse_token, required_line};

/// Onset rule declared by an AquaCrop crop-calendar file.
#[derive(Debug)]
pub enum CropOnset {
    /// Growth starts on a fixed calendar day.
    Fixed {
        /// Day number interpreted against the reference year 1901.
        reference_day_number: i32,
    },
    /// Growth onset is generated from a climate criterion.
    Generated {
        /// First day number in the search period.
        search_start_day_number: i32,
        /// Length of the search period, in days.
        search_length: i32,
        /// Numeric rainfall or air-temperature criterion code.
        criterion: i32,
        /// Threshold applied by the criterion.
        threshold: f64,
        /// Required number of successive days.
        successive: i32,
        /// Required number of occurrences.
        occurrences: i32,
    },
}

/// An AquaCrop crop-calendar input file.
#[derive(Debug)]
pub struct CropCalendar {
    /// Calendar description.
    pub description: String,
    /// Version declared inside the file.
    pub version: AquaCropVersion,
    /// Fixed or generated crop-onset rule.
    pub onset: CropOnset,
}

/// Reads and parses an AquaCrop `.CAL` file.
///
/// # Errors
///
/// Returns [`Error`] when the file cannot be read or its nine-line layout is invalid.
pub fn read_crop_calendar(path: impl AsRef<Path>) -> Result<CropCalendar, Error> {
    parse_crop_calendar(&fs::read_to_string(path).map_err(InputError::from)?)
}

/// Parses an AquaCrop `.CAL` file.
///
/// # Errors
///
/// Returns [`Error`] when the version or onset fields are invalid.
pub fn parse_crop_calendar(input: &str) -> Result<CropCalendar, Error> {
    let lines: Vec<_> = input.lines().collect();
    required_line(&lines, 9, "crop-calendar fields")?;
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
    let mode = parse_token::<i32>(lines[2], 3, "onset mode")?;
    let search_start_day_number = parse_token::<i32>(lines[3], 4, "search start day")?;
    let search_length = parse_token::<i32>(lines[4], 5, "search length")?;
    let criterion_or_day = parse_token::<i32>(lines[5], 6, "onset day or criterion")?;
    let threshold = parse_token::<f64>(lines[6], 7, "onset threshold")?;
    let successive = parse_token::<i32>(lines[7], 8, "successive days")?;
    let occurrences = parse_token::<i32>(lines[8], 9, "occurrences")?;
    let onset = if mode == 0 {
        CropOnset::Fixed {
            reference_day_number: criterion_or_day,
        }
    } else {
        CropOnset::Generated {
            search_start_day_number,
            search_length,
            criterion: criterion_or_day,
            threshold,
            successive,
            occurrences,
        }
    };

    Ok(CropCalendar {
        description: lines[0].trim_end().to_owned(),
        version,
        onset,
    })
}

#[cfg(test)]
mod tests {
    use std::assert_matches;

    use super::*;

    const FIXED_CALENDAR: &str = "Example\n7.3 : version\n0 : fixed\n-9\n-9\n141\n-9\n-9\n-9\n";

    #[test]
    fn fixed_onset_preserves_reference_day_number() {
        let calendar =
            parse_crop_calendar(FIXED_CALENDAR).expect("the fixed calendar fixture should parse");

        assert_matches!(
            calendar.onset,
            CropOnset::Fixed {
                reference_day_number: 141
            }
        );
    }

    #[test]
    fn generated_onset_preserves_criterion_fields() {
        let input = "Example\n7.3\n1\n100\n30\n13\n20.0\n8\n2\n";

        let calendar =
            parse_crop_calendar(input).expect("the generated calendar fixture should parse");
        assert_matches!(
            calendar,
            CropCalendar {
                onset: CropOnset::Generated {
                    search_start_day_number: 100,
                    search_length: 30,
                    criterion: 13,
                    threshold: 20.0,
                    successive: 8,
                    occurrences: 2,
                },
                ..
            }
        );
    }
}
