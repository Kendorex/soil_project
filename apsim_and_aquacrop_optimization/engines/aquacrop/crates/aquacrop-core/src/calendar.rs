//! AquaCrop calendar dates and day-number conversion.

use std::num::ParseIntError;
use std::str::FromStr;

use crate::Error;
use crate::compat::{roundc_i32, trunc_i32};

const FIRST_YEAR: i32 = 1901;
const ELAPSED_DAYS: [f64; 12] = [
    0.0, 31.0, 59.25, 90.25, 120.25, 151.25, 181.25, 212.25, 243.25, 273.25, 304.25, 334.25,
];
const DAYS_IN_MONTH: [i32; 12] = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];

/// Returns the number of days in an AquaCrop calendar month.
///
/// `year` is an AquaCrop calendar year and `month` is a one-based calendar
/// month; the result is a count of calendar days.
///
/// # Errors
///
/// Returns [`CalendarError::InvalidMonth`] when `month` is not in `1..=12`.
pub fn days_in_month(year: i32, month: i32) -> Result<i32, Error> {
    let month_index = usize::try_from(month - 1)
        .ok()
        .filter(|&index| index < DAYS_IN_MONTH.len())
        .ok_or(CalendarError::InvalidMonth(month))?;
    let leap_day = i32::from(month == 2 && year % 4 == 0);
    Ok(DAYS_IN_MONTH[month_index] + leap_day)
}

/// Resolves a day number interpreted in 1901 to the same month and day in another year.
///
/// # Errors
///
/// Returns [`Error`] when the reference day number or resulting date is invalid.
pub fn reference_date_in_year(reference_day_number: i32, year: i32) -> Result<CalendarDate, Error> {
    let reference = CalendarDate::from_day_number(reference_day_number)?;
    CalendarDate::new(year, reference.month(), reference.day())
}

/// Resolves a crop-file premature-end day against a planting year.
///
/// `premature_day` is an AquaCrop day number counted from 1 January of the
/// planting year; values above 365 select the following year. `planting_year`
/// is a calendar year. An absent day returns `None`; a defined
/// result is an absolute AquaCrop day number.
///
/// # Errors
///
/// Returns [`Error`] when the premature day or resulting calendar date is invalid.
pub fn premature_end_day_number(
    premature_day: Option<i32>,
    planting_year: i32,
) -> Result<Option<i32>, Error> {
    let Some(premature_day) = premature_day else {
        return Ok(None);
    };
    let reference = CalendarDate::from_day_number(premature_day)?;
    let year = planting_year
        .checked_add(i32::from(premature_day > 365))
        .ok_or(CalendarError::YearOutOfRange(planting_year))?;
    CalendarDate::new(year, reference.month(), reference.day())
        .map(CalendarDate::day_number)
        .map(Some)
}

/// A calendar date accepted by the AquaCrop computational core.
///
/// AquaCrop uses a 1901-based `365.25` calendar. Consequently, every fourth year is a
/// leap year even when the Gregorian calendar would exclude a century year.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct CalendarDate {
    year: i32,
    month: u8,
    day: u8,
}

impl FromStr for CalendarDate {
    type Err = Error;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        let bytes = s.as_bytes();

        if bytes.len() != 10 || bytes[4] != b'-' || bytes[7] != b'-' {
            return Err(CalendarError::InvalidFormat.into());
        }

        let year: u16 = s[0..4]
            .parse()
            .map_err(|source| CalendarError::ParsingError {
                part: "year",
                source,
            })?;
        let month: u8 = s[5..7]
            .parse()
            .map_err(|source| CalendarError::ParsingError {
                part: "year",
                source,
            })?;
        let day: u8 = s[8..10]
            .parse()
            .map_err(|source| CalendarError::ParsingError {
                part: "year",
                source,
            })?;

        Self::new(year.into(), month.into(), day.into())
    }
}

#[cfg(feature = "serde")]
impl serde::Serialize for CalendarDate {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: serde::Serializer,
    {
        let Self { year, month, day } = self;
        serializer.serialize_str(&format!("{year:04}-{month:02}-{day:02}"))
    }
}

#[cfg(feature = "serde")]
impl<'de> serde::Deserialize<'de> for CalendarDate {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        let value = String::deserialize(deserializer)?;
        CalendarDate::from_str(&value).map_err(serde::de::Error::custom)
    }
}

impl CalendarDate {
    /// Creates a validated AquaCrop calendar date.
    ///
    /// `year` is a calendar year, `month` is one-based, and `day` is a
    /// one-based day of month.
    ///
    /// # Errors
    ///
    /// Returns [`CalendarError`] when the year precedes 1901, the month or day is invalid,
    /// or the corresponding AquaCrop day number cannot be represented by `i32`.
    pub fn new(year: i32, month: i32, day: i32) -> Result<Self, Error> {
        if year < FIRST_YEAR {
            return Err(CalendarError::YearOutOfRange(year).into());
        }
        let last_day = days_in_month(year, month)?;
        if !(1..=last_day).contains(&day) {
            return Err(CalendarError::InvalidDay { year, month, day }.into());
        }

        let date = Self {
            year,
            month: u8::try_from(month).map_err(|_| CalendarError::InvalidMonth(month))?,
            day: u8::try_from(day).map_err(|_| CalendarError::InvalidDay { year, month, day })?,
        };
        let day_number = date.day_number_f64();
        if day_number > f64::from(i32::MAX) {
            return Err(CalendarError::YearOutOfRange(year).into());
        }
        Ok(date)
    }

    /// Converts an AquaCrop day number to a calendar date.
    ///
    /// `day_number` is an absolute day count whose unit is one calendar day and
    /// whose epoch is 1 January 1901.
    ///
    /// # Errors
    ///
    /// Returns [`CalendarError::DayNumberOutOfRange`] for day numbers before 1.
    pub fn from_day_number(day_number: i32) -> Result<Self, Error> {
        if day_number < 1 {
            return Err(CalendarError::DayNumberOutOfRange(day_number).into());
        }

        let elapsed_years = trunc_i32((f64::from(day_number) - 0.05) / 365.25);
        let days_in_year = f64::from(day_number) - f64::from(elapsed_years) * 365.25;
        let year = FIRST_YEAR + elapsed_years;
        let mut month = 1_usize;
        while month < 12 && days_in_year > ELAPSED_DAYS[month] {
            month += 1;
        }
        let day = roundc_i32(days_in_year - ELAPSED_DAYS[month - 1] + 0.25 + 0.06);

        Self::new(
            year,
            i32::try_from(month).map_err(|_| CalendarError::DayNumberOutOfRange(day_number))?,
            day,
        )
    }

    /// Returns the year component.
    #[must_use]
    pub const fn year(self) -> i32 {
        self.year
    }

    /// Returns the one-based month component.
    #[must_use]
    pub const fn month(self) -> i32 {
        self.month as i32
    }

    /// Returns the one-based day-of-month component.
    #[must_use]
    pub const fn day(self) -> i32 {
        self.day as i32
    }

    /// Returns AquaCrop's day number, where 1 is 1 January 1901.
    #[must_use]
    pub fn day_number(self) -> i32 {
        trunc_i32(self.day_number_f64())
    }

    fn day_number_f64(self) -> f64 {
        f64::from(self.year - FIRST_YEAR) * 365.25
            + ELAPSED_DAYS[usize::from(self.month - 1)]
            + f64::from(self.day)
            + 0.05
    }
}

/// Failure to construct or decode an AquaCrop calendar date.
#[derive(Debug, thiserror::Error)]
pub enum CalendarError {
    /// The year is outside AquaCrop's supported day-number range.
    #[error("year {0} is outside the AquaCrop calendar range")]
    YearOutOfRange(i32),
    /// The month is not in `1..=12`.
    #[error("month {0} is outside 1..=12")]
    InvalidMonth(i32),
    /// The day does not exist in the given AquaCrop month and year.
    #[error("day {day} is invalid for {year:04}-{month:02}")]
    InvalidDay {
        /// Supplied year.
        year: i32,
        /// Supplied month.
        month: i32,
        /// Supplied day.
        day: i32,
    },
    /// AquaCrop day numbers start at 1.
    #[error("AquaCrop day number {0} is before 1")]
    DayNumberOutOfRange(i32),
    /// The date does not use the `YYYY-MM-DD` representation expected by AquaCrop.
    #[error("expected date in YYYY-MM-DD format")]
    InvalidFormat,
    /// A numeric component of the date could not be parsed.
    #[error("failed to parse {part}")]
    ParsingError {
        /// Name of the date component that failed to parse.
        part: &'static str,
        /// Underlying integer parsing error.
        #[source]
        source: ParseIntError,
    },
}

#[cfg(test)]
mod tests {
    use std::assert_matches;

    use super::*;

    #[test]
    fn first_aquacrop_date_has_day_number_one() {
        let date = CalendarDate::new(1901, 1, 1).expect("the AquaCrop epoch should be valid");
        assert_eq!(date.day_number(), 1);
    }

    #[test]
    fn date_and_day_number_round_trip_across_leap_day() {
        let actual = CalendarDate::from_day_number(1_155)
            .expect("the pinned leap-day number should be valid");
        let expected =
            CalendarDate::new(1904, 2, 29).expect("the pinned leap date should be valid");
        assert_eq!(actual, expected);
    }

    #[test]
    fn aquacrop_calendar_treats_century_year_as_leap_year() {
        CalendarDate::new(2100, 2, 29)
            .expect("February 29 should be valid under the AquaCrop leap-year rule");
    }

    #[test]
    fn month_length_uses_aquacrop_leap_year_rule() {
        let length = days_in_month(2100, 2).expect("February 2100 should be a valid month");
        assert_eq!(length, 29);
    }

    #[test]
    fn calendar_rejects_day_zero() {
        assert_matches!(
            CalendarDate::from_day_number(0),
            Err(Error::Calendar(CalendarError::DayNumberOutOfRange(0)))
        );
    }

    #[test]
    fn premature_end_above_day_365_moves_to_the_following_year() {
        let result = premature_end_day_number(Some(366), 2014)
            .expect("a valid premature-end day should convert to a day number");
        assert_eq!(
            result,
            Some(
                CalendarDate::new(2015, 1, 1)
                    .expect("the expected date should be valid")
                    .day_number()
            )
        );
    }

    #[test]
    fn reference_date_preserves_month_and_day_in_leap_year() {
        let date = reference_date_in_year(141, 2016).expect("the fixed onset date should be valid");

        assert!(date.year() == 2016 && date.month() == 5 && date.day() == 21);
    }
}
