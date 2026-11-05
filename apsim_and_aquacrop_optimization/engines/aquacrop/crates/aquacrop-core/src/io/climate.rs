use std::fs;
use std::path::Path;
use std::str::FromStr;

use crate::calendar::{CalendarDate, days_in_month};
use crate::climate::{AtmosphericCo2, DailyWeather};
use crate::compat::roundc_i32;
use crate::{AquaCropVersion, Error};

use super::InputError;
use super::parse::{first_token, parse_next, parse_token, required_line};

const CLIMATE_HEADER_LINES: usize = 8;

/// Frequency represented by one observation in an AquaCrop climate data file.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ClimateRecordFrequency {
    /// One observation per calendar day.
    Daily,
    /// One observation per ten-day period, with the final period ending at month end.
    TenDaily,
    /// One observation per calendar month.
    Monthly,
}

/// Date coverage and frequency of an AquaCrop climate data file.
#[derive(Debug)]
pub struct ClimateRecord {
    /// Observation frequency.
    pub frequency: ClimateRecordFrequency,
    /// Date of the first observation or period.
    pub first_date: CalendarDate,
    /// Final date covered by the last observation or period.
    pub last_date: CalendarDate,
    /// Number of observations in the file.
    pub observation_count: usize,
}

/// A scalar AquaCrop climate data file, such as reference ET or rainfall.
#[derive(Debug)]
pub struct ScalarClimateFile {
    /// Description from the first line.
    pub description: String,
    /// File coverage metadata.
    pub record: ClimateRecord,
    /// Values in file order and in the original file units.
    pub values: Vec<f64>,
}

/// An AquaCrop minimum/maximum temperature data file.
#[derive(Debug)]
pub struct TemperatureFile {
    /// Description from the first line.
    pub description: String,
    /// File coverage metadata.
    pub record: ClimateRecord,
    /// Temperature pairs in file order.
    pub observations: Vec<(f64, f64)>,
}

pub(crate) fn align_daily_weather(
    simulation_first_day: i32,
    crop_first_day: i32,
    crop_last_day: i32,
    temperatures: &TemperatureFile,
    reference_et: &ScalarClimateFile,
    rain: &ScalarClimateFile,
    carbon_dioxide: &AtmosphericCo2,
) -> Result<Vec<DailyWeather>, Error> {
    let carbon_dioxide = carbon_dioxide
        .concentration_for_period(crop_first_day, crop_last_day)?
        .ok_or(Error::InvalidSimulationConfiguration(
            "carbon-dioxide series cannot be empty",
        ))?;
    let weather_end = temperatures
        .record
        .last_date
        .day_number()
        .min(reference_et.record.last_date.day_number())
        .min(rain.record.last_date.day_number());
    let mut weather = Vec::new();
    for day in simulation_first_day..=weather_end {
        let temperature_index = daily_index(temperatures.record.first_date.day_number(), day)?;
        let reference_et_index = daily_index(reference_et.record.first_date.day_number(), day)?;
        let rain_index = daily_index(rain.record.first_date.day_number(), day)?;
        let temperature = temperatures.observations.get(temperature_index).ok_or(
            Error::InvalidSimulationConfiguration("temperature series does not cover the run"),
        )?;
        let reference_et = *reference_et.values.get(reference_et_index).ok_or(
            Error::InvalidSimulationConfiguration("reference-ET series does not cover the run"),
        )?;
        let rain = *rain
            .values
            .get(rain_index)
            .ok_or(Error::InvalidSimulationConfiguration(
                "rainfall series does not cover the run",
            ))?;
        weather.push(DailyWeather {
            minimum_temperature: temperature.0,
            maximum_temperature: temperature.1,
            reference_et,
            rain,
            carbon_dioxide,
        });
    }
    Ok(weather)
}

fn daily_index(first: i32, requested: i32) -> Result<usize, Error> {
    usize::try_from(requested - first).map_err(|_| {
        Error::InvalidSimulationConfiguration("climate series begins after the simulation period")
    })
}

/// Filenames declared by an AquaCrop `.CLI` file.
#[derive(Debug)]
pub struct ClimateFileSet {
    /// Climate description.
    pub description: String,
    /// Version declared inside the file.
    pub version: AquaCropVersion,
    /// Minimum/maximum temperature filename.
    pub temperature_file: String,
    /// Reference evapotranspiration filename.
    pub reference_et_file: String,
    /// Rainfall filename.
    pub rain_file: String,
    /// Atmospheric carbon-dioxide filename.
    pub co2_file: String,
}

/// Reads and parses an AquaCrop `.CLI` file.
///
/// # Errors
///
/// Returns [`Error`] when the file cannot be read or has an invalid layout.
pub fn read_climate_file_set(path: impl AsRef<Path>) -> Result<ClimateFileSet, Error> {
    parse_climate_file_set(&fs::read_to_string(path).map_err(InputError::from)?)
}

/// Parses an AquaCrop `.CLI` file.
///
/// # Errors
///
/// Returns [`Error`] when a required line or version is invalid.
pub fn parse_climate_file_set(input: &str) -> Result<ClimateFileSet, Error> {
    let lines: Vec<_> = input.lines().collect();
    let version_token = first_token(required_line(&lines, 2, "AquaCrop version")?, 2, "version")?;
    let version =
        AquaCropVersion::from_str(version_token).map_err(|_| InputError::InvalidValue {
            line: 2,
            field: "AquaCrop version",
            value: version_token.to_owned(),
        })?;

    Ok(ClimateFileSet {
        description: required_line(&lines, 1, "description")?
            .trim_end()
            .to_owned(),
        version,
        temperature_file: required_filename(&lines, 3, "temperature filename")?,
        reference_et_file: required_filename(&lines, 4, "reference ET filename")?,
        rain_file: required_filename(&lines, 5, "rain filename")?,
        co2_file: required_filename(&lines, 6, "CO2 filename")?,
    })
}

/// Reads and parses a scalar AquaCrop climate data file.
///
/// # Errors
///
/// Returns [`Error`] when the file cannot be read or contains invalid metadata or data.
pub fn read_scalar_climate_file(path: impl AsRef<Path>) -> Result<ScalarClimateFile, Error> {
    parse_scalar_climate_file(&fs::read_to_string(path).map_err(InputError::from)?)
}

/// Parses a scalar AquaCrop climate data file.
///
/// # Errors
///
/// Returns [`Error`] when the header or any observation is invalid.
pub fn parse_scalar_climate_file(input: &str) -> Result<ScalarClimateFile, Error> {
    let lines: Vec<_> = input.lines().collect();
    let header = parse_header(&lines)?;
    let values = lines
        .iter()
        .enumerate()
        .skip(CLIMATE_HEADER_LINES)
        .map(|(index, line)| parse_token::<f64>(line, index + 1, "climate observation"))
        .collect::<Result<Vec<_>, _>>()?;
    let record = complete_record(&header, values.len())?;

    Ok(ScalarClimateFile {
        description: lines[0].trim_end().to_owned(),
        record,
        values,
    })
}

/// Reads and parses an AquaCrop minimum/maximum temperature file.
///
/// # Errors
///
/// Returns [`Error`] when the file cannot be read or contains invalid metadata or data.
pub fn read_temperature_file(path: impl AsRef<Path>) -> Result<TemperatureFile, Error> {
    parse_temperature_file(&fs::read_to_string(path).map_err(InputError::from)?)
}

/// Parses an AquaCrop minimum/maximum temperature file.
///
/// # Errors
///
/// Returns [`Error`] when the header or any temperature pair is invalid.
pub fn parse_temperature_file(input: &str) -> Result<TemperatureFile, Error> {
    let lines: Vec<_> = input.lines().collect();
    let header = parse_header(&lines)?;
    let observations = lines
        .iter()
        .enumerate()
        .skip(CLIMATE_HEADER_LINES)
        .map(|(index, line)| {
            let line_number = index + 1;
            let mut tokens = line.split_whitespace();
            let minimum = parse_next::<f64>(&mut tokens, line_number, "minimum temperature")?;
            let maximum = parse_next::<f64>(&mut tokens, line_number, "maximum temperature")?;
            Ok((minimum, maximum))
        })
        .collect::<Result<Vec<_>, InputError>>()?;
    let record = complete_record(&header, observations.len())?;

    Ok(TemperatureFile {
        description: lines[0].trim_end().to_owned(),
        record,
        observations,
    })
}

struct Header {
    frequency: ClimateRecordFrequency,
    first_date: CalendarDate,
}

fn parse_header(lines: &[&str]) -> Result<Header, Error> {
    required_line(lines, CLIMATE_HEADER_LINES, "climate data header")?;
    let frequency_code = parse_token::<i32>(lines[1], 2, "record frequency")?;
    let frequency = match frequency_code {
        1 => ClimateRecordFrequency::Daily,
        2 => ClimateRecordFrequency::TenDaily,
        3 => ClimateRecordFrequency::Monthly,
        _ => {
            return Err(InputError::InvalidValue {
                line: 2,
                field: "record frequency",
                value: frequency_code.to_string(),
            }
            .into());
        }
    };
    let day = parse_token::<i32>(lines[2], 3, "first day")?;
    let month = parse_token::<i32>(lines[3], 4, "first month")?;
    let year = parse_token::<i32>(lines[4], 5, "first year")?;
    let first_date = CalendarDate::new(year, month, day)?;

    match frequency {
        ClimateRecordFrequency::TenDaily if !matches!(day, 1 | 11 | 21) => {
            return Err(InputError::InvalidValue {
                line: 3,
                field: "first ten-day period",
                value: day.to_string(),
            }
            .into());
        }
        ClimateRecordFrequency::Monthly if day != 1 => {
            return Err(InputError::InvalidValue {
                line: 3,
                field: "first monthly period",
                value: day.to_string(),
            }
            .into());
        }
        ClimateRecordFrequency::Daily
        | ClimateRecordFrequency::TenDaily
        | ClimateRecordFrequency::Monthly => {}
    }

    Ok(Header {
        frequency,
        first_date,
    })
}

fn complete_record(header: &Header, count: usize) -> Result<ClimateRecord, Error> {
    let count_i32 = i32::try_from(count).map_err(|_| InputError::TooManyObservations(count))?;
    let last_date = match header.frequency {
        ClimateRecordFrequency::Daily => {
            let offset = count_i32.saturating_sub(1);
            let last_day_number = header
                .first_date
                .day_number()
                .checked_add(offset)
                .ok_or(InputError::TooManyObservations(count))?;
            CalendarDate::from_day_number(last_day_number)?
        }
        ClimateRecordFrequency::TenDaily => {
            complete_ten_daily_record(header.first_date, count_i32)?
        }
        ClimateRecordFrequency::Monthly => complete_monthly_record(header.first_date, count_i32)?,
    };

    Ok(ClimateRecord {
        frequency: header.frequency,
        first_date: header.first_date,
        last_date,
        observation_count: count,
    })
}

fn complete_ten_daily_record(first_date: CalendarDate, count: i32) -> Result<CalendarDate, Error> {
    let mut period = roundc_i32(f64::from(first_date.day() + 9) / 10.0) + count - 1;
    let mut month = first_date.month();
    let mut year = first_date.year();
    while period > 3 {
        period -= 3;
        month += 1;
        if month > 12 {
            month = 1;
            year += 1;
        }
    }
    let day = match period {
        1 => 10,
        2 => 20,
        _ => days_in_month(year, month)?,
    };
    CalendarDate::new(year, month, day)
}

fn complete_monthly_record(first_date: CalendarDate, count: i32) -> Result<CalendarDate, Error> {
    let zero_based_month = first_date.month() - 1 + count - 1;
    let year = first_date.year() + zero_based_month.div_euclid(12);
    let month = zero_based_month.rem_euclid(12) + 1;
    CalendarDate::new(year, month, days_in_month(year, month)?)
}

fn required_filename(
    lines: &[&str],
    line_number: usize,
    field: &'static str,
) -> Result<String, Error> {
    Ok(first_token(
        required_line(lines, line_number, field)?,
        line_number,
        field,
    )?
    .to_owned())
}

#[cfg(test)]
mod tests {
    use std::assert_matches;

    use super::*;

    const SCALAR_DAILY: &str =
        "Example\n1 : daily\n1 : day\n1 : month\n2014 : year\n\nvalue\n=====\n1.5\n2.5\n";
    const TEMPERATURE_DAILY: &str = "Example\n1 : daily\n1 : day\n1 : month\n2014 : year\n\nvalues\n=====\n-2.0 8.0\n1.0 11.0\n";

    #[test]
    fn scalar_daily_file_parses_values_and_coverage() {
        let result = parse_scalar_climate_file(SCALAR_DAILY)
            .expect("the scalar climate fixture should parse");

        assert_matches!(
            result,
            ScalarClimateFile {
                values,
                record: ClimateRecord {
                    observation_count: 2,
                    ..
                },
                ..
            } if values == [1.5, 2.5]
        );
    }

    #[test]
    fn temperature_file_parses_minimum_and_maximum_columns() {
        let result = parse_temperature_file(TEMPERATURE_DAILY)
            .expect("the temperature fixture should parse");

        assert_matches!(
            result,
            TemperatureFile { observations, .. }
                if observations == [
                    (-2.0, 8.0),
                    (1.0, 11.0),
                ]
        );
    }

    #[test]
    fn climate_file_set_reads_declared_version_and_names() {
        let input = "Example\n7.3 : version\ntemp.Tnx\neto.ETo\nrain.PLU\nco2.CO2\n";

        let file_set =
            parse_climate_file_set(input).expect("the climate file-set fixture should parse");
        assert_matches!(
            file_set,
            ClimateFileSet { version, temperature_file, .. }
                if version == AquaCropVersion::new(7, 3) && temperature_file == "temp.Tnx"
        );
    }
}
