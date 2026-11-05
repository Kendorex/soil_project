//! Regression coverage for project groundwater-table (`.GWT`) input.

use std::fs;
use std::path::{Path, PathBuf};

use aquacrop_conformance::{OfficialTestcase, SUPPLEMENTARY_TESTDATA_ROOT};
use aquacrop_core::io::run_project_with_outputs;

#[test]
fn shallow_constant_water_table_matches_official_daily_output()
-> Result<(), Box<dyn std::error::Error>> {
    // Supplementary files extend the official testcase without duplicating its inputs.
    let testcase = OfficialTestcase::new();
    let official = testcase.source_root();
    let supplementary = Path::new(SUPPLEMENTARY_TESTDATA_ROOT);
    let temporary = tempfile::tempdir()?;
    copy_directory(&official.join("DATA"), &temporary.path().join("DATA"))?;
    copy_directory(&official.join("LIST"), &temporary.path().join("LIST"))?;
    copy_directory(&official.join("PARAM"), &temporary.path().join("PARAM"))?;
    copy_directory(&official.join("SIMUL"), &temporary.path().join("SIMUL"))?;
    copy_directory(&supplementary.join("DATA"), &temporary.path().join("DATA"))?;
    copy_directory(&supplementary.join("LIST"), &temporary.path().join("LIST"))?;
    copy_directory(
        &supplementary.join("PARAM"),
        &temporary.path().join("PARAM"),
    )?;
    fs::create_dir(temporary.path().join("OUTP"))?;

    run_project_with_outputs(temporary.path(), "LIST/ShallowGwt.PRM")?;

    for filename in [
        "ShallowGwtPRMday.OUT",
        "ShallowGwtPRMseason.OUT",
        "ShallowGwtPRMharvests.OUT",
    ] {
        let generated = fs::read_to_string(temporary.path().join("OUTP").join(filename))?;
        let expected = fs::read_to_string(supplementary.join("OUTP_REF").join(filename))?;
        let generated_lines: Vec<_> = generated.lines().skip(1).collect();
        let expected_lines: Vec<_> = expected.lines().skip(1).collect();
        assert_eq!(
            generated_lines.len(),
            expected_lines.len(),
            "{filename} has a different number of rows"
        );
        for (index, (generated, expected)) in
            generated_lines.iter().zip(&expected_lines).enumerate()
        {
            assert_row_matches(generated, expected, filename, index + 2);
        }
    }
    Ok(())
}

/// Compares output rows with a numeric tolerance of 0.15.
///
/// Non-numeric fields must match exactly.
fn assert_row_matches(generated: &str, expected: &str, filename: &str, line_number: usize) {
    const TOLERANCE: f64 = 0.15;
    let generated_tokens: Vec<_> = generated.split_whitespace().collect();
    let expected_tokens: Vec<_> = expected.split_whitespace().collect();
    assert_eq!(
        generated_tokens.len(),
        expected_tokens.len(),
        "{filename} line {line_number} has a different field count"
    );
    for (column, (generated_token, expected_token)) in
        generated_tokens.iter().zip(&expected_tokens).enumerate()
    {
        match (
            generated_token.parse::<f64>(),
            expected_token.parse::<f64>(),
        ) {
            (Ok(generated_value), Ok(expected_value)) => {
                assert!(
                    (generated_value - expected_value).abs() <= TOLERANCE,
                    "{filename} line {line_number} column {column}: {generated_token} vs {expected_token}"
                );
            }
            _ => assert_eq!(
                generated_token, expected_token,
                "{filename} line {line_number} column {column}"
            ),
        }
    }
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
