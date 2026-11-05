#![forbid(unsafe_code)]

//! Basic Model Interface (BMI) implementation for AquaCrop.
//!
//! This crate wraps [`aquacrop_core::engine::Simulation`] with BMI lifecycle and variable
//! dispatch. `aquacrop-bmi-py` provides the `PyO3` binding.

pub mod config;
pub mod engine;
pub mod error;
pub mod registry;

pub use engine::BmiModel;
pub use error::BmiError;
