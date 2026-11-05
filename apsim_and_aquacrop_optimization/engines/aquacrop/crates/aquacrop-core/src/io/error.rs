use std::io;

/// Failure to read or parse an AquaCrop input file.
#[derive(Debug, thiserror::Error)]
pub enum InputError {
    /// Filesystem read failed.
    #[error(transparent)]
    Io(#[from] io::Error),
    /// A required line was absent.
    #[error("line {line}: missing {field}")]
    MissingLine {
        /// One-based line number.
        line: usize,
        /// Field expected on that line.
        field: &'static str,
    },
    /// A field could not be decoded.
    #[error("line {line}: invalid {field} {value:?}")]
    InvalidValue {
        /// One-based line number.
        line: usize,
        /// Field being decoded.
        field: &'static str,
        /// Invalid token.
        value: String,
    },
    /// The observation count cannot be represented by the reference implementation.
    #[error("climate file has too many observations: {0}")]
    TooManyObservations(usize),
    /// A repeated fixed-size section ended partway through an item.
    #[error(
        "incomplete {section} item {item}: expected {expected_lines} lines, found {actual_lines}",
        item = complete_items + 1
    )]
    IncompleteSection {
        /// Human-readable section name.
        section: &'static str,
        /// Number of complete items parsed before the incomplete item.
        complete_items: usize,
        /// Expected number of lines in each item.
        expected_lines: usize,
        /// Number of lines available for the incomplete item.
        actual_lines: usize,
    },
}
