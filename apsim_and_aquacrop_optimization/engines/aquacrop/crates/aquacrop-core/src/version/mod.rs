use std::fmt::{self, Display, Formatter};
use std::str::FromStr;

use crate::Error;

pub use generated::{
    AQUACROP_COMPATIBLE_VERSION, AQUACROP_REFERENCE_COMMIT, AQUACROP_REFERENCE_TAG,
};

mod generated;

/// A version declared by an AquaCrop input file.
#[derive(Debug, PartialEq)]
pub struct AquaCropVersion {
    /// Major version component.
    pub major: u16,
    /// Minor version component.
    pub minor: u16,
}

impl AquaCropVersion {
    /// Creates a version from its dimensionless major and minor components.
    #[must_use]
    pub const fn new(major: u16, minor: u16) -> Self {
        Self { major, minor }
    }

    /// Returns the AquaCrop model version reproduced by this release.
    #[must_use]
    pub const fn compatible() -> Self {
        Self::new(
            generated::AQUACROP_COMPATIBLE_VERSION_MAJOR,
            generated::AQUACROP_COMPATIBLE_VERSION_MINOR,
        )
    }
}

impl Default for AquaCropVersion {
    fn default() -> Self {
        Self::compatible()
    }
}

impl FromStr for AquaCropVersion {
    type Err = Error;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        let (major, minor) = value
            .trim()
            .split_once('.')
            .ok_or_else(|| VersionError::Invalid(value.to_owned()))?;
        Ok(Self {
            major: major
                .parse()
                .map_err(|_| VersionError::Invalid(value.to_owned()))?,
            minor: minor
                .parse()
                .map_err(|_| VersionError::Invalid(value.to_owned()))?,
        })
    }
}

impl Display for AquaCropVersion {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> fmt::Result {
        write!(formatter, "{}.{}", self.major, self.minor)
    }
}

/// Failure to parse an AquaCrop file version.
#[derive(Debug, thiserror::Error)]
pub enum VersionError {
    /// The version was not a `major.minor` pair of unsigned integers.
    #[error("invalid AquaCrop version {0:?}")]
    Invalid(String),
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn version_parses_file_format_number() {
        let version = "7.3"
            .parse::<AquaCropVersion>()
            .expect("the supported version should parse");
        assert_eq!(version, AquaCropVersion::new(7, 3));
    }

    #[test]
    fn version_rejects_missing_minor_component() {
        assert!("7".parse::<AquaCropVersion>().is_err());
    }

    #[test]
    fn compatible_version_matches_package_metadata() {
        assert_eq!(
            AquaCropVersion::compatible().to_string(),
            AQUACROP_COMPATIBLE_VERSION
        );
    }
}
