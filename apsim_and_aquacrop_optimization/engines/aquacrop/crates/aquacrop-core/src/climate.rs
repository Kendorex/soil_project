//! Climate-related calculation primitives in AquaCrop reference units.

use std::borrow::Cow;

use crate::Error;
use crate::calendar::{CalendarDate, days_in_month};
use crate::compat::{REFERENCE_CO2, roundc_i32};

/// Minimum and maximum air temperature for one day, in degrees Celsius.
pub trait TemperatureExtrema {
    /// Minimum air temperature.
    fn minimum(&self) -> f64;

    /// Maximum air temperature.
    fn maximum(&self) -> f64;
}

impl TemperatureExtrema for (f64, f64) {
    fn minimum(&self) -> f64 {
        self.0
    }

    fn maximum(&self) -> f64 {
        self.1
    }
}

/// Weather supplied for one consecutive simulation day.
///
/// The containing sequence starts on the simulation's first day. Dates and
/// AquaCrop day numbers are derived from that position during initialization.
#[derive(Clone, Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct DailyWeather {
    /// Minimum air temperature, in degrees Celsius.
    pub minimum_temperature: f64,
    /// Maximum air temperature, in degrees Celsius.
    pub maximum_temperature: f64,
    /// Reference evapotranspiration, in millimetres.
    pub reference_et: f64,
    /// Rainfall, in millimetres.
    pub rain: f64,
    /// Atmospheric carbon-dioxide concentration, in parts per million.
    pub carbon_dioxide: f64,
}

impl TemperatureExtrema for DailyWeather {
    fn minimum(&self) -> f64 {
        self.minimum_temperature
    }

    fn maximum(&self) -> f64 {
        self.maximum_temperature
    }
}

/// One annual atmospheric carbon-dioxide observation.
#[derive(Clone, Debug)]
pub struct Co2Observation {
    /// Calendar year.
    pub year: i32,
    /// Atmospheric concentration, in parts per million by volume.
    pub concentration: f64,
}

/// Annual atmospheric carbon-dioxide series used by AquaCrop calculations.
#[derive(Debug)]
pub struct AtmosphericCo2 {
    /// Observations in strictly increasing year order.
    observations: Cow<'static, [Co2Observation]>,
}

impl AtmosphericCo2 {
    /// Creates an atmospheric CO2 series by taking ownership of the observations.
    ///
    /// This constructor accepts any value that can be converted into a vector,
    /// including an existing [`Vec<Co2Observation>`].
    #[must_use]
    pub fn new<T: Into<Vec<Co2Observation>>>(observations: T) -> Self {
        Self {
            observations: Cow::Owned(observations.into()),
        }
    }

    /// Creates an atmospheric CO2 series backed by a static observation slice.
    ///
    /// The slice is borrowed without copying and must remain valid for the
    /// entire program lifetime.
    #[must_use]
    pub const fn from_static(observations: &'static [Co2Observation]) -> Self {
        Self {
            observations: Cow::Borrowed(observations),
        }
    }

    #[cfg(test)]
    pub(crate) fn observations(&self) -> &[Co2Observation] {
        self.observations.as_ref()
    }

    /// Returns the concentration for a year, linearly interpolating between records.
    ///
    /// Years outside the series use the nearest endpoint, matching the reference model.
    /// `year` is a calendar year and the returned concentration is in parts per
    /// million by volume.
    #[must_use]
    pub fn concentration_for_year(&self, year: i32) -> Option<f64> {
        let first = self.observations.first()?;
        if year <= first.year {
            return Some(first.concentration);
        }
        for pair in self.observations.windows(2) {
            let lower = &pair[0];
            let upper = &pair[1];
            if year <= upper.year {
                let year_fraction =
                    f64::from(year - lower.year) / f64::from(upper.year - lower.year);
                return Some(
                    lower.concentration
                        + (upper.concentration - lower.concentration) * year_fraction,
                );
            }
        }
        self.observations.last().map(|value| value.concentration)
    }

    /// Returns the AquaCrop concentration for a simulation period.
    ///
    /// The reference model averages concentrations for the first and last calendar years;
    /// a year of 1901 selects the fixed reference concentration. Both arguments are
    /// absolute AquaCrop day numbers.
    ///
    /// # Errors
    ///
    /// Returns [`Error`] when either day number is invalid.
    #[expect(
        clippy::manual_midpoint,
        reason = "addition before division preserves the reference Fortran operation order"
    )]
    pub fn concentration_for_period(
        &self,
        first_day_number: i32,
        last_day_number: i32,
    ) -> Result<Option<f64>, Error> {
        let first_year = CalendarDate::from_day_number(first_day_number)?.year();
        let last_year = CalendarDate::from_day_number(last_day_number)?.year();
        if first_year == 1901 || last_year == 1901 {
            return Ok(Some(REFERENCE_CO2));
        }
        Ok(self
            .concentration_for_year(first_year)
            .zip(self.concentration_for_year(last_year))
            .map(|(first, last)| (first + last) / 2.0))
    }
}

/// Reference climate required by calibrated fertility and salinity calculations.
#[derive(Clone, Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct CalibrationClimate {
    /// Generated 365-day reference temperature climatology.
    pub reference_temperatures: ReferenceTemperatureClimatology,
    /// Reference atmospheric carbon-dioxide concentration, in parts per million.
    pub reference_carbon_dioxide: f64,
}

/// Derives the reference climate used by calibrated fertility and salinity calculations.
///
/// `temperatures` must contain consecutive daily observations from `first_date`
/// Carbon dioxide for the rounded midpoint year is obtained from `carbon_dioxide`,
/// matching AquaCrop's reference preparation.
///
/// # Errors
///
/// Returns [`Error`] when the temperature coverage is inconsistent or either
/// climate series is empty.
pub fn prepare_calibration_climate<T: TemperatureExtrema>(
    first_date: CalendarDate,
    temperatures: &[T],
    carbon_dioxide: &AtmosphericCo2,
) -> Result<CalibrationClimate, Error> {
    let temperature_day_count = temperatures
        .len()
        .checked_sub(1)
        .and_then(|count| i32::try_from(count).ok())
        .ok_or(Error::InvalidSimulationConfiguration(
            "reference temperature series cannot be empty or is too long",
        ))?;

    let last_day_number = first_date
        .day_number()
        .checked_add(temperature_day_count)
        .ok_or(Error::InvalidSimulationConfiguration(
            "reference temperature date range is invalid",
        ))?;
    let last_date = CalendarDate::from_day_number(last_day_number)?;

    let temperature_pairs: Vec<_> = temperatures
        .iter()
        .map(|temperature| (temperature.minimum(), temperature.maximum()))
        .collect();
    let reference_temperatures =
        reference_temperature_climatology(first_date.day_number(), &temperature_pairs)?;
    let reference_year = roundc_i32(f64::from(first_date.year() + last_date.year()) / 2.0);
    let reference_carbon_dioxide = carbon_dioxide
        .concentration_for_year(reference_year)
        .ok_or(Error::InvalidSimulationConfiguration(
            "carbon-dioxide series cannot be empty",
        ))?;
    Ok(CalibrationClimate {
        reference_temperatures,
        reference_carbon_dioxide,
    })
}

/// One temperature pair in the generated 365-day reference climatology.
#[derive(Clone, Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct ReferenceTemperature {
    /// Minimum air temperature, in degrees Celsius.
    pub minimum: f64,
    /// Maximum air temperature, in degrees Celsius.
    pub maximum: f64,
}

/// A validated 365-day temperature climatology for AquaCrop's reference year.
#[derive(Clone, Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize))]
#[cfg_attr(feature = "serde", serde(transparent))]
pub struct ReferenceTemperatureClimatology(Vec<ReferenceTemperature>);

impl ReferenceTemperatureClimatology {
    /// Returns the 365 daily reference-temperature pairs.
    #[must_use]
    pub fn as_slice(&self) -> &[ReferenceTemperature] {
        &self.0
    }
}

impl AsRef<[ReferenceTemperature]> for ReferenceTemperatureClimatology {
    fn as_ref(&self) -> &[ReferenceTemperature] {
        self.as_slice()
    }
}

impl TryFrom<Vec<ReferenceTemperature>> for ReferenceTemperatureClimatology {
    type Error = Error;

    fn try_from(temperatures: Vec<ReferenceTemperature>) -> Result<Self, Self::Error> {
        if temperatures.len() != 365 {
            return Err(Error::InvalidSimulationConfiguration(
                "reference temperature climatology must contain 365 days",
            ));
        }
        Ok(Self(temperatures))
    }
}

#[cfg(feature = "serde")]
impl<'de> serde::Deserialize<'de> for ReferenceTemperatureClimatology {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        let temperatures = Vec::<ReferenceTemperature>::deserialize(deserializer)?;
        Self::try_from(temperatures).map_err(serde::de::Error::custom)
    }
}

/// AquaCrop method used to calculate daily growing degree days.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "snake_case"))]
#[repr(i8)]
pub enum GrowingDegreeDayMethod {
    /// Clamp the mean temperature to the base and upper thresholds.
    MeanTemperature = 1,
    /// Clamp both daily extrema to both thresholds before taking their mean.
    ClampBothExtrema = 2,
    /// Clamp `Tmax` to both thresholds and `Tmin` only to the upper threshold.
    #[default]
    ClampUpperTemperature = 3,
}

/// Builds the source model's daily reference-temperature climatology.
///
/// `first_day_number` is the absolute AquaCrop day number of the first
/// observation. Every `(minimum, maximum)` pair is in degrees Celsius and
/// represents the next consecutive calendar day. The returned vector contains
/// 365 daily pairs for the reference year 1901.
///
/// # Errors
///
/// Returns [`Error`] when the series is empty, its day range overflows, or a
/// calendar date cannot be decoded.
pub fn reference_temperature_climatology<T: TemperatureExtrema>(
    first_day_number: i32,
    temperatures: &[T],
) -> Result<ReferenceTemperatureClimatology, Error> {
    let last_offset = i32::try_from(temperatures.len().saturating_sub(1))
        .map_err(|_| Error::InvalidSimulationConfiguration("temperature series is too long"))?;
    let last_day_number =
        first_day_number
            .checked_add(last_offset)
            .ok_or(Error::InvalidSimulationConfiguration(
                "temperature day range overflows",
            ))?;
    let first_date = CalendarDate::from_day_number(first_day_number)?;
    let last_date = CalendarDate::from_day_number(last_day_number)?;
    if temperatures.is_empty() {
        return Err(Error::InvalidSimulationConfiguration(
            "reference temperature series cannot be empty",
        ));
    }

    let year_count = last_date.year() - first_date.year() + 1;
    let mut month_year_counts = [year_count; 12];
    if year_count > 1 {
        for count in &mut month_year_counts[..usize::try_from(first_date.month() - 1).unwrap_or(0)]
        {
            *count -= 1;
        }
        for count in &mut month_year_counts[usize::try_from(last_date.month()).unwrap_or(12)..] {
            *count -= 1;
        }
    }
    let mut monthly_means = [(0.0, 0.0); 12];
    let mut index = 0_usize;
    while index < temperatures.len() {
        let offset = i32::try_from(index)
            .map_err(|_| Error::InvalidSimulationConfiguration("temperature series is too long"))?;
        let date = CalendarDate::from_day_number(first_day_number + offset)?;
        let month_index = usize::try_from(date.month() - 1)
            .map_err(|_| Error::InvalidSimulationConfiguration("invalid temperature month"))?;
        let mut minimum_sum = 0.0;
        let mut maximum_sum = 0.0;
        let mut days = 0_i32;
        while index < temperatures.len() {
            let inner_offset = i32::try_from(index).map_err(|_| {
                Error::InvalidSimulationConfiguration("temperature series is too long")
            })?;
            let inner_date = CalendarDate::from_day_number(first_day_number + inner_offset)?;
            if inner_date.year() != date.year() || inner_date.month() != date.month() {
                break;
            }
            let daily = &temperatures[index];
            minimum_sum += daily.minimum();
            maximum_sum += daily.maximum();
            days += 1;
            index += 1;
        }
        let divisor = f64::from(days * month_year_counts[month_index]);
        monthly_means[month_index].0 += minimum_sum / divisor;
        monthly_means[month_index].1 += maximum_sum / divisor;
    }
    for pair in &mut monthly_means {
        pair.0 = f64::from(round_to_hundredth_single(pair.0));
        pair.1 = f64::from(round_to_hundredth_single(pair.1));
    }
    interpolate_reference_year(&monthly_means)?.try_into()
}

fn interpolate_reference_year(
    monthly_means: &[(f64, f64); 12],
) -> Result<Vec<ReferenceTemperature>, Error> {
    let mut result = Vec::with_capacity(365);
    for month in 1..=12 {
        let previous = if month == 1 { 11 } else { month - 2 };
        let current = month - 1;
        let next = if month == 12 { 0 } else { month };
        let minimum = interpolation_parameters(
            monthly_means[previous].0 * 30.0,
            monthly_means[current].0 * 30.0,
            monthly_means[next].0 * 30.0,
        );
        let maximum = interpolation_parameters(
            monthly_means[previous].1 * 30.0,
            monthly_means[current].1 * 30.0,
            monthly_means[next].1 * 30.0,
        );
        let day_count = days_in_month(1901, i32::try_from(month).unwrap_or(12))?;
        let mut start = 30_i32;
        for _ in 0..day_count {
            let end = start + 1;
            result.push(ReferenceTemperature {
                minimum: f64::from(round_to_hundredth_single(integrated_parabola(
                    minimum, start, end,
                ))),
                maximum: f64::from(round_to_hundredth_single(integrated_parabola(
                    maximum, start, end,
                ))),
            });
            start = end;
        }
    }
    Ok(result)
}

#[expect(
    clippy::cast_precision_loss,
    reason = "the source explicitly converts the rounded integer to single precision"
)]
fn round_to_hundredth_single(value: f64) -> f32 {
    roundc_i32(100.0 * value) as f32 / 100.0
}

fn interpolation_parameters(previous: f64, current: f64, next: f64) -> (f64, f64, f64) {
    (
        (previous - 2.0 * current + next) / (6.0 * 30.0_f64.powi(3)),
        (-6.0 * previous + 9.0 * current - 3.0 * next) / (6.0 * 30.0_f64.powi(2)),
        (11.0 * previous - 7.0 * current + 2.0 * next) / (6.0 * 30.0),
    )
}

fn integrated_parabola(parameters: (f64, f64, f64), start: i32, end: i32) -> f64 {
    let (cubic, quadratic, linear) = parameters;
    cubic * f64::from(end.pow(3) - start.pow(3))
        + quadratic * f64::from(end.pow(2) - start.pow(2))
        + linear * f64::from(end - start)
}

/// Calculates daily thermal time in degree Celsius-days.
///
/// This preserves the three branch-specific clipping orders in AquaCrop 7.3.
/// `base_temperature`, `upper_temperature`, `minimum_temperature`, and
/// `maximum_temperature` are all in degrees Celsius. `method` is a
/// dimensionless calculation selector; the result is in degree Celsius-days.
#[must_use]
#[expect(
    clippy::manual_midpoint,
    reason = "the addition-before-division order is required for Fortran compatibility"
)]
pub fn growing_degree_days(
    base_temperature: f64,
    upper_temperature: f64,
    minimum_temperature: f64,
    maximum_temperature: f64,
    method: GrowingDegreeDayMethod,
) -> f64 {
    let average_temperature = match method {
        GrowingDegreeDayMethod::MeanTemperature => {
            let mut average = (maximum_temperature + minimum_temperature) / 2.0;
            if average > upper_temperature {
                average = upper_temperature;
            }
            if average < base_temperature {
                average = base_temperature;
            }
            average
        }
        GrowingDegreeDayMethod::ClampBothExtrema => {
            let mut adjusted_maximum = maximum_temperature;
            if maximum_temperature < base_temperature {
                adjusted_maximum = base_temperature;
            }
            if maximum_temperature > upper_temperature {
                adjusted_maximum = upper_temperature;
            }
            let mut adjusted_minimum = minimum_temperature;
            if minimum_temperature < base_temperature {
                adjusted_minimum = base_temperature;
            }
            if minimum_temperature > upper_temperature {
                adjusted_minimum = upper_temperature;
            }
            (adjusted_maximum + adjusted_minimum) / 2.0
        }
        GrowingDegreeDayMethod::ClampUpperTemperature => {
            let mut adjusted_maximum = maximum_temperature;
            if maximum_temperature < base_temperature {
                adjusted_maximum = base_temperature;
            }
            if maximum_temperature > upper_temperature {
                adjusted_maximum = upper_temperature;
            }
            let mut adjusted_minimum = minimum_temperature;
            if minimum_temperature > upper_temperature {
                adjusted_minimum = upper_temperature;
            }
            let mut average = (adjusted_maximum + adjusted_minimum) / 2.0;
            if average < base_temperature {
                average = base_temperature;
            }
            average
        }
    };

    average_temperature - base_temperature
}

#[cfg(test)]
mod tests {
    use assertables::{assert_eq_f64, assert_eq_f64_as_result};

    use super::*;

    #[test]
    fn constant_daily_record_generates_constant_reference_year() {
        let temperatures = vec![(10.0, 20.0); 365];
        let reference = reference_temperature_climatology(1, &temperatures)
            .expect("a complete daily record should produce a reference climatology");
        let reference = reference.as_ref();

        assert!(
            reference.len() == 365
                && reference
                    .iter()
                    .all(
                        |value| assert_eq_f64_as_result!(value.minimum, 10.0_f64).is_ok()
                            && assert_eq_f64_as_result!(value.maximum, 20.0_f64).is_ok()
                    )
        );
    }

    #[test]
    fn reference_climatology_rejects_nonannual_input() {
        let result = ReferenceTemperatureClimatology::try_from(Vec::new());

        assert!(result.is_err());
    }

    #[test]
    #[allow(
        clippy::float_cmp,
        reason = "the pinned formulas produce exact integer values"
    )]
    fn methods_preserve_their_distinct_lower_temperature_adjustments() {
        let calculate = |method| growing_degree_days(10.0, 30.0, -10.0, 20.0, method);

        assert_eq!(
            [
                calculate(GrowingDegreeDayMethod::MeanTemperature),
                calculate(GrowingDegreeDayMethod::ClampBothExtrema),
                calculate(GrowingDegreeDayMethod::ClampUpperTemperature),
            ],
            [0.0, 5.0, 0.0]
        );
    }

    #[test]
    #[allow(clippy::float_cmp, reason = "zero is the exact lower clamp result")]
    fn all_methods_return_zero_below_base_temperature() {
        for method in [
            GrowingDegreeDayMethod::MeanTemperature,
            GrowingDegreeDayMethod::ClampBothExtrema,
            GrowingDegreeDayMethod::ClampUpperTemperature,
        ] {
            assert_eq!(growing_degree_days(10.0, 30.0, 2.0, 8.0, method), 0.0);
        }
    }

    #[test]
    fn carbon_dioxide_interpolates_missing_year() {
        let series = AtmosphericCo2 {
            observations: Cow::Owned(vec![
                Co2Observation {
                    year: 2000,
                    concentration: 369.0,
                },
                Co2Observation {
                    year: 2002,
                    concentration: 373.0,
                },
            ]),
        };
        let result = series
            .concentration_for_year(2001)
            .expect("the interpolated year should have a concentration");

        assert_eq_f64!(result, 371.0_f64);
    }

    #[test]
    fn carbon_dioxide_clamps_after_last_record() {
        let series = AtmosphericCo2 {
            observations: Cow::Owned(vec![Co2Observation {
                year: 2002,
                concentration: 373.0,
            }]),
        };
        let result = series
            .concentration_for_year(2100)
            .expect("a year after the series should clamp to the last concentration");

        assert_eq_f64!(result, 373.0_f64);
    }
}
