//! Storage and mobilization of perennial forage assimilates.

use crate::compat::roundc_i32;
use crate::crop::AssimilateTransfer;
use crate::stress::stress_coefficient;

/// Assimilate-transfer state retained across daily and seasonal steps.
#[derive(Debug)]
pub struct AssimilateTransferState {
    /// Whether end-of-season storage is active.
    pub storage_active: bool,
    /// Whether stored biomass is being mobilized into new growth.
    pub mobilization_active: bool,
    /// Stored biomass selected for mobilization this season, in tonnes per hectare.
    pub biomass_to_mobilize: f64,
    /// Biomass already mobilized this season, in tonnes per hectare.
    pub biomass_mobilized: f64,
    /// Biomass stored during the current storage period, in tonnes per hectare.
    pub biomass_stored: f64,
}

/// Daily crop state used to select the transferred fraction.
#[derive(Debug)]
pub struct AssimilateTransferInputs {
    /// Current virtual crop day, starting at one.
    pub virtual_day: i32,
    /// Last day of the crop cycle.
    pub maturity_day: i32,
    /// Actual green canopy cover as a soil-cover fraction.
    pub canopy_cover: f64,
    /// Total maximum canopy cover as a soil-cover fraction.
    pub maximum_canopy_cover: f64,
    /// Reduction of maximum cover by stress, in percent.
    pub maximum_canopy_reduction: f64,
    /// Canopy cover retained after cutting, as a soil-cover fraction.
    pub cutting_canopy_cover: f64,
    /// Whether a cutting occurred on the preceding day.
    pub cutting_previous_day: bool,
    /// Whether the crop has ended permanently.
    pub no_more_crop: bool,
}

/// Biomass moved into or out of above-ground biomass on one day.
#[derive(Debug)]
pub struct AssimilateFlux {
    /// Biomass mobilized from root storage, in tonnes per hectare.
    pub into_biomass: f64,
    /// Biomass stored in roots, in tonnes per hectare.
    pub out_of_biomass: f64,
    /// Fraction of today's assimilate production selected for transfer.
    pub fraction: f64,
}

impl AssimilateTransferState {
    /// Creates an inactive transfer state with no stored biomass.
    #[must_use]
    pub const fn new() -> Self {
        Self {
            storage_active: false,
            mobilization_active: false,
            biomass_to_mobilize: 0.0,
            biomass_mobilized: 0.0,
            biomass_stored: 0.0,
        }
    }

    /// Initializes transfer state for a linked perennial growing season.
    ///
    /// `season_year` is the one-based cultivation-season count and
    /// `stored_previous_season` is biomass stored in the immediately
    /// preceding season, in tonnes per hectare. Parameter fields retain their
    /// documented units.
    #[must_use]
    pub fn for_linked_season(
        parameters: &AssimilateTransfer,
        season_year: i32,
        stored_previous_season: f64,
    ) -> Self {
        let first_perennial_season_factor = if season_year == 2 { 0.2 } else { 1.0 };
        let biomass_to_mobilize = if parameters.enabled && season_year > 1 {
            stored_previous_season * first_perennial_season_factor * f64::from(parameters.mobilized)
                / 100.0
        } else {
            0.0
        };
        Self {
            storage_active: false,
            mobilization_active: roundc_i32(1_000.0 * biomass_to_mobilize) > 0,
            biomass_to_mobilize,
            biomass_mobilized: 0.0,
            biomass_stored: 0.0,
        }
    }
}

impl Default for AssimilateTransferState {
    fn default() -> Self {
        Self::new()
    }
}

/// Selects and applies the daily perennial assimilate transfer.
///
/// `daily_biomass_without_transfer` and both returned biomass fluxes
/// are in tonnes per hectare. State, parameter, and input fields retain their
/// field-documented units; the returned fraction is dimensionless.
pub fn transfer_assimilates(
    state: &mut AssimilateTransferState,
    parameters: &AssimilateTransfer,
    inputs: &AssimilateTransferInputs,
    daily_biomass_without_transfer: f64,
) -> AssimilateFlux {
    if !parameters.enabled || inputs.no_more_crop {
        state.storage_active = false;
        state.mobilization_active = false;
        return AssimilateFlux {
            into_biomass: 0.0,
            out_of_biomass: 0.0,
            fraction: 0.0,
        };
    }
    if inputs.virtual_day == inputs.maturity_day - parameters.storage_period + 1 {
        state.storage_active = true;
        if state.mobilization_active {
            state.biomass_to_mobilize = state.biomass_mobilized;
        }
        state.mobilization_active = false;
    }

    let mut fraction = 0.0;
    let mut into_biomass = 0.0;
    let mut out_of_biomass = 0.0;
    if state.mobilization_active && state.biomass_to_mobilize > state.biomass_mobilized {
        let remaining_fraction =
            (state.biomass_to_mobilize - state.biomass_mobilized) / state.biomass_to_mobilize;
        fraction = ((-5.0 * remaining_fraction).exp() - 1.0) / ((-5.0_f64).exp() - 1.0);
        let adjusted_maximum =
            inputs.maximum_canopy_cover * (1.0 - inputs.maximum_canopy_reduction / 100.0);
        if inputs.canopy_cover > 0.9 * adjusted_maximum {
            fraction *= (adjusted_maximum - inputs.canopy_cover) / (0.1 * adjusted_maximum);
        }
        fraction = fraction.max(0.0);
        into_biomass = fraction * daily_biomass_without_transfer;
        if state.biomass_mobilized + into_biomass > state.biomass_to_mobilize {
            into_biomass = state.biomass_to_mobilize - state.biomass_mobilized;
        }
        state.biomass_mobilized += into_biomass;
        if roundc_i32(1_000.0 * state.biomass_to_mobilize)
            <= roundc_i32(1_000.0 * state.biomass_mobilized)
        {
            state.mobilization_active = false;
        }
    }

    if state.storage_active && parameters.storage_period > 0 {
        let storage_canopy_fraction = if inputs.cutting_previous_day {
            0.0
        } else {
            let adjusted_maximum =
                inputs.maximum_canopy_cover * (1.0 - inputs.maximum_canopy_reduction / 100.0);
            if inputs.canopy_cover > inputs.cutting_canopy_cover
                && inputs.canopy_cover < adjusted_maximum
            {
                (inputs.canopy_cover - inputs.cutting_canopy_cover)
                    / (adjusted_maximum - inputs.cutting_canopy_cover)
            } else {
                1.0
            }
        };
        let storage_time =
            f64::from(inputs.virtual_day - (inputs.maturity_day - parameters.storage_period))
                / f64::from(parameters.storage_period);
        fraction = storage_canopy_fraction
            * (f64::from(parameters.stored) / 100.0)
            * (1.0 - stress_coefficient(storage_time, 0.0, 1.0, -5.0));
        fraction = fraction.clamp(0.0, 1.0);
        out_of_biomass = fraction * daily_biomass_without_transfer;
        state.biomass_stored += out_of_biomass;
    }

    AssimilateFlux {
        into_biomass,
        out_of_biomass,
        fraction,
    }
}

#[cfg(test)]
mod tests {
    use super::{AssimilateTransferInputs, AssimilateTransferState, transfer_assimilates};
    use crate::crop::AssimilateTransfer;

    fn parameters() -> AssimilateTransfer {
        AssimilateTransfer {
            enabled: true,
            storage_period: 100,
            stored: 65,
            mobilized: 60,
        }
    }

    #[test]
    fn final_storage_day_uses_the_configured_fraction() {
        let mut state = AssimilateTransferState::new();
        state.storage_active = true;
        let flux = transfer_assimilates(
            &mut state,
            &parameters(),
            &AssimilateTransferInputs {
                virtual_day: 180,
                maturity_day: 180,
                canopy_cover: 0.95,
                maximum_canopy_cover: 0.95,
                maximum_canopy_reduction: 0.0,
                cutting_canopy_cover: 0.3,
                cutting_previous_day: false,
                no_more_crop: false,
            },
            0.2,
        );

        assert!((flux.out_of_biomass - 0.13).abs() < 1.0e-12);
    }

    #[test]
    fn day_after_cutting_suppresses_assimilate_storage() {
        let mut state = AssimilateTransferState::new();
        state.storage_active = true;
        let flux = transfer_assimilates(
            &mut state,
            &parameters(),
            &AssimilateTransferInputs {
                virtual_day: 180,
                maturity_day: 180,
                canopy_cover: 0.95,
                maximum_canopy_cover: 0.95,
                maximum_canopy_reduction: 0.0,
                cutting_canopy_cover: 0.3,
                cutting_previous_day: true,
                no_more_crop: false,
            },
            0.2,
        );

        assert!(flux.out_of_biomass.abs() < f64::EPSILON);
    }

    #[test]
    fn second_season_mobilizes_twenty_percent_of_the_selected_store() {
        let state = AssimilateTransferState::for_linked_season(&parameters(), 2, 1.0);
        assert!((state.biomass_to_mobilize - 0.12).abs() < 1.0e-12);
        assert!(state.mobilization_active);
    }

    #[test]
    fn mobilization_cannot_exceed_selected_storage() {
        let mut state = AssimilateTransferState::new();
        state.mobilization_active = true;
        state.biomass_to_mobilize = 0.05;
        let flux = transfer_assimilates(
            &mut state,
            &parameters(),
            &AssimilateTransferInputs {
                virtual_day: 1,
                maturity_day: 180,
                canopy_cover: 0.1,
                maximum_canopy_cover: 0.95,
                maximum_canopy_reduction: 0.0,
                cutting_canopy_cover: 0.3,
                cutting_previous_day: false,
                no_more_crop: false,
            },
            0.2,
        );

        assert!((flux.into_biomass - 0.05).abs() < 1.0e-12);
        assert!(!state.mobilization_active);
    }
}
