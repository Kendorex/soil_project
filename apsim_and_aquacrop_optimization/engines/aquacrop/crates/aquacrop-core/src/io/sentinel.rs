//! AquaCrop missing-value encoding at the file compatibility boundary.

pub(super) const NEGATIVE_NINE_I32_SENTINEL: i32 = -9;
pub(super) const NEGATIVE_NINE_F64_SENTINEL: f64 = -9.0;
pub(super) const NEGATIVE_NINE_POINT_NINE_F64_SENTINEL: f64 = -9.9;

pub(super) trait NegativeNineSentinel: Sized {
    fn without_negative_nine(self) -> Option<Self>;
}

impl NegativeNineSentinel for i32 {
    fn without_negative_nine(self) -> Option<Self> {
        (self != NEGATIVE_NINE_I32_SENTINEL).then_some(self)
    }
}

impl NegativeNineSentinel for f64 {
    fn without_negative_nine(self) -> Option<Self> {
        (self != NEGATIVE_NINE_F64_SENTINEL).then_some(self)
    }
}

#[cfg(test)]
mod tests {
    use assertables::assert_eq_f64_as_result;

    use super::*;

    #[test]
    fn file_sentinels_decode_to_absent_values() {
        assert!(NEGATIVE_NINE_F64_SENTINEL.without_negative_nine().is_none());
        assert!(NEGATIVE_NINE_I32_SENTINEL.without_negative_nine().is_none());
    }

    #[test]
    fn defined_file_values_are_preserved() {
        assert!(
            3.25_f64
                .without_negative_nine()
                .is_some_and(|value| { assert_eq_f64_as_result!(value, 3.25_f64).is_ok() })
        );
        assert!(
            4_i32
                .without_negative_nine()
                .is_some_and(|value| value == 4)
        );
    }
}
