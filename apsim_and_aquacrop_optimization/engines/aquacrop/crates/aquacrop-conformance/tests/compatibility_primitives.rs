//! Public compatibility checks against the pinned AquaCrop 7.3 implementation.

use aquacrop_conformance::TestResult;
use aquacrop_core::calendar::CalendarDate;
use aquacrop_core::climate::{GrowingDegreeDayMethod, growing_degree_days};
use aquacrop_core::compat::{roundc_i8, roundc_i32};

#[test]
fn roundc_matches_pinned_half_to_even_examples() {
    assert_eq!(
        [
            roundc_i32(1.5),
            roundc_i32(2.5),
            roundc_i32(-1.5),
            roundc_i32(-2.5)
        ],
        [2, 2, -2, -2]
    );
}

#[test]
fn roundc_matches_pinned_integer_clipping() {
    assert_eq!(
        [
            roundc_i32(f64::MAX),
            roundc_i32(f64::MIN),
            i32::from(roundc_i8(128.0))
        ],
        [i32::MAX, i32::MIN, i32::from(i8::MAX)]
    );
}

#[test]
fn calendar_matches_pinned_1901_epoch() -> TestResult {
    let actual = CalendarDate::from_day_number(1)?;
    let expected = CalendarDate::new(1901, 1, 1)?;
    assert_eq!(actual, expected);

    Ok(())
}

#[test]
fn calendar_matches_pinned_leap_day_conversion() -> TestResult {
    let actual = CalendarDate::from_day_number(1_155)?;
    let expected = CalendarDate::new(1904, 2, 29)?;
    assert_eq!(actual, expected);

    Ok(())
}

#[test]
#[allow(
    clippy::float_cmp,
    reason = "the pinned formulas produce exact integer values"
)]
fn gdd_methods_match_pinned_branch_results() {
    let calculate = |method| growing_degree_days(10.0, 30.0, -10.0, 20.0, method);

    assert_eq!(
        [
            calculate(GrowingDegreeDayMethod::MeanTemperature),
            calculate(GrowingDegreeDayMethod::ClampBothExtrema),
            calculate(GrowingDegreeDayMethod::ClampUpperTemperature),
        ],
        [0.0, 5.0, 0.0]
    );
}
