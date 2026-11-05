use crate::calendar::CalendarError;
use crate::soil::SoilError;
use crate::version::VersionError;
use crate::water_balance::WaterBalanceError;

#[cfg(feature = "file-io")]
use crate::io::InputError;

/// Error returned by fallible `aquacrop-core` APIs.
#[derive(Debug, thiserror::Error)]
pub enum Error {
    /// An AquaCrop calendar value was invalid.
    #[error(transparent)]
    Calendar(#[from] CalendarError),
    /// Soil-layer parameters violated a physical range required by the model.
    #[error(transparent)]
    Soil(#[from] SoilError),
    /// Crop parameters violated a relationship required by the model.
    #[error("invalid crop configuration: {0}")]
    InvalidCropConfiguration(&'static str),
    /// A soil-water-balance process received invalid configuration or state.
    #[error(transparent)]
    WaterBalance(#[from] WaterBalanceError),
    /// A point-simulation configuration cannot define a valid daily run.
    #[error("invalid simulation configuration: {0}")]
    InvalidSimulationConfiguration(&'static str),
    /// An AquaCrop version could not be decoded.
    #[error(transparent)]
    Version(#[from] VersionError),
    /// A filesystem-based AquaCrop input could not be read or decoded.
    #[cfg(feature = "file-io")]
    #[error(transparent)]
    Input(#[from] InputError),
}
