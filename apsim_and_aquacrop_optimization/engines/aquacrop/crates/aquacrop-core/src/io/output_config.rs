use std::fs;
use std::io::ErrorKind;
use std::path::Path;

use crate::Error;
use crate::output::OutputAggregation;

use super::InputError;

/// One daily result group selectable in `SIMUL/DailyResults.SIM`.
#[derive(Debug)]
pub enum DailyOutputSection {
    /// Soil-water-balance fields.
    WaterBalance,
    /// Crop development and production fields.
    PreparedCrop,
    /// Profile and root-zone soil-water fields.
    SoilWater,
    /// Profile and root-zone salinity fields.
    Salinity,
    /// Water content at compartment nodes.
    CompartmentWater,
    /// Saturated-extract conductivity at compartment nodes.
    CompartmentSalinity,
    /// Daily climate input fields.
    Climate,
    /// Irrigation-event and interval report.
    Irrigation,
}

/// Daily result groups requested by `SIMUL/DailyResults.SIM`.
#[derive(Debug)]
pub struct DailyOutputSelection {
    selected: [bool; 8],
}

impl DailyOutputSelection {
    /// Returns whether `section` was requested.
    #[must_use]
    pub fn contains(&self, section: &DailyOutputSection) -> bool {
        self.selected[daily_index(section)]
    }
}

/// One additional result file selectable in `SIMUL/ParticularResults.SIM`.
#[derive(Debug)]
pub enum ParticularOutputSection {
    /// Biomass and yield at multiple forage cuttings.
    Harvests,
    /// Comparison with field observations.
    Evaluation,
}

/// Additional result files requested by `SIMUL/ParticularResults.SIM`.
#[derive(Debug)]
pub struct ParticularOutputSelection {
    selected: [bool; 2],
}

impl ParticularOutputSelection {
    /// Returns whether `section` was requested.
    #[must_use]
    pub fn contains(&self, section: &ParticularOutputSection) -> bool {
        self.selected[particular_index(section)]
    }
}

/// Output configuration loaded from the standard `SIMUL` directory.
#[derive(Debug)]
pub struct OutputRequests {
    /// Intermediate reporting frequency, or `None` for seasonal results only.
    pub aggregation: Option<OutputAggregation>,
    /// Requested daily result groups.
    pub daily: DailyOutputSelection,
    /// Requested additional result files.
    pub particular: ParticularOutputSelection,
}

/// Reads the standard AquaCrop output-selection files below `root/SIMUL`.
///
/// Missing selection files disable their corresponding outputs, matching the
/// reference executable. The files contain dimensionless integer selection codes.
///
/// # Errors
///
/// Returns [`Error`] when an existing selection file cannot be read.
pub fn read_output_requests(root: impl AsRef<Path>) -> Result<OutputRequests, Error> {
    let simul = root.as_ref().join("SIMUL");
    let aggregation = read_optional(simul.join("AggregationResults.SIM"))?
        .as_deref()
        .and_then(parse_aggregation);
    let daily_codes = read_optional(simul.join("DailyResults.SIM"))?
        .as_deref()
        .map_or([false; 9], selected_codes);
    let particular_codes = read_optional(simul.join("ParticularResults.SIM"))?
        .as_deref()
        .map_or([false; 9], selected_codes);

    Ok(OutputRequests {
        aggregation,
        daily: DailyOutputSelection {
            selected: [
                daily_codes[1],
                daily_codes[2],
                daily_codes[3],
                daily_codes[4],
                daily_codes[5],
                daily_codes[6],
                daily_codes[7],
                daily_codes[8],
            ],
        },
        particular: ParticularOutputSelection {
            selected: [particular_codes[1], particular_codes[2]],
        },
    })
}

fn read_optional(path: impl AsRef<Path>) -> Result<Option<String>, Error> {
    match fs::read_to_string(path) {
        Ok(value) => Ok(Some(value)),
        Err(error) if error.kind() == ErrorKind::NotFound => Ok(None),
        Err(error) => Err(InputError::Io(error).into()),
    }
}

fn parse_aggregation(input: &str) -> Option<OutputAggregation> {
    match first_selection_code(input) {
        Some(1) => Some(OutputAggregation::Daily),
        Some(2) => Some(OutputAggregation::TenDaily),
        Some(3) => Some(OutputAggregation::Monthly),
        _ => None,
    }
}

fn selected_codes(input: &str) -> [bool; 9] {
    let mut selected = [false; 9];
    for code in input.lines().filter_map(selection_code) {
        if let Some(value) = selected.get_mut(code) {
            *value = true;
        }
    }
    selected
}

fn first_selection_code(input: &str) -> Option<usize> {
    input.lines().find_map(selection_code)
}

fn selection_code(line: &str) -> Option<usize> {
    line.trim_start()
        .chars()
        .next()?
        .to_digit(10)
        .and_then(|value| usize::try_from(value).ok())
}

const fn daily_index(section: &DailyOutputSection) -> usize {
    match section {
        DailyOutputSection::WaterBalance => 0,
        DailyOutputSection::PreparedCrop => 1,
        DailyOutputSection::SoilWater => 2,
        DailyOutputSection::Salinity => 3,
        DailyOutputSection::CompartmentWater => 4,
        DailyOutputSection::CompartmentSalinity => 5,
        DailyOutputSection::Climate => 6,
        DailyOutputSection::Irrigation => 7,
    }
}

const fn particular_index(section: &ParticularOutputSection) -> usize {
    match section {
        ParticularOutputSection::Harvests => 0,
        ParticularOutputSection::Evaluation => 1,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn daily_selection_uses_the_first_nonblank_character_of_each_line() {
        let selected = selected_codes("description\n 1 : water\n7 : climate\n 8 : irrigation\n");

        assert!(selected[1]);
        assert!(selected[7]);
        assert!(selected[8]);
        assert!(!selected[2]);
    }

    #[test]
    fn zero_aggregation_selects_seasonal_output_only() {
        assert!(parse_aggregation("0 : seasonal\n").is_none());
    }
}
