//! Builds an `aquacrop-core` [`SimulationConfig`] from in-memory AquaCrop files.
//!
//! The parsers use the same file formats as the project runner without accessing the filesystem.

use aquacrop_core::climate::{DailyWeather, GrowingDegreeDayMethod, prepare_calibration_climate};
use aquacrop_core::crop::CropSeason;
use aquacrop_core::engine::{InitialConditions, SimulationConfig};
use aquacrop_core::io::{
    AquaCropProgramParameters, ClimateRecordFrequency, parse_co2_file, parse_crop_file,
    parse_irrigation_file, parse_management_file, parse_program_parameters,
    parse_scalar_climate_file, parse_soil_profile, parse_temperature_file,
};
use aquacrop_core::parameters::SimulationParameters;
use serde::Deserialize;

use crate::error::BmiError;

/// A calendar date, deserialized from a scenario's JSON as `{"year": ..., "month": ..., "day": ...}`.
#[derive(Debug, Clone, Copy, Deserialize)]
pub struct DateInput {
    /// Calendar year.
    pub year: i32,
    /// Calendar month, 1-12.
    pub month: i32,
    /// Calendar day of month.
    pub day: i32,
}

impl DateInput {
    fn day_number(self) -> Result<i32, BmiError> {
        aquacrop_core::calendar::CalendarDate::new(self.year, self.month, self.day)
            .map(aquacrop_core::calendar::CalendarDate::day_number)
            .map_err(|error| {
                BmiError::InvalidConfiguration(format!(
                    "invalid date {}-{}-{}: {error}",
                    self.year, self.month, self.day
                ))
            })
    }
}

/// A complete, self-contained BMI scenario: AquaCrop-format text blocks plus the explicit dates
/// the reference would otherwise take from a `.PRM`/`.PRO` project file.
///
/// Every `*_text` field holds the verbatim content of the corresponding AquaCrop file format
/// (for example `crop_text` is what a `.CRO` file would contain), never a filesystem path. This
/// is the whole of the "in-memory, no file I/O, no temporary directories" contract: the caller
/// (Python, or a coupled model) is responsible for having those contents in memory already.
#[derive(Debug, Deserialize)]
pub struct ScenarioConfig {
    /// One-based cultivation-season count, passed straight through to `SimulationConfig`.
    #[serde(default = "one")]
    pub season_year: i32,
    /// Verbatim `.CRO` crop-file content.
    pub crop_text: String,
    /// Verbatim `.SOL` soil-file content.
    pub soil_text: String,
    /// Verbatim field-management file content, or `None` for `Management::default()`
    /// ("no specific field management").
    #[serde(default)]
    pub management_text: Option<String>,
    /// Verbatim irrigation-file content, or `None` for rainfed cropping with no schedule.
    #[serde(default)]
    pub irrigation_text: Option<String>,
    /// Verbatim program-parameters file content, or `None` for `SimulationParameters::default()`.
    #[serde(default)]
    pub parameters_text: Option<String>,
    /// Verbatim minimum/maximum temperature file content.
    pub temperature_text: String,
    /// Verbatim rainfall file content.
    pub rain_text: String,
    /// Verbatim reference-evapotranspiration file content.
    pub eto_text: String,
    /// Verbatim atmospheric CO2 file content.
    pub co2_text: String,
    /// First simulated day.
    pub simulation_start: DateInput,
    /// Last simulated day, inclusive.
    pub simulation_end: DateInput,
    /// First day of the crop cycle.
    pub crop_start: DateInput,
    /// Last day of the crop cycle, inclusive.
    pub crop_end: DateInput,
}

const fn one() -> i32 {
    1
}

/// Parses a JSON-encoded [`ScenarioConfig`] and assembles it into a valid `aquacrop-core`
/// `SimulationConfig` plus fresh (non-linked) `InitialConditions`, performing no filesystem or
/// network access.
///
/// # Errors
///
/// Returns [`BmiError::InvalidConfiguration`] when the JSON cannot be parsed or any AquaCrop text
/// block fails to parse, and [`BmiError::Simulation`] when `aquacrop-core` itself rejects the
/// assembled configuration (for example non-contiguous climate coverage).
pub fn build_from_json(
    json: &str,
) -> Result<(SimulationConfig, InitialConditions, ConfigSnapshot), BmiError> {
    let scenario: ScenarioConfig = serde_json::from_str(json).map_err(|error| {
        BmiError::InvalidConfiguration(format!("invalid scenario JSON: {error}"))
    })?;
    build(&scenario)
}

/// Assembles an already-parsed [`ScenarioConfig`] into a `SimulationConfig` and fresh
/// `InitialConditions`. See [`build_from_json`] for the JSON entry point.
///
/// # Errors
///
/// See [`build_from_json`].
#[expect(
    clippy::too_many_lines,
    reason = "keeping the file-based runner's assembly recipe visible in one place is intentional, see module docs"
)]
pub fn build(
    scenario: &ScenarioConfig,
) -> Result<(SimulationConfig, InitialConditions, ConfigSnapshot), BmiError> {
    fn invalid(context: &'static str) -> impl Fn(aquacrop_core::Error) -> BmiError {
        move |error| BmiError::InvalidConfiguration(format!("{context}: {error}"))
    }

    let crop = parse_crop_file(&scenario.crop_text).map_err(invalid("crop"))?;
    let soil = parse_soil_profile(&scenario.soil_text).map_err(invalid("soil"))?;
    let management = scenario
        .management_text
        .as_deref()
        .map(parse_management_file)
        .transpose()
        .map_err(invalid("management"))?
        .unwrap_or_default();
    let irrigation = scenario
        .irrigation_text
        .as_deref()
        .map(parse_irrigation_file)
        .transpose()
        .map_err(invalid("irrigation"))?;
    let program_parameters = scenario
        .parameters_text
        .as_deref()
        .map(parse_program_parameters)
        .transpose()
        .map_err(invalid("program parameters"))?
        .unwrap_or_else(|| AquaCropProgramParameters {
            simulation: SimulationParameters::default(),
            gdd_method: GrowingDegreeDayMethod::default(),
        });

    let temperatures =
        parse_temperature_file(&scenario.temperature_text).map_err(invalid("temperature"))?;
    let rain = parse_scalar_climate_file(&scenario.rain_text).map_err(invalid("rain"))?;
    let reference_et =
        parse_scalar_climate_file(&scenario.eto_text).map_err(invalid("reference ET"))?;
    let co2 = parse_co2_file(&scenario.co2_text).map_err(invalid("CO2"))?;

    for (label, frequency) in [
        ("temperature", temperatures.record.frequency),
        ("rain", rain.record.frequency),
        ("reference ET", reference_et.record.frequency),
    ] {
        if frequency != ClimateRecordFrequency::Daily {
            return Err(BmiError::InvalidConfiguration(format!(
                "{label} series must be daily; aggregated (ten-daily/monthly) climate expansion \
                 is not implemented, matching aquacrop-core's file-based runner"
            )));
        }
    }

    let first_day_number = scenario.simulation_start.day_number()?;
    let last_day_number = scenario.simulation_end.day_number()?;
    let first_crop_day_number = scenario.crop_start.day_number()?;
    let last_crop_day_number = scenario.crop_end.day_number()?;

    // Mirror io/runner.rs's load_project_run field-for-field: crop.for_season needs the crop
    // period's own temperature slice, independent of the simulation-wide weather series built
    // below (a GDD-cycle crop's calendar derivation can walk temperatures from
    // first_crop_day_number, distinct from first_day_number).
    let crop_temperature_start =
        usize::try_from(first_crop_day_number - temperatures.record.first_date.day_number())
            .map_err(|_| {
                BmiError::InvalidConfiguration(
                    "temperature series begins after the crop period".to_owned(),
                )
            })?;
    let crop_temperatures = temperatures
        .observations
        .get(crop_temperature_start..)
        .ok_or_else(|| {
            BmiError::InvalidConfiguration(
                "temperature series does not cover the crop period".to_owned(),
            )
        })?;
    let crop = crop
        .for_season(CropSeason::new_with_temperatures(
            program_parameters.gdd_method,
            crop_temperatures,
        ))
        .map_err(invalid("crop"))?;

    let temperature_first = temperatures.record.first_date.day_number();
    let rain_first = rain.record.first_date.day_number();
    let reference_et_first = reference_et.record.first_date.day_number();

    // Reimplements io/climate.rs's align_daily_weather, which is private to aquacrop-core --
    // kept field-for-field identical rather than restructured, per this module's own "mirrors
    // io/runner.rs" contract. Climate coverage extends to the shortest common coverage across
    // the three series, not merely to last_day_number: aquacrop-core's own
    // adjust_gdd_crop_calendar (for GDD-cycle crops) walks the weather series forward from
    // first_crop_day_number looking for each phenology milestone's calendar-day equivalent, and
    // can need days beyond last_day_number to find them. Clipping weather exactly at
    // last_day_number starves that search for exactly the crops (GDD-cycle) most likely to be
    // used with a BMI-style caller. step() itself already stops advancing once a record's
    // day_number exceeds last_day_number, so the extra trailing days are inert once the run
    // logically ends, exactly as they are in a file-based project run sharing one climate series
    // across several linked runs.
    let weather_end = [
        temperatures.record.last_date.day_number(),
        rain.record.last_date.day_number(),
        reference_et.record.last_date.day_number(),
    ]
    .into_iter()
    .min()
    .unwrap_or(last_day_number);

    let carbon_dioxide_for_crop_period = co2
        .series
        .concentration_for_period(first_crop_day_number, last_crop_day_number)
        .map_err(invalid("CO2 period lookup"))?
        .ok_or_else(|| {
            BmiError::InvalidConfiguration("CO2 series has no observations".to_owned())
        })?;

    let mut weather = Vec::with_capacity(
        usize::try_from(weather_end.saturating_sub(first_day_number) + 1).map_err(|_| {
            BmiError::InvalidConfiguration("simulation period is invalid".to_owned())
        })?,
    );
    for day in first_day_number..=weather_end {
        let temperature_index = day_offset(day, temperature_first, "temperature")?;
        let rain_index = day_offset(day, rain_first, "rain")?;
        let reference_et_index = day_offset(day, reference_et_first, "reference ET")?;
        let temperature = temperatures
            .observations
            .get(temperature_index)
            .ok_or_else(|| out_of_range("temperature", day))?;
        let rain_value = rain
            .values
            .get(rain_index)
            .copied()
            .ok_or_else(|| out_of_range("rain", day))?;
        let reference_et_value = reference_et
            .values
            .get(reference_et_index)
            .copied()
            .ok_or_else(|| out_of_range("reference ET", day))?;
        weather.push(DailyWeather {
            minimum_temperature: temperature.0,
            maximum_temperature: temperature.1,
            reference_et: reference_et_value,
            rain: rain_value,
            carbon_dioxide: carbon_dioxide_for_crop_period,
        });
    }

    let calibration_climate = prepare_calibration_climate(
        temperatures.record.first_date,
        &temperatures.observations,
        &co2.series,
    )
    .map_err(invalid("reference climate"))?;

    let snapshot = ConfigSnapshot {
        soil_layer_thicknesses: soil.layers.iter().map(|layer| layer.thickness).collect(),
        initial_compartment_count: soil
            .clone()
            .prepare()
            .map_err(invalid("soil"))?
            .reference_compartments_at_field_capacity_for_depth(
                crop.parameters.rooting.maximum_depth,
            )
            .len(),
        irrigation_method_ordinal: irrigation
            .as_ref()
            .map(|schedule| irrigation_method_ordinal(&schedule.method)),
        bund_height: management.bund_height,
        fertility_stress: management.fertility_stress,
        mulch_cover: management.mulch_cover,
    };

    let config = SimulationConfig {
        season_year: scenario.season_year,
        crop,
        soil,
        management,
        irrigation,
        parameters: program_parameters.simulation,
        weather,
        external_irrigation: None,
        groundwater: None,
        calibration_climate: Some(calibration_climate),
        simulation_start: first_day_number.into(),
        simulation_stop: last_day_number.into(),
        crop_start: first_crop_day_number.into(),
        crop_stop: last_crop_day_number.into(),
    };

    Ok((config, InitialConditions::at_field_capacity(), snapshot))
}

fn day_offset(day: i32, series_first_day: i32, label: &'static str) -> Result<usize, BmiError> {
    usize::try_from(day - series_first_day).map_err(|_| out_of_range(label, day))
}

fn out_of_range(label: &'static str, day: i32) -> BmiError {
    BmiError::InvalidConfiguration(format!(
        "{label} series does not cover simulation day {day}"
    ))
}

/// Facts captured from the parsed configuration before it is consumed by `Simulation::initialize`,
/// needed for BMI read-back paths that `aquacrop-core` does not itself echo into `DailyResult`
/// that are not represented in `SimulationConfig`.
#[derive(Debug, Clone)]
pub struct ConfigSnapshot {
    /// Soil-layer thicknesses in profile order, in metres, for per-layer aggregation
    /// (`soil__water_content_in_layers`, `soil_water_actual_layer-N`).
    pub soil_layer_thicknesses: Vec<f64>,
    /// Number of compartments `Simulation::initialize` will build (recomputed independently here
    /// with the same public `reference_compartments_at_field_capacity_for_depth` call
    /// `Simulation::initialize` itself uses for its `compartments: None` fallback, since
    /// `Simulation` does not expose its compartment count until after the first `step()`; using
    /// the plain `compartments_at_field_capacity_for_depth` here would silently disagree with what
    /// `Simulation::initialize` actually builds, since that method now constructs an unbounded,
    /// uniformly-discretized grid rather than the twelve-compartment reference grid).
    pub initial_compartment_count: usize,
    /// Irrigation method, using the reference's numeric convention (0=Basin, 1=Border, 2=Drip,
    /// 3=Furrow, 4=Sprinkler; see `bmi_aquacrop.f90:723-724`), or `None` if the scenario supplied
    /// no irrigation schedule at all.
    pub irrigation_method_ordinal: Option<f64>,
    /// Initial bund height, in metres, from the parsed management configuration.
    pub bund_height: f64,
    /// Initial fertility-stress level, in percent, from the parsed management configuration.
    pub fertility_stress: i32,
    /// Initial mulch cover, in percent, from the parsed management configuration.
    pub mulch_cover: i8,
}

/// Maps a Rust [`aquacrop_core::irrigation::IrrigationMethod`] to the reference's numeric BMI
/// convention (`bmi_aquacrop.f90:723-724`), which does not match the Rust enum's declaration order.
fn irrigation_method_ordinal(method: &aquacrop_core::irrigation::IrrigationMethod) -> f64 {
    use aquacrop_core::irrigation::IrrigationMethod;
    match method {
        IrrigationMethod::Basin => 0.0,
        IrrigationMethod::Border => 1.0,
        IrrigationMethod::Drip => 2.0,
        IrrigationMethod::Furrow => 3.0,
        IrrigationMethod::Sprinkler => 4.0,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_required_field_is_reported_as_invalid_configuration() {
        let error = build_from_json("{}").expect_err("empty scenario must fail to parse");
        assert!(matches!(error, BmiError::InvalidConfiguration(_)));
    }
}
