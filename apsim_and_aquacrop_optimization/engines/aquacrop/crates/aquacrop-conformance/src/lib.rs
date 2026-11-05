#![doc = include_str!("../README.md")]
#![forbid(unsafe_code)]

use std::fs;
use std::path::{Path, PathBuf};

use aquacrop_core::crop::{CropSeason, PreparedCrop};
use aquacrop_core::salinity::total_salt;
use aquacrop_core::soil::{PreparedSoilProfile, SoilCompartment};
use aquacrop_core::{Error, io};
use tempfile::TempDir;

/// A type alias for the result of a conformance test.
pub type TestResult<T = ()> = Result<T, Box<dyn std::error::Error>>;

/// Absolute path to the testcase for the currently supported AquaCrop version.
const TESTCASE_ROOT: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../third-party/KUL-RSDA/AquaCrop/testcase"
);

/// Absolute path to project-owned conformance fixtures.
///
/// Tests overlay these files on a copy of the vendored testcase.
pub const SUPPLEMENTARY_TESTDATA_ROOT: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/testdata");

/// A testcase project configured from a fixture source.
///
/// Call [`Self::copy`] to create a writable temporary copy. The copy contains the input
/// directories required to run a project and an empty `OUTP` directory, keeping the source
/// fixture read-only while a test runs the model.
#[derive(Debug)]
pub struct TestcaseProject<'a> {
    source: &'a Path,
}

impl<'a> TestcaseProject<'a> {
    /// Configures a project from a testcase source directory.
    #[must_use]
    pub fn new<P: AsRef<Path> + ?Sized>(source: &'a P) -> Self {
        Self {
            source: source.as_ref(),
        }
    }

    /// Creates a writable temporary project by copying this project's source fixture.
    ///
    /// # Errors
    ///
    /// Returns an error when the temporary directory or copied fixture files cannot be created.
    pub fn copy(&self) -> std::io::Result<CopiedTestcaseProject<'a>> {
        let temporary = tempfile::tempdir()?;
        copy_project_inputs(self.source, temporary.path())?;

        Ok(CopiedTestcaseProject {
            source: self.source,
            temporary,
        })
    }

    /// Returns the root of the source project.
    #[must_use]
    pub fn source_root(&self) -> &Path {
        self.source
    }
}

/// The single official testcase for the currently supported AquaCrop version.
#[derive(Debug)]
pub struct OfficialTestcase {
    project: TestcaseProject<'static>,
}

impl OfficialTestcase {
    /// Configures the supported official testcase.
    #[must_use]
    pub fn new() -> Self {
        Self {
            project: TestcaseProject::new(TESTCASE_ROOT),
        }
    }

    /// Returns the root of the official testcase.
    #[must_use]
    pub fn source_root(&self) -> &Path {
        self.project.source_root()
    }

    /// Creates a writable temporary copy of the official testcase.
    ///
    /// # Errors
    ///
    /// Returns an error when the temporary directory or copied fixture files cannot be created.
    pub fn copy(&self) -> std::io::Result<CopiedTestcaseProject<'static>> {
        self.project.copy()
    }

    /// Returns the path to the official testcase soil file.
    #[must_use]
    pub fn soil_path(&self) -> PathBuf {
        self.source_root().join("DATA/Ottawa.SOL")
    }

    /// Returns the path to the official testcase GDD crop file.
    #[must_use]
    pub fn gdd_crop_path(&self) -> PathBuf {
        self.source_root().join("DATA/AlfOttawaGDD.CRO")
    }

    /// Returns the path to the official testcase default crop file.
    #[must_use]
    pub fn default_crop_path(&self) -> PathBuf {
        self.source_root().join("SIMUL/DEFAULT.CRO")
    }

    /// Reads and prepares the soil profile from the official testcase.
    ///
    /// # Errors
    ///
    /// Returns [`Error`] when the soil file cannot be read, parsed, or prepared.
    pub fn prepared_soil_profile(&self) -> Result<PreparedSoilProfile, Error> {
        io::read_soil_profile(self.soil_path())?.prepare()
    }

    /// Reads and prepares the GDD crop from the official testcase.
    ///
    /// # Errors
    ///
    /// Returns [`Error`] when the crop file cannot be read, parsed, or prepared.
    pub fn prepared_gdd_crop(&self) -> Result<PreparedCrop, Error> {
        read_prepared_crop(self.gdd_crop_path())
    }
}

impl Default for OfficialTestcase {
    fn default() -> Self {
        Self::new()
    }
}

/// A writable temporary copy of a testcase project.
#[derive(Debug)]
pub struct CopiedTestcaseProject<'a> {
    source: &'a Path,
    temporary: TempDir,
}

impl CopiedTestcaseProject<'_> {
    /// Returns the root of the source project.
    #[must_use]
    pub fn source_root(&self) -> &Path {
        self.source
    }

    /// Returns the writable root of the copied project.
    #[must_use]
    pub fn working_root(&self) -> &Path {
        self.temporary.path()
    }

    /// Compares generated result files with the source fixture's reference output set.
    ///
    /// Comparison ignores the generated first line in ordinary result files because it contains
    /// run-specific status text. Status files are compared in full.
    ///
    /// # Errors
    ///
    /// Returns an error when generated or reference output files cannot be read.
    ///
    /// # Panics
    ///
    /// Panics when the generated output set or compared output content differs from the reference.
    pub fn assert_reference_outputs(&self) -> std::io::Result<()> {
        let generated_directory = self.working_root().join("OUTP");
        let reference_directory = self.source_root().join("OUTP_REF");
        let generated = directory_file_names(&generated_directory)?;
        let expected = directory_file_names(&reference_directory)?;
        assert_eq!(generated, expected, "generated output file set differs");

        for filename in generated {
            let actual = generated_directory.join(&filename);
            let reference = reference_directory.join(&filename);
            if matches!(filename.as_str(), "AllDone.OUT" | "ListProjectsLoaded.OUT") {
                assert_eq!(fs::read_to_string(actual)?, fs::read_to_string(reference)?);
            } else {
                assert_result_body_eq(&filename, &actual, &reference)?;
            }
        }
        Ok(())
    }

    /// Compares generated output file names with the source fixture's reference output set.
    ///
    /// # Errors
    ///
    /// Returns an error when generated or reference output directories cannot be read.
    ///
    /// # Panics
    ///
    /// Panics when the generated output file names differ from the reference set.
    pub fn assert_reference_output_file_names(&self) -> std::io::Result<()> {
        assert_eq!(
            directory_file_names(&self.working_root().join("OUTP"))?,
            directory_file_names(&self.source_root().join("OUTP_REF"))?,
            "generated output file set differs"
        );
        Ok(())
    }
}

/// Reads and invariantly prepares a complete crop fixture.
///
/// # Errors
///
/// Returns [`Error`] when the fixture cannot be read, parsed, or prepared.
pub fn read_prepared_crop(path: impl AsRef<Path>) -> Result<PreparedCrop, Error> {
    io::read_crop_file(path)?
        .for_season(CropSeason::default())?
        .prepare()
}

/// Returns the total water stored in a prepared soil profile, in millimetres.
#[must_use]
pub fn profile_water(profile: &PreparedSoilProfile, compartments: &[SoilCompartment]) -> f64 {
    compartments
        .iter()
        .map(|compartment| {
            profile.layers[compartment.layer_index]
                .water_storage(compartment.theta, compartment.thickness)
        })
        .sum()
}

/// Returns the total salt stored in a prepared soil profile, in grams per square metre.
#[must_use]
pub fn profile_salt(profile: &PreparedSoilProfile, compartments: &[SoilCompartment]) -> f64 {
    compartments
        .iter()
        .map(|compartment| total_salt(compartment, &profile.layers[compartment.layer_index]))
        .sum()
}

fn directory_file_names(directory: &Path) -> std::io::Result<Vec<String>> {
    let mut names = fs::read_dir(directory)?
        .map(|entry| Ok(entry?.file_name().to_string_lossy().into_owned()))
        .collect::<std::io::Result<Vec<_>>>()?;
    names.sort();
    Ok(names)
}

fn assert_result_body_eq(filename: &str, generated: &Path, expected: &Path) -> std::io::Result<()> {
    let generated = fs::read_to_string(generated)?;
    let expected = fs::read_to_string(expected)?;
    let generated_lines: Vec<_> = generated.trim().lines().skip(1).collect();
    let expected_lines: Vec<_> = expected.trim().lines().skip(1).collect();
    for (index, (generated, expected)) in generated_lines.iter().zip(&expected_lines).enumerate() {
        assert_eq!(
            *generated,
            *expected,
            "{filename} result line {} differs",
            index + 2
        );
    }
    assert_eq!(
        generated_lines.len(),
        expected_lines.len(),
        "{filename} result line count differs"
    );
    Ok(())
}

fn copy_project_inputs(source: &Path, destination: &Path) -> std::io::Result<()> {
    for entry in fs::read_dir(source)? {
        let entry = entry?;
        let name = entry.file_name();
        if matches!(name.to_str(), Some("OUTP" | "OUTP_REF")) {
            continue;
        }

        let source_path = entry.path();
        let destination_path = destination.join(&name);
        if entry.file_type()?.is_dir() {
            copy_directory(&source_path, &destination_path)?;
        } else {
            fs::create_dir_all(destination)?;
            fs::copy(source_path, destination_path)?;
        }
    }
    fs::create_dir(destination.join("OUTP"))
}

fn copy_directory(source: &Path, destination: &Path) -> std::io::Result<()> {
    fs::create_dir_all(destination)?;
    for entry in fs::read_dir(source)? {
        let entry = entry?;
        let source_path = entry.path();
        let destination_path: PathBuf = destination.join(entry.file_name());
        if entry.file_type()?.is_dir() {
            copy_directory(&source_path, &destination_path)?;
        } else {
            fs::copy(source_path, destination_path)?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn copied_project_preserves_arbitrary_inputs_and_excludes_outputs() -> std::io::Result<()> {
        let source = tempfile::tempdir()?;
        fs::create_dir(source.path().join("custom"))?;
        fs::write(source.path().join("custom/input.txt"), "input")?;
        fs::write(source.path().join("project.txt"), "project")?;
        fs::create_dir(source.path().join("OUTP_REF"))?;
        fs::write(source.path().join("OUTP_REF/reference.out"), "reference")?;
        fs::create_dir(source.path().join("OUTP"))?;
        fs::write(source.path().join("OUTP/generated.out"), "generated")?;

        let project = TestcaseProject::new(source.path()).copy()?;
        let root = project.working_root();

        assert_eq!(fs::read_to_string(root.join("custom/input.txt"))?, "input");
        assert_eq!(fs::read_to_string(root.join("project.txt"))?, "project");
        assert!(root.join("OUTP").is_dir());
        assert!(fs::read_dir(root.join("OUTP"))?.next().is_none());
        assert!(!root.join("OUTP_REF").exists());
        Ok(())
    }
}
