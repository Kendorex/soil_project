//! Numerical constants and operations retained for AquaCrop compatibility.

/// Electrical-conductivity conversion used by AquaCrop, in grams per litre per dS/m.
pub const EQUIVALENT_WEIGHT: f64 = 0.64;

/// Reference atmospheric carbon-dioxide concentration, in parts per million.
pub const REFERENCE_CO2: f64 = 369.41;

/// Minimum evaporation extraction depth, in centimetres.
pub const MINIMUM_EVAPORATION_DEPTH_CM: f64 = 15.0;

/// AquaCrop's general-purpose domain threshold for values considered effectively zero.
pub const ZERO_THRESHOLD: f64 = 1.0e-6;

/// Rounds a binary64 value to an `i32` using AquaCrop's round-half-to-even rule.
///
/// `value` and the result retain the caller's unit. Values outside the target
/// range are clipped before rounding, matching AquaCrop 7.3.
#[must_use]
#[expect(
    clippy::cast_possible_truncation,
    reason = "roundc deliberately converts an already clipped integral f64"
)]
pub fn roundc_i32(value: f64) -> i32 {
    roundc(value.clamp(f64::from(i32::MIN), f64::from(i32::MAX))) as i32
}

/// Rounds a binary64 value to an `i8` using AquaCrop's round-half-to-even rule.
///
/// `value` and the result retain the caller's unit. Values outside the target
/// range are clipped before rounding, matching AquaCrop 7.3.
#[must_use]
#[expect(
    clippy::cast_possible_truncation,
    reason = "roundc deliberately converts an already clipped integral f64"
)]
pub fn roundc_i8(value: f64) -> i8 {
    roundc(value.clamp(f64::from(i8::MIN), f64::from(i8::MAX))) as i8
}

/// Removes the fractional part toward zero, matching AquaCrop's `trunc` helper.
///
/// `value` and the result retain the caller's unit.
#[must_use]
#[expect(
    clippy::cast_possible_truncation,
    reason = "trunc deliberately converts the integral part of an f64"
)]
pub fn trunc_i32(value: f64) -> i32 {
    value.trunc() as i32
}

#[expect(
    clippy::cast_possible_truncation,
    reason = "the clipped floor is converted only to inspect its parity"
)]
fn roundc(value: f64) -> f64 {
    let lower = value.floor();
    let is_half = (value - lower - 0.5).abs() < f64::EPSILON;

    if is_half {
        if (lower as i64).unsigned_abs() % 2 == 0 {
            lower
        } else {
            value.ceil()
        }
    } else {
        value.round()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundc_i32_rounds_half_to_even_for_both_signs() {
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
    fn roundc_i32_clips_to_target_range_before_rounding() {
        assert_eq!(
            [roundc_i32(f64::MAX), roundc_i32(f64::MIN)],
            [i32::MAX, i32::MIN]
        );
    }

    #[test]
    fn roundc_i8_clips_to_target_range_before_rounding() {
        assert_eq!([roundc_i8(127.6), roundc_i8(-128.6)], [i8::MAX, i8::MIN]);
    }

    #[test]
    fn trunc_removes_fraction_toward_zero_for_both_signs() {
        assert_eq!([trunc_i32(2.9), trunc_i32(-2.9)], [2, -2]);
    }
}
