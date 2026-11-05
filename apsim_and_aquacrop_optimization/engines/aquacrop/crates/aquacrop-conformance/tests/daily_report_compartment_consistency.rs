//! Regression coverage for daily-report header and data consistency.

use std::fs;

use aquacrop_conformance::{OfficialTestcase, TestResult};
use aquacrop_core::io::run_project_with_outputs;

#[test]
fn daily_report_header_compartment_count_matches_the_data_for_a_shallow_rooting_crop() -> TestResult
{
    let project = OfficialTestcase::new().copy()?;
    let crop_path = project.working_root().join("DATA/AlfOttawaGDD.CRO");
    let crop = fs::read_to_string(&crop_path)?;
    // Use a shallow crop to exercise the 1.20 m compartment floor.
    let shallow_rooted = crop
        .lines()
        .map(|line| {
            if line.contains("Maximum effective rooting depth (m)") {
                "     1.00      : Maximum effective rooting depth (m)"
            } else {
                line
            }
        })
        .collect::<Vec<_>>()
        .join("\n");
    fs::write(&crop_path, shallow_rooted)?;

    run_project_with_outputs(project.working_root(), "LIST/Ottawa.PRM")?;

    let day_output = fs::read_to_string(project.working_root().join("OUTP/OttawaPRMday.OUT"))?;
    let mut lines = day_output.lines();
    let header_names = lines.nth(3).ok_or("missing header name row")?;
    let mut data_lines = lines.skip(1); // units row
    let first_data_row = data_lines.next().ok_or("missing first data row")?;

    // The header reports the floored profile depth.
    assert!(
        header_names.contains("WC(1.20)"),
        "header must report the floored 1.20 m profile depth, got: {header_names}"
    );
    assert!(
        !header_names.contains("WC(1.00)"),
        "header must not report the raw, unfloored 1.00 m rootmax"
    );

    // Count labels directly because single-digit labels contain a space.
    let header_wc_labels = count_wc_compartment_labels(header_names);
    let data_fields = first_data_row.split_whitespace().count();

    assert_eq!(
        header_wc_labels, 12,
        "header should declare twelve WC compartment labels for a rootmax below the 1.20 m floor"
    );
    assert_eq!(
        data_fields, 98,
        "data row should carry the full 98-field reference layout (12 WC + 12 ECe compartments \
         included), matching the header's declared compartment count"
    );

    Ok(())
}

/// Counts `WC` labels in a daily-output header.
fn count_wc_compartment_labels(header_row: &str) -> usize {
    let bytes = header_row.as_bytes();
    let mut count = 0;
    let mut index = 0;
    while index + 4 <= bytes.len() {
        let is_label = &bytes[index..index + 2] == b"WC"
            && (bytes[index + 2].is_ascii_digit() || bytes[index + 2] == b' ')
            && bytes[index + 3].is_ascii_digit();
        if is_label {
            count += 1;
            index += 4;
        } else {
            index += 1;
        }
    }
    count
}
