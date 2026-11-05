use std::fs;
use std::path::Path;
use std::str::{FromStr, Lines};

use super::sentinel::{NEGATIVE_NINE_I32_SENTINEL, NegativeNineSentinel};
use crate::crop::{
    AirTemperatureCriterion, AssimilateTransfer, Crop, CropCanopy, CropCycle, CropDevelopment,
    CropEvapotranspiration, CropFertilityStress, CropKind, CropParameters, CropProductivity,
    CropRooting, CropSalinityStress, CropTemperature, CropWaterStress, DepletionAdjustment,
    DevelopmentCalendar, PartialDevelopmentCalendar, PerennialEnd, PerennialOnset, PerennialPeriod,
    PlantingMethod,
};
use crate::{AquaCropVersion, Error};

use super::InputError;

/// Reads an AquaCrop `.CRO` file into user-facing crop configuration.
///
/// # Errors
///
/// Returns [`Error`] when the file cannot be read or decoded.
pub fn read_crop_file(path: impl AsRef<Path>) -> Result<Crop, Error> {
    parse_crop_file(&fs::read_to_string(path).map_err(InputError::from)?)
}

/// Parses an AquaCrop `.CRO` file into user-facing crop configuration.
///
/// # Errors
///
/// Returns [`Error`] when a required field is absent, malformed, or outside its integer type.
#[expect(
    clippy::too_many_lines,
    reason = "the sequential parser deliberately mirrors the versioned Fortran file layout"
)]
pub fn parse_crop_file(input: &str) -> Result<Crop, Error> {
    let mut lines = CropLines::new(input);
    lines.text("crop description")?;
    let version_text = lines.token("AquaCrop version")?;
    let version =
        AquaCropVersion::from_str(version_text).map_err(|_| InputError::InvalidValue {
            line: 2,
            field: "AquaCrop version",
            value: version_text.to_owned(),
        })?;
    let version_code = i32::from(version.major) * 10 + i32::from(version.minor);
    lines.skip("file protection flag")?;

    let kind = match lines.parse::<i32>("crop kind")? {
        1 => CropKind::Vegetative,
        2 => CropKind::Grain,
        3 => CropKind::Tuber,
        4 => CropKind::Forage,
        value => return Err(lines.invalid("crop kind", &value).into()),
    };
    let planting_method = match lines.parse::<i32>("planting method")? {
        0 => PlantingMethod::Transplant,
        -9 => PlantingMethod::Regrowth,
        _ => PlantingMethod::Seed,
    };
    let uses_growing_degree_days = lines.parse::<i32>("crop-cycle mode")? == 0;
    let depletion_adjustment = match lines.parse::<i32>("depletion adjustment")? {
        0 => DepletionAdjustment::None,
        1 => DepletionAdjustment::Fao,
        value => return Err(lines.invalid("depletion adjustment", &value).into()),
    };

    let base = lines.parse("base temperature")?;
    let upper = lines.parse("upper temperature")?;
    lines.parse::<i32>("declared total GDD")?;

    let water_stress = CropWaterStress {
        canopy_expansion_upper: lines.parse("upper canopy-expansion threshold")?,
        canopy_expansion_lower: lines.parse("lower canopy-expansion threshold")?,
        canopy_expansion_shape: lines.parse("canopy-expansion shape")?,
        stomatal_closure_upper: lines.parse("stomatal threshold")?,
        stomatal_closure_shape: lines.parse("stomatal shape")?,
        senescence_upper: lines.parse("senescence threshold")?,
        senescence_shape: lines.parse("senescence shape")?,
        eto_delay_senescence: lines.parse("ETo delay before wilting")?,
        pollination_upper: lines
            .parse::<f64>("pollination threshold")?
            .without_negative_nine(),
        anaerobic_point_volume: lines.parse("anaerobic point")?,
    };

    let calibration_stress = lines.parse("fertility calibration stress")?;
    let canopy_growth_shape = lines.parse("fertility canopy-growth shape")?;
    let maximum_canopy_shape = lines.parse("fertility maximum-canopy shape")?;
    let water_productivity_shape = lines.parse("fertility water-productivity shape")?;
    let canopy_decline_shape = lines.parse("fertility canopy-decline shape")?;
    let premature_end_input = lines.parse::<i32>("premature end day")?;
    let premature_end_day = if version_code <= 72 {
        None
    } else {
        premature_end_input.without_negative_nine()
    };
    let fertility_stress = CropFertilityStress {
        calibration_stress,
        canopy_growth_shape,
        maximum_canopy_shape,
        water_productivity_shape,
        canopy_decline_shape,
    };

    let pollination_minimum = optional_i8(lines.parse("cold-pollination threshold")?);
    let pollination_maximum = optional_i8(lines.parse("heat-pollination threshold")?);
    let full_transpiration_gdd = lines
        .parse::<f64>("minimum GDD for full transpiration")?
        .without_negative_nine();
    let temperature = CropTemperature {
        base,
        upper,
        pollination_minimum,
        pollination_maximum,
        full_transpiration_gdd,
    };

    let (ece_minimum, ece_maximum) = if version_code < 32 {
        (Some(2), Some(15))
    } else {
        let minimum = lines.parse("minimum salinity threshold")?;
        let maximum = lines.parse("maximum salinity threshold")?;
        lines.skip("obsolete salinity shape")?;
        (optional_i8(minimum), optional_i8(maximum))
    };
    let (canopy_distortion, stomatal_response) = if version_code < 51 {
        (25, 100)
    } else {
        (
            lines.parse("salinity canopy distortion")?,
            lines.parse("salinity stomatal response")?,
        )
    };
    let salinity_stress = CropSalinityStress {
        ece_minimum,
        ece_maximum,
        canopy_distortion,
        stomatal_response,
    };

    let kc_top = lines.parse("maximum crop coefficient")?;
    let kc_decline_input = lines.parse("crop-coefficient decline")?;
    let kc_decline_cumulative = if version_code <= 72 {
        11.0
    } else {
        kc_decline_input
    };
    let minimum_depth_input = lines.parse("minimum rooting depth")?;
    let maximum_depth = lines.parse::<f64>("maximum rooting depth")?;
    let minimum_depth = f64::min(minimum_depth_input, maximum_depth);
    let shape = lines.parse("root shape")?;
    let extraction_top_quarter = lines.parse("top-quarter root extraction")?;
    let extraction_bottom_quarter = lines.parse("bottom-quarter root extraction")?;
    let late_season_evaporation_reduction = lines.parse("late-season evaporation reduction")?;

    let seedling_size = lines.parse("seedling canopy size")?;
    let regrowth_plant_size = if version_code < 50 {
        seedling_size
    } else {
        lines.parse("regrowth canopy size")?
    };
    let planting_density = lines.parse("planting density")?;
    let growth_coefficient_per_day = lines.parse("daily canopy-growth coefficient")?;
    let self_thinning_years = optional_i8(lines.parse("self-thinning years")?);
    let self_thinning_shape = lines
        .parse::<f64>("self-thinning shape")?
        .without_negative_nine();
    lines.skip("obsolete canopy parameter")?;
    let maximum_cover = lines.parse("maximum canopy cover")?;
    let decline_coefficient_per_day = lines.parse("daily canopy-decline coefficient")?;

    let days_to_emergence = lines.parse("days to emergence")?;
    let days_to_maximum_rooting = lines.parse("days to maximum rooting")?;
    let days_to_senescence = lines.parse("days to senescence")?;
    let days_to_maturity = lines.parse("days to maturity")?;
    let mut days_to_flowering = lines.parse("days to flowering")?;
    let mut flowering_length_days = lines.parse("flowering length in days")?;
    let is_leaf_or_forage = matches!(&kind, CropKind::Vegetative | CropKind::Forage);
    if is_leaf_or_forage {
        days_to_flowering = 0;
        flowering_length_days = 0;
    }
    let determinacy_linked_to_flowering = lines.parse::<i32>("determinacy linkage")? == 1;
    let potential_excess_input = lines.parse::<i32>("potential excess fruits")?;
    let potential_excess_fruits = if is_leaf_or_forage {
        None
    } else {
        potential_excess_input
            .without_negative_nine()
            .map(|value| {
                i16::try_from(value).map_err(|_| lines.invalid("potential excess fruits", &value))
            })
            .transpose()?
    };
    let days_to_harvest_index = lines
        .parse::<i32>("days to harvest-index buildup")?
        .without_negative_nine();

    let normalized_water_productivity = lines.parse("normalized water productivity")?;
    let yield_formation_water_productivity = lines.parse("yield-formation water productivity")?;
    let elevated_co2_adaptation = if version_code < 32 {
        50
    } else {
        lines.parse("elevated-CO2 adaptation")?
    };
    let mut reference_harvest_index = lines.parse("reference harvest index")?;
    let pre_flowering_harvest_index_increase =
        optional_i8(lines.parse("pre-flowering harvest-index increase")?);
    let vegetative_growth_impact_coefficient = lines
        .parse::<f64>("vegetative-growth harvest-index coefficient")?
        .without_negative_nine();
    let stomatal_closure_impact_coefficient = lines
        .parse::<f64>("stomatal harvest-index coefficient")?
        .without_negative_nine();
    let maximum_harvest_index_increase =
        optional_i8(lines.parse("maximum harvest-index increase")?);
    if version_code == 30
        && is_leaf_or_forage
        && reference_harvest_index == NEGATIVE_NINE_I32_SENTINEL
    {
        reference_harvest_index = 85;
    }
    let productivity = CropProductivity {
        normalized_water_productivity,
        yield_formation_water_productivity,
        elevated_co2_adaptation,
        reference_harvest_index,
        pre_flowering_harvest_index_increase,
        vegetative_growth_impact_coefficient,
        stomatal_closure_impact_coefficient,
        maximum_harvest_index_increase,
    };

    let gdd_to_emergence = lines.parse("GDD to emergence")?;
    let gdd_to_maximum_rooting = lines.parse("GDD to maximum rooting")?;
    let gdd_to_senescence = lines.parse("GDD to senescence")?;
    let gdd_to_maturity = lines.parse("GDD to maturity")?;
    let mut gdd_to_flowering = lines.parse("GDD to flowering")?;
    let mut flowering_length_gdd = lines.parse("flowering length in GDD")?;
    let growth_coefficient_per_gdd = lines.parse("GDD canopy-growth coefficient")?;
    let decline_coefficient_per_gdd = lines.parse("GDD canopy-decline coefficient")?;
    let gdd_to_harvest_index = lines.parse::<i32>("GDD to harvest-index buildup")?;
    if uses_growing_degree_days && is_leaf_or_forage {
        gdd_to_flowering = 0;
        flowering_length_gdd = 0;
    }
    let gdd_to_harvest_index = gdd_to_harvest_index.without_negative_nine();
    let dry_matter = if version_code < 62 {
        None
    } else {
        optional_i8(lines.parse("yield dry matter")?)
    };

    let (first_year_minimum_depth, sown_in_first_year, assimilate_transfer) = if version_code < 62 {
        (
            minimum_depth,
            planting_method == PlantingMethod::Seed,
            AssimilateTransfer {
                enabled: false,
                storage_period: 0,
                stored: 0,
                mobilized: 0,
            },
        )
    } else {
        let first_year_minimum_depth = lines.parse("first-year minimum rooting depth")?;
        let sown_in_first_year = lines.parse::<i32>("first-year planting method")? == 1;
        let enabled = lines.parse::<i32>("assimilate transfer flag")? == 1;
        let storage_period = lines.parse("assimilate storage period")?;
        let stored = lines.parse("assimilates stored")?;
        let mobilized = lines.parse("assimilates mobilized")?;
        (
            first_year_minimum_depth,
            sown_in_first_year,
            AssimilateTransfer {
                enabled,
                storage_period,
                stored,
                mobilized,
            },
        )
    };

    let perennial_period = if kind == CropKind::Forage {
        lines.skip("internal calendar spacer")?;
        lines.skip("internal calendar title")?;
        lines.skip("internal calendar separator")?;
        Some(parse_perennial_period(&mut lines)?)
    } else {
        None
    };

    let calendar_days = DevelopmentCalendar {
        emergence: days_to_emergence,
        maximum_rooting: days_to_maximum_rooting,
        senescence: days_to_senescence,
        maturity: days_to_maturity,
        flowering: days_to_flowering,
        flowering_length: flowering_length_days,
        harvest_index: days_to_harvest_index,
        canopy_growth_coefficient: growth_coefficient_per_day,
        canopy_decline_coefficient: decline_coefficient_per_day,
    };
    let cycle = if uses_growing_degree_days {
        let calendar_days = PartialDevelopmentCalendar {
            emergence: days_to_emergence.without_negative_nine(),
            maximum_rooting: days_to_maximum_rooting.without_negative_nine(),
            senescence: days_to_senescence.without_negative_nine(),
            maturity: days_to_maturity.without_negative_nine(),
            flowering: days_to_flowering.without_negative_nine(),
            flowering_length: flowering_length_days.without_negative_nine(),
            harvest_index: days_to_harvest_index,
            canopy_growth_coefficient: growth_coefficient_per_day.without_negative_nine(),
            canopy_decline_coefficient: decline_coefficient_per_day.without_negative_nine(),
        };
        let gdd = DevelopmentCalendar {
            emergence: gdd_to_emergence,
            maximum_rooting: gdd_to_maximum_rooting,
            senescence: gdd_to_senescence,
            maturity: gdd_to_maturity,
            flowering: gdd_to_flowering,
            flowering_length: flowering_length_gdd,
            harvest_index: gdd_to_harvest_index,
            canopy_growth_coefficient: growth_coefficient_per_gdd,
            canopy_decline_coefficient: decline_coefficient_per_gdd,
        };
        if let Ok(calendar_days) = calendar_days.clone().try_into_complete() {
            CropCycle::GrowingDegreeDays { calendar_days, gdd }
        } else {
            CropCycle::UnresolvedGrowingDegreeDays { calendar_days, gdd }
        }
    } else {
        CropCycle::CalendarDays { calendar_days }
    };

    Ok(Crop {
        cycle,
        parameters: CropParameters {
            kind,
            planting_method,
            depletion_adjustment,
            temperature,
            water_stress,
            fertility_stress,
            salinity_stress,
            rooting: CropRooting {
                minimum_depth,
                maximum_depth,
                shape,
                extraction_top_quarter,
                extraction_bottom_quarter,
                first_year_minimum_depth,
                sown_in_first_year,
            },
            assimilate_transfer,
            evapotranspiration: CropEvapotranspiration {
                kc_top,
                kc_decline_cumulative,
                late_season_evaporation_reduction,
            },
            canopy: CropCanopy {
                seedling_size,
                regrowth_plant_size,
                planting_density,
                self_thinning_years,
                self_thinning_shape,
                maximum_cover,
            },
            development: CropDevelopment {
                determinacy_linked_to_flowering,
                potential_excess_fruits,
                dry_matter,
            },
            productivity,
            premature_end_day,
            perennial_period,
        },
    })
}

fn optional_i8(value: i8) -> Option<i8> {
    (i32::from(value) != NEGATIVE_NINE_I32_SENTINEL).then_some(value)
}

fn parse_perennial_period(lines: &mut CropLines<'_>) -> Result<PerennialPeriod, Error> {
    let onset_code = lines.parse("perennial onset criterion")?;
    let onset = PerennialOnset {
        criterion: match onset_code {
            12 => Some(AirTemperatureCriterion::MeanTemperature),
            13 => Some(AirTemperatureCriterion::GrowingDegreeDays),
            _ => None,
        },
        first_day: lines.parse("perennial onset first day")?,
        first_month: lines.parse("perennial onset first month")?,
        search_length: lines.parse("perennial onset search length")?,
        threshold: lines.parse("perennial onset threshold")?,
        successive: lines.parse("perennial onset successive days")?,
        occurrences: lines.parse::<i8>("perennial onset occurrences")?.min(3),
    };
    let end_code = lines.parse("perennial end criterion")?;
    let end = PerennialEnd {
        criterion: match end_code {
            62 => Some(AirTemperatureCriterion::MeanTemperature),
            63 => Some(AirTemperatureCriterion::GrowingDegreeDays),
            _ => None,
        },
        last_day: lines.parse("perennial end last day")?,
        last_month: lines.parse("perennial end last month")?,
        extra_years: lines.parse("perennial end extra years")?,
        search_length: lines.parse("perennial end search length")?,
        threshold: lines.parse("perennial end threshold")?,
        successive: lines.parse("perennial end successive days")?,
        occurrences: lines.parse::<i8>("perennial end occurrences")?.min(3),
    };
    Ok(PerennialPeriod { onset, end })
}

struct CropLines<'a> {
    lines: Lines<'a>,
    line_number: usize,
}

impl<'a> CropLines<'a> {
    fn new(input: &'a str) -> Self {
        Self {
            lines: input.lines(),
            line_number: 0,
        }
    }

    fn text(&mut self, field: &'static str) -> Result<&'a str, InputError> {
        self.line_number += 1;
        self.lines.next().ok_or(InputError::MissingLine {
            line: self.line_number,
            field,
        })
    }

    fn token(&mut self, field: &'static str) -> Result<&'a str, InputError> {
        let line = self.text(field)?;
        let token = line
            .split_whitespace()
            .next()
            .ok_or(InputError::MissingLine {
                line: self.line_number,
                field,
            })?;
        Ok(token)
    }

    fn parse<T>(&mut self, field: &'static str) -> Result<T, InputError>
    where
        T: FromStr,
    {
        let token = self.token(field)?;
        token.parse().map_err(|_| InputError::InvalidValue {
            line: self.line_number,
            field,
            value: token.to_owned(),
        })
    }

    fn skip(&mut self, field: &'static str) -> Result<(), InputError> {
        self.text(field).map(drop)
    }

    fn invalid(&self, field: &'static str, value: &impl ToString) -> InputError {
        InputError::InvalidValue {
            line: self.line_number,
            field,
            value: value.to_string(),
        }
    }
}
