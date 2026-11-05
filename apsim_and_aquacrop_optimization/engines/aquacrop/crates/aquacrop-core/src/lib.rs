#![doc = include_str!("../README.md")]
#![forbid(unsafe_code)]

pub mod balance;
pub mod biomass;
pub mod calendar;
pub mod canopy;
pub mod climate;
pub mod compat;
pub mod crop;
pub mod cutting;
pub mod engine;
mod error;
pub mod et;
pub mod evaporation;
pub mod harvest;
#[cfg(feature = "file-io")]
pub mod io;
pub mod irrigation;
pub mod management;
pub mod output;
pub mod parameters;
pub mod perennial;
pub mod rooting;
pub mod salinity;
pub mod soil;
pub mod stress;
pub mod transpiration;
mod version;
pub mod water_balance;

pub use error::Error;
pub use version::{
    AQUACROP_COMPATIBLE_VERSION, AQUACROP_REFERENCE_COMMIT, AQUACROP_REFERENCE_TAG,
    AquaCropVersion, VersionError,
};
