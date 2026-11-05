//! Error type for the BMI engine.

/// Errors returned by [`crate::BmiModel`] operations.
///
/// Variants map to BMI-style failure categories rather than to `aquacrop-core`'s own error
/// hierarchy, since a BMI caller (Python, or a coupled model) needs to distinguish "unknown
/// variable name" from "wrong shape" from "invalid lifecycle call" independent of the underlying
/// numerical cause.
#[derive(Debug, thiserror::Error)]
pub enum BmiError {
    /// The model was already initialized; the reference does not guard against this, but this
    /// wrapper does, since re-running initialization on live state is not a case the reference's
    /// own permissiveness should be copied into a "corrected" implementation without comment.
    #[error("model is already initialized")]
    AlreadyInitialized,
    /// A method that requires initialization was called before `initialize()`.
    #[error("model has not been initialized")]
    NotInitialized,
    /// A method was called after `finalize()`.
    #[error("model has already been finalized")]
    AlreadyFinalized,
    /// `initialize()` was called with a configuration that could not be parsed or assembled.
    #[error("invalid configuration: {0}")]
    InvalidConfiguration(String),
    /// The underlying `aquacrop-core` simulation reported an error.
    #[error("simulation error: {0}")]
    Simulation(#[from] aquacrop_core::Error),
    /// `get_value`/`set_value`/indexed variants were called with a name that does not resolve to
    /// any canonical variable, after alias resolution.
    #[error("unknown variable: {0}")]
    UnknownVariable(String),
    /// `get_value` was called on a variable that has no reference getter (write-only), or
    /// `set_value` was called on a read-only variable.
    /// matching the reference's own get/set asymmetries exactly, not a Rust-side restriction.
    #[error("variable {name} does not support {operation}, matching the reference contract")]
    UnsupportedOperation {
        /// Canonical variable name.
        name: String,
        /// `"get"` or `"set"`.
        operation: &'static str,
    },
    /// A buffer passed to a getter/setter had the wrong length for the variable's current grid
    /// size.
    #[error("wrong shape for {name}: expected {expected} value(s), got {actual}")]
    WrongShape {
        /// Canonical variable name.
        name: String,
        /// Required length.
        expected: usize,
        /// Length actually supplied.
        actual: usize,
    },
    /// An index passed to `get_value_at_indices`/`set_value_at_indices` was out of range for the
    /// variable's current grid size.
    #[error("index {index} out of range for {name} (grid size {size})")]
    IndexOutOfRange {
        /// Canonical variable name.
        name: String,
        /// The offending index.
        index: usize,
        /// Current grid size.
        size: usize,
    },
}
