//! End-to-end checks for the `aquacrop` executable.

use std::fs;
use std::process::Command;

use aquacrop_conformance::{OfficialTestcase, TestResult};

#[test]
fn default_invocation_generates_the_reference_output_set() -> TestResult {
    let project = OfficialTestcase::new().copy()?;

    let output = Command::new(env!("CARGO_BIN_EXE_aquacrop"))
        .current_dir(project.working_root())
        .output()?;
    assert!(
        output.status.success(),
        "aquacrop failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );

    project.assert_reference_outputs()?;

    Ok(())
}

#[test]
fn root_option_runs_a_directory_without_changing_working_directory() -> TestResult {
    let project = OfficialTestcase::new().copy()?;

    let status = Command::new(env!("CARGO_BIN_EXE_aquacrop"))
        .arg("--root")
        .arg(project.working_root())
        .status()?;

    assert!(status.success());

    Ok(())
}

#[test]
fn missing_project_list_discovers_project_files() -> TestResult {
    let project = OfficialTestcase::new().copy()?;
    let working_root = project.working_root();
    fs::remove_file(working_root.join("LIST/ListProjects.txt"))?;

    let output = Command::new(env!("CARGO_BIN_EXE_aquacrop"))
        .current_dir(working_root)
        .output()?;

    assert!(
        output.status.success(),
        "aquacrop failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    project.assert_reference_output_file_names()?;

    Ok(())
}

#[test]
fn missing_project_directory_returns_failure() -> TestResult {
    let temporary = tempfile::tempdir()?;
    let missing = temporary.path().join("missing");
    let output = Command::new(env!("CARGO_BIN_EXE_aquacrop"))
        .arg("--root")
        .arg(missing)
        .output()?;

    assert!(!output.status.success());
    assert_ne!(output.stderr, Vec::<u8>::new());

    Ok(())
}
