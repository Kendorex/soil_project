//! Beginning- and end-of-day water and salt balance accounting.

use crate::Error;
use crate::salinity::total_salt;
use crate::soil::{PreparedSoilProfile, SoilCompartment};
use crate::water_balance::WaterBalanceError;

/// Profile and surface storage recorded before daily processes begin.
#[derive(Debug)]
pub struct DailyBalanceSnapshot {
    /// Water stored in soil compartments, in millimetres.
    pub profile_water: f64,
    /// Water stored on the soil surface, in millimetres.
    pub surface_water: f64,
    /// Dissolved plus deposited soil salt, in g/m2.
    pub profile_salt: f64,
}

/// External daily fluxes required to close the water and salt balances.
#[derive(Debug)]
pub struct DailyBalanceFluxes {
    /// Precipitation, in millimetres.
    pub rain: f64,
    /// Applied irrigation, in millimetres.
    pub irrigation: f64,
    /// Drainage leaving the profile, in millimetres.
    pub drainage: f64,
    /// Surface runoff, in millimetres.
    pub runoff: f64,
    /// Actual soil and surface evaporation, in millimetres.
    pub evaporation: f64,
    /// Actual crop transpiration, in millimetres.
    pub transpiration: f64,
    /// Water added by capillary rise, in millimetres.
    pub capillary_rise: f64,
    /// Horizontal water added to the profile, in millimetres.
    pub horizontal_water: f64,
    /// Surface water remaining at the end of the day, in millimetres.
    pub ending_surface_water: f64,
    /// Salt entering with infiltrated irrigation and surface storage, in g/m2.
    pub infiltrated_salt: f64,
    /// Salt leaving in bottom drainage, in g/m2.
    pub drained_salt: f64,
    /// Salt entering with capillary rise, in g/m2.
    pub capillary_salt: f64,
    /// Horizontal salt added to the profile, in g/m2.
    pub horizontal_salt: f64,
}

/// End-of-day stores and residual balance errors.
#[derive(Debug)]
pub struct DailyBalance {
    /// Water stored in soil compartments at day end, in millimetres.
    pub ending_profile_water: f64,
    /// Salt stored in soil compartments at day end, in g/m2.
    pub ending_profile_salt: f64,
    /// Unclosed water balance, in millimetres.
    pub water_error: f64,
    /// Unclosed salt balance, in g/m2.
    pub salt_error: f64,
}

/// Records beginning-of-day stores and clears compartment drainage work values.
///
/// `surface_water` and returned water stores are in millimetres. Profile and
/// compartment fields retain their field-documented units; returned salt mass
/// is in grams per square metre.
///
/// # Errors
///
/// Returns [`Error`] when a compartment contains an invalid soil-layer index.
pub fn begin_daily_balance(
    profile: &PreparedSoilProfile,
    compartments: &mut [SoilCompartment],
    surface_water: f64,
) -> Result<DailyBalanceSnapshot, Error> {
    let (profile_water, profile_salt) = profile_stores(profile, compartments)?;
    for compartment in compartments {
        compartment.drainage = 0.0;
    }
    Ok(DailyBalanceSnapshot {
        profile_water,
        surface_water,
        profile_salt,
    })
}

/// Closes the daily water and salt balances against their beginning stores.
///
/// Beginning-store and flux fields retain their field-documented units: water
/// values are in millimetres and salt values are in grams per square metre.
/// Returned balance fields use the same units.
///
/// # Errors
///
/// Returns [`Error`] when a compartment contains an invalid soil-layer index.
pub fn end_daily_balance(
    profile: &PreparedSoilProfile,
    compartments: &[SoilCompartment],
    beginning: &DailyBalanceSnapshot,
    fluxes: &DailyBalanceFluxes,
) -> Result<DailyBalance, Error> {
    let (ending_profile_water, ending_profile_salt) = profile_stores(profile, compartments)?;
    let water_error = beginning.profile_water + beginning.surface_water
        - (ending_profile_water
            + fluxes.drainage
            + fluxes.runoff
            + fluxes.evaporation
            + fluxes.transpiration
            + fluxes.ending_surface_water
            - fluxes.rain
            - fluxes.irrigation
            - fluxes.capillary_rise
            - fluxes.horizontal_water);
    let salt_error = beginning.profile_salt - ending_profile_salt + fluxes.infiltrated_salt
        - fluxes.drained_salt
        + fluxes.capillary_salt
        + fluxes.horizontal_salt;
    Ok(DailyBalance {
        ending_profile_water,
        ending_profile_salt,
        water_error,
        salt_error,
    })
}

fn profile_stores(
    profile: &PreparedSoilProfile,
    compartments: &[SoilCompartment],
) -> Result<(f64, f64), Error> {
    let mut water = 0.0;
    let mut salt = 0.0;
    for (compartment_index, compartment) in compartments.iter().enumerate() {
        let layer =
            profile
                .layers
                .get(compartment.layer_index)
                .ok_or(WaterBalanceError::MissingLayer {
                    compartment: compartment_index,
                    layer: compartment.layer_index,
                })?;
        water += layer.water_storage(compartment.theta, compartment.thickness);
        salt += total_salt(compartment, layer);
    }
    Ok((water, salt))
}
