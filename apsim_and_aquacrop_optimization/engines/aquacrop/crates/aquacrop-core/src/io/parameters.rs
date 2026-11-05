use std::fs;
use std::path::Path;
use std::str::FromStr;

use crate::climate::GrowingDegreeDayMethod;
use crate::parameters::{EffectiveRainfallMethod, SimulationParameters};
use crate::{Error, io::InputError};

use super::parse::{parse_token, required_line};

const PARAMETER_COUNT: usize = 25;

/// Numerical controls decoded from an AquaCrop program-parameter file.
#[derive(Debug)]
pub struct AquaCropProgramParameters {
    /// Filesystem-independent simulation controls.
    pub simulation: SimulationParameters,
    /// GDD convention applied when adapting the crop to a season.
    pub gdd_method: GrowingDegreeDayMethod,
}

impl std::ops::Deref for AquaCropProgramParameters {
    type Target = SimulationParameters;

    fn deref(&self) -> &Self::Target {
        &self.simulation
    }
}

impl std::ops::DerefMut for AquaCropProgramParameters {
    fn deref_mut(&mut self) -> &mut Self::Target {
        &mut self.simulation
    }
}

/// Reads an AquaCrop `.PPn` program-parameter file.
///
/// # Errors
///
/// Returns [`Error`] when the file cannot be read or a required value is invalid.
pub fn read_program_parameters(path: impl AsRef<Path>) -> Result<AquaCropProgramParameters, Error> {
    parse_program_parameters(&fs::read_to_string(path).map_err(InputError::from)?)
}

/// Parses an AquaCrop `.PPn` program-parameter file.
///
/// # Errors
///
/// Returns [`Error`] when a required value is missing or invalid.
pub fn parse_program_parameters(input: &str) -> Result<AquaCropProgramParameters, Error> {
    let lines: Vec<_> = input.lines().collect();
    required_line(&lines, PARAMETER_COUNT, "program parameters")?;
    let gdd_code: i8 = value(&lines, 20, "GDD method")?;
    let gdd_code = gdd_code.clamp(1, 3);
    let gdd_method = match gdd_code {
        1 => GrowingDegreeDayMethod::MeanTemperature,
        2 => GrowingDegreeDayMethod::ClampBothExtrema,
        _ => GrowingDegreeDayMethod::ClampUpperTemperature,
    };
    let effective_rainfall_code: i8 = value(&lines, 21, "effective rainfall method")?;
    let effective_rainfall_method = match effective_rainfall_code {
        0 => EffectiveRainfallMethod::Full,
        1 => EffectiveRainfallMethod::UsdaSoilConservationService,
        2 => EffectiveRainfallMethod::Percentage,
        code => {
            return Err(InputError::InvalidValue {
                line: 22,
                field: "effective rainfall method",
                value: code.to_string(),
            }
            .into());
        }
    };
    let antecedent_moisture_code: i8 = value(&lines, 14, "antecedent moisture correction")?;
    Ok(AquaCropProgramParameters {
        simulation: SimulationParameters {
            evaporation_decline_factor: value(&lines, 0, "evaporation decline factor")?,
            wet_bare_soil_coefficient: value(&lines, 1, "wet bare-soil coefficient")?,
            minimum_canopy_for_harvest_index: value(&lines, 2, "harvest-index canopy threshold")?,
            initial_root_depth: value(&lines, 3, "initial root depth")?,
            maximum_root_expansion: 5.0,
            root_stress_shape: value(&lines, 5, "root stress shape")?,
            germination_water: value(&lines, 6, "germination water")?,
            depletion_et_adjustment_factor: value(&lines, 7, "depletion ET adjustment")?,
            deficient_aeration_delay: value(&lines, 8, "deficient aeration days")?,
            senescence_exponent: value(&lines, 9, "senescence exponent")?,
            early_senescence_threshold_reduction: value(
                &lines,
                10,
                "senescence threshold reduction",
            )?,
            upper_soil_depth: value(&lines, 11, "upper soil depth")?,
            maximum_evaporation_depth: value(&lines, 12, "maximum evaporation depth")?,
            runoff_depth: value(&lines, 13, "runoff depth")?,
            antecedent_moisture_correction: antecedent_moisture_code == 1,
            salt_diffusion: value(&lines, 15, "salt diffusion")?,
            salt_solubility: value(&lines, 16, "salt solubility")?,
            capillary_rise_shape: value(&lines, 17, "capillary rise shape")?,
            default_minimum_temperature: value(&lines, 18, "default minimum temperature")?,
            default_maximum_temperature: value(&lines, 19, "default maximum temperature")?,
            gdd_method,
            effective_rainfall_method,
            effective_rainfall: value(&lines, 22, "effective rainfall percent")?,
            showers_per_decade: value(&lines, 23, "showers per decade")?,
            aggregated_rainfall_evaporation_reduction: value(
                &lines,
                24,
                "rainfall evaporation reduction",
            )?,
        },
        gdd_method,
    })
}

fn value<T>(lines: &[&str], index: usize, field: &'static str) -> Result<T, InputError>
where
    T: FromStr,
{
    parse_token(lines[index], index + 1, field)
}
