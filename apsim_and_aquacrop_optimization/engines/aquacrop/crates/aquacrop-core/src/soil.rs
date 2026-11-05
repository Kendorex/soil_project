//! Soil-profile parameters, compartment state, and water-storage conversions.

use crate::Error;
use crate::compat::{roundc_i8, roundc_i32};

/// Number of soil compartments used by the AquaCrop reference model.
///
/// `aquacrop-core` can construct and simulate larger compartment grids. The
/// reference-compatible automatic grid remains limited to this count.
pub const REFERENCE_SOIL_COMPARTMENT_COUNT: usize = 12;

/// Default compartment thickness, in metres.
pub const DEFAULT_COMPARTMENT_THICKNESS: f64 = 0.10;

/// Number of salinity cells stored in each compartment.
pub const SALT_CELL_COUNT: usize = 11;

/// Texture class used to select capillary-rise parameters.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum SoilClass {
    /// Sandy soil.
    Sandy,
    /// Loamy soil.
    Loamy,
    /// Sandy-clayey soil.
    SandyClayey,
    /// Silty-clayey soil.
    SiltyClayey,
}

/// One homogeneous layer in a soil profile.
#[derive(Clone, Debug)]
pub struct PreparedSoilLayer {
    /// Layer thickness, in metres.
    pub thickness: f64,
    /// Volumetric water content at saturation, in percent.
    pub saturation: f64,
    /// Volumetric water content at field capacity, in percent.
    pub field_capacity: f64,
    /// Volumetric water content at wilting point, in percent.
    pub wilting_point: f64,
    /// Quantized daily drainage coefficient.
    pub drainage_coefficient: f64,
    /// Saturated hydraulic conductivity, in mm/day.
    pub saturated_hydraulic_conductivity: f64,
    /// Root penetrability, in percent.
    pub penetrability: i8,
    /// Gravel content by mass, in percent.
    pub gravel_mass: i8,
    /// Derived gravel content by volume, in percent.
    pub gravel_volume: f64,
    /// Number of regular salinity cells.
    pub salinity_cell_count: i8,
    /// Number of salinity cells including the saturation cell.
    pub salinity_cell_count_with_saturation: i8,
    /// Fine-earth water content at the upper salinity-cell limit, in m3/m3.
    pub salinity_upper_limit: f64,
    /// Width of one regular salinity cell, in m3/m3.
    pub salinity_cell_width: f64,
    /// Salt mobility factors for the eleven reference cells.
    pub salt_mobility: [f64; SALT_CELL_COUNT],
    /// Texture class used by capillary rise.
    pub class: SoilClass,
    /// Capillary-rise regression parameters, when capillary rise is physically available.
    pub capillary_rise_parameters: Option<(f64, f64)>,
}

/// Raw parameters needed to construct and normalize one soil layer.
#[derive(Clone, Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct SoilLayer {
    /// Layer thickness, in metres.
    pub thickness: f64,
    /// Volumetric water content at saturation, in percent.
    pub saturation: f64,
    /// Volumetric water content at field capacity, in percent.
    pub field_capacity: f64,
    /// Volumetric water content at wilting point, in percent.
    pub wilting_point: f64,
    /// Saturated hydraulic conductivity, in mm/day.
    pub saturated_hydraulic_conductivity: f64,
    /// Root penetrability, in percent.
    pub penetrability: i8,
    /// Gravel content by mass, in percent.
    pub gravel_mass: i8,
    /// Stored capillary-rise parameters, or `None` when they must be derived.
    pub capillary_rise_parameters: Option<(f64, f64)>,
}

impl SoilLayer {
    /// Validates the input and derives its hydraulic, gravel, salinity, and texture values.
    ///
    /// Numeric parameter fields retain the units documented on
    /// [`SoilLayer`]; derived layer fields retain their own documented units.
    ///
    /// # Errors
    ///
    /// Returns [`Error`] when a parameter is non-finite or violates the model's physical ranges.
    pub fn prepare(self) -> Result<PreparedSoilLayer, Error> {
        validate_layer_parameters(&self)?;
        let Self {
            thickness,
            saturation,
            field_capacity,
            wilting_point,
            saturated_hydraulic_conductivity,
            penetrability,
            gravel_mass,
            capillary_rise_parameters,
        } = self;
        let gravel_volume = gravel_volume_from_mass(saturation, gravel_mass);
        let drainage_coefficient = drainage_coefficient_from_ksat(saturated_hydraulic_conductivity);
        let salinity_cell_count_with_saturation =
            salinity_cells_from_ksat(saturated_hydraulic_conductivity);
        let salinity_cell_count = salinity_cell_count_with_saturation - 1;
        let macro_porosity = roundc_i8(field_capacity);
        let salinity_upper_limit = (saturation / 100.0)
            * (f64::from(salinity_cell_count) / f64::from(salinity_cell_count + 2));
        let salinity_cell_width = salinity_upper_limit / f64::from(salinity_cell_count);
        let salt_mobility = salt_mobility(
            20,
            macro_porosity,
            salinity_cell_count,
            salinity_cell_count_with_saturation,
            salinity_upper_limit,
        );
        let class = classify_soil(
            saturation,
            field_capacity,
            wilting_point,
            saturated_hydraulic_conductivity,
        );
        let capillary_rise_parameters = capillary_rise_parameters
            .or_else(|| derive_capillary_rise_parameters(&class, saturated_hydraulic_conductivity));

        Ok(PreparedSoilLayer {
            thickness,
            saturation,
            field_capacity,
            wilting_point,
            drainage_coefficient,
            saturated_hydraulic_conductivity,
            penetrability,
            gravel_mass,
            gravel_volume,
            salinity_cell_count,
            salinity_cell_count_with_saturation,
            salinity_upper_limit,
            salinity_cell_width,
            salt_mobility,
            class,
            capillary_rise_parameters,
        })
    }
}

impl PreparedSoilLayer {
    /// Fine-earth fraction after excluding gravel by volume.
    #[must_use]
    pub fn fine_earth_fraction(&self) -> f64 {
        1.0 - self.gravel_volume / 100.0
    }

    /// Converts volumetric water content and depth to stored water, in millimetres.
    ///
    /// `theta` is a volumetric fraction and `depth` is in metres.
    #[must_use]
    pub fn water_storage(&self, theta: f64, depth: f64) -> f64 {
        1_000.0 * theta * depth * self.fine_earth_fraction()
    }

    /// Converts a water-depth change to a volumetric-water-content change.
    ///
    /// `water` is in millimetres and `depth` is in metres; the result is a
    /// volumetric fraction.
    #[must_use]
    pub fn theta_change_from_water(&self, water: f64, depth: f64) -> f64 {
        water / (1_000.0 * depth * self.fine_earth_fraction())
    }
}

/// Failure to construct a physically valid soil layer.
#[derive(Debug, thiserror::Error)]
pub enum SoilError {
    /// A named parameter or relationship is outside its supported range.
    #[error("invalid soil-layer parameters: {0}")]
    InvalidParameters(&'static str),
    /// A soil profile does not contain any layers.
    #[error("soil profile must contain at least one layer")]
    EmptyProfile,
}

fn validate_layer_parameters(parameters: &SoilLayer) -> Result<(), SoilError> {
    let finite_values = [
        parameters.thickness,
        parameters.saturation,
        parameters.field_capacity,
        parameters.wilting_point,
        parameters.saturated_hydraulic_conductivity,
    ];
    if finite_values.iter().any(|value| !value.is_finite()) {
        return Err(SoilError::InvalidParameters("values must be finite"));
    }
    if parameters.thickness <= 0.0 {
        return Err(SoilError::InvalidParameters("thickness must be positive"));
    }
    if !(0.0..=100.0).contains(&parameters.saturation)
        || !(0.0..=parameters.saturation).contains(&parameters.field_capacity)
        || !(0.0..=parameters.field_capacity).contains(&parameters.wilting_point)
    {
        return Err(SoilError::InvalidParameters(
            "water contents must satisfy 0 <= WP <= FC <= SAT <= 100",
        ));
    }
    if parameters.saturated_hydraulic_conductivity < 0.0 {
        return Err(SoilError::InvalidParameters(
            "saturated hydraulic conductivity cannot be negative",
        ));
    }
    if !(0..=100).contains(&parameters.penetrability)
        || !(0..=100).contains(&parameters.gravel_mass)
    {
        return Err(SoilError::InvalidParameters(
            "penetrability and gravel mass must be percentages",
        ));
    }
    if parameters
        .capillary_rise_parameters
        .is_some_and(|(a, b)| !a.is_finite() || !b.is_finite())
    {
        return Err(SoilError::InvalidParameters(
            "capillary-rise parameters must be finite",
        ));
    }
    Ok(())
}

/// User-supplied soil-profile configuration before layer preparation.
#[derive(Clone, Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct SoilProfile {
    /// SCS curve number.
    pub curve_number: i8,
    /// Readily evaporable water in the top layer, in millimetres.
    pub readily_evaporable_water: i8,
    /// Ordered soil-layer parameters from the surface downward.
    pub layers: Vec<SoilLayer>,
}

impl SoilProfile {
    /// Validates and prepares every soil layer.
    ///
    /// # Errors
    ///
    /// Returns [`Error`] when the profile is empty or a layer violates its
    /// physical constraints.
    pub fn prepare(self) -> Result<PreparedSoilProfile, Error> {
        if self.layers.is_empty() {
            return Err(SoilError::EmptyProfile.into());
        }
        let layers = self
            .layers
            .into_iter()
            .map(SoilLayer::prepare)
            .collect::<Result<_, _>>()?;
        Ok(PreparedSoilProfile {
            curve_number: self.curve_number,
            readily_evaporable_water: self.readily_evaporable_water,
            layers,
        })
    }
}

/// Validated soil profile used by numerical processes.
#[derive(Clone, Debug)]
pub struct PreparedSoilProfile {
    /// SCS curve number.
    pub curve_number: i8,
    /// Readily evaporable water in the top layer, in millimetres.
    pub readily_evaporable_water: i8,
    /// Ordered soil layers from the surface downward.
    pub layers: Vec<PreparedSoilLayer>,
}

impl PreparedSoilProfile {
    /// Total declared profile depth, in metres.
    #[must_use]
    pub fn depth(&self) -> f64 {
        self.layers.iter().map(|layer| layer.thickness).sum()
    }

    /// Builds an unbounded compartment grid and initializes it at field capacity.
    ///
    /// Compartments use [`DEFAULT_COMPARTMENT_THICKNESS`], except for a possibly
    /// thinner final compartment. Unlike the AquaCrop reference grid, this grid
    /// adds compartments instead of increasing their thickness when the profile
    /// is deeper than twelve default compartments.
    #[must_use]
    pub fn compartments_at_field_capacity(&self) -> Vec<SoilCompartment> {
        self.compartments_at_field_capacity_for_depth(self.depth())
    }

    /// Builds an unbounded compartment grid for a maximum effective rooting depth.
    ///
    /// AquaCrop may extend the compartment grid below the declared soil-profile
    /// depth when the crop has a deeper unrestricted root zone. Properties of the
    /// deepest soil layer are used for those compartments.
    /// `maximum_rooting_depth` and compartment depths and thicknesses are in metres.
    #[must_use]
    pub fn compartments_at_field_capacity_for_depth(
        &self,
        maximum_rooting_depth: f64,
    ) -> Vec<SoilCompartment> {
        let effective_depth = maximum_rooting_depth + 0.000_001;
        let mut thicknesses = Vec::new();
        let mut total_depth = 0.0;
        while total_depth + 0.000_01 < effective_depth {
            let thickness = (effective_depth - total_depth).min(DEFAULT_COMPARTMENT_THICKNESS);
            thicknesses.push(thickness);
            total_depth += thickness;
        }

        self.compartments_at_field_capacity_with_thicknesses(thicknesses)
    }

    /// Builds the twelve-compartment AquaCrop reference grid for a maximum
    /// effective rooting depth.
    ///
    /// For depths greater than twelve default compartments, the reference
    /// algorithm increases compartment thickness instead of adding compartments.
    /// Use [`Self::compartments_at_field_capacity_for_depth`] when an unbounded,
    /// uniformly discretized grid is preferred over numerical compatibility.
    ///
    /// The returned grid never has fewer than [`REFERENCE_SOIL_COMPARTMENT_COUNT`]
    /// compartments, regardless of how shallow `maximum_rooting_depth` is. Official's
    /// `AdjustSizeCompartments` is only ever called to *grow* a compartment grid that
    /// already starts at the default twelve-compartment/0.10 m span (1.20 m) before any
    /// crop is even considered (`tempprocessing.f90`'s `roundc(CropRootMax) >
    /// roundc(TotDepth)` gate, checked against that pre-existing 1.20 m baseline, never
    /// against the soil profile's own depth). A crop with a shallower rooting depth
    /// than that never triggers growth and simply keeps the 1.20 m floor. Confirmed
    /// against ground truth: official's own `Canola`/`Tomato`/`SugarBeet` runs (each
    /// `rootmax` = 1.00 m) report `WC(1.20)`, twelve compartments, not ten. This is the
    /// single source of truth for that floor. Every caller gets it automatically,
    /// rather than each caller separately remembering to clamp its input depth.
    #[must_use]
    pub fn reference_compartments_at_field_capacity_for_depth(
        &self,
        maximum_rooting_depth: f64,
    ) -> Vec<SoilCompartment> {
        #[expect(
            clippy::cast_precision_loss,
            reason = "the fixed compartment count is exactly representable"
        )]
        let maximum_rooting_depth = maximum_rooting_depth
            .max(REFERENCE_SOIL_COMPARTMENT_COUNT as f64 * DEFAULT_COMPARTMENT_THICKNESS);
        let effective_depth = maximum_rooting_depth + 0.000_001;
        let mut thicknesses = Vec::with_capacity(REFERENCE_SOIL_COMPARTMENT_COUNT);
        let mut total_depth = 0.0;
        while thicknesses.len() < REFERENCE_SOIL_COMPARTMENT_COUNT
            && total_depth + 0.000_01 < effective_depth
        {
            let thickness = (effective_depth - total_depth).min(DEFAULT_COMPARTMENT_THICKNESS);
            thicknesses.push(thickness);
            total_depth += thickness;
        }
        if total_depth + 0.000_01 < effective_depth {
            let addition = (effective_depth / DEFAULT_COMPARTMENT_THICKNESS - 12.0) / 78.0;
            thicknesses.clear();
            for index in 1_u8..=12 {
                let unrounded = DEFAULT_COMPARTMENT_THICKNESS * (1.0 + f64::from(index) * addition);
                thicknesses.push(0.05 * f64::from(roundc_i32(unrounded * 20.0)));
            }
            total_depth = thicknesses.iter().sum();
            while total_depth < effective_depth {
                thicknesses[REFERENCE_SOIL_COMPARTMENT_COUNT - 1] += 0.05;
                total_depth += 0.05;
            }
            while total_depth - 0.049_999_99 >= effective_depth {
                thicknesses[REFERENCE_SOIL_COMPARTMENT_COUNT - 1] -= 0.05;
                total_depth -= 0.05;
            }
        }

        self.compartments_at_field_capacity_with_thicknesses(thicknesses)
    }

    fn compartments_at_field_capacity_with_thicknesses(
        &self,
        thicknesses: Vec<f64>,
    ) -> Vec<SoilCompartment> {
        let mut compartments = Vec::with_capacity(thicknesses.len());
        let mut total_depth = 0.0;
        for thickness in thicknesses {
            let midpoint = total_depth + thickness / 2.0;
            let layer_index = self.layer_index_at_depth(midpoint);
            let layer = &self.layers[layer_index];
            compartments.push(SoilCompartment::at_field_capacity(
                thickness,
                layer_index,
                layer.field_capacity,
            ));
            total_depth += thickness;
        }
        compartments
    }

    fn layer_index_at_depth(&self, depth: f64) -> usize {
        let mut boundary = 0.0;
        for (index, layer) in self.layers.iter().enumerate() {
            boundary += layer.thickness;
            if depth <= boundary {
                return index;
            }
        }
        self.layers.len().saturating_sub(1)
    }
}

/// Mutable state for one horizontal soil compartment.
#[derive(Debug)]
pub struct SoilCompartment {
    /// Compartment thickness, in metres.
    pub thickness: f64,
    /// Volumetric water content as an m3/m3 fraction.
    pub theta: f64,
    /// Drainage through the lower boundary, in mm/day.
    pub drainage: f64,
    /// Zero-based index of the associated soil layer.
    pub layer_index: usize,
    /// Maximum root extraction, in m3/m3/day.
    pub maximum_root_extraction: f64,
    /// Field capacity adjusted for a water table, in volume percent.
    pub adjusted_field_capacity: f64,
    /// Consecutive days under anaerobic conditions.
    pub anaerobic_exposure: i32,
    /// Fraction of the compartment inside the active calculation depth.
    pub weighting_factor: f64,
    /// Locally possible extraction after stress, in m3/m3/day.
    pub major_sink: f64,
    /// Target extraction from the root distribution, in m3/m3/day.
    pub minor_sink: f64,
    /// Dissolved salt in each salinity cell, in g/m2.
    pub dissolved_salt: [f64; SALT_CELL_COUNT],
    /// Deposited salt in each salinity cell, in g/m2.
    pub deposited_salt: [f64; SALT_CELL_COUNT],
}

impl SoilCompartment {
    pub(crate) fn at_field_capacity(
        thickness: f64,
        layer_index: usize,
        field_capacity: f64,
    ) -> Self {
        Self {
            thickness,
            theta: field_capacity / 100.0,
            drainage: 0.0,
            layer_index,
            maximum_root_extraction: 0.0,
            adjusted_field_capacity: field_capacity,
            anaerobic_exposure: 0,
            weighting_factor: 0.0,
            major_sink: 0.0,
            minor_sink: 0.0,
            dissolved_salt: [0.0; SALT_CELL_COUNT],
            deposited_salt: [0.0; SALT_CELL_COUNT],
        }
    }
}

/// Persistent soil-compartment state accepted as input or retained for continuation.
///
/// Daily extraction weights, sinks, and drainage are deliberately absent because
/// they are transient working values recalculated by the engine.
#[derive(Clone, Debug)]
#[cfg_attr(feature = "serde", derive(serde::Serialize, serde::Deserialize))]
#[cfg_attr(feature = "serde", serde(rename_all = "camelCase"))]
pub struct SoilCompartmentState {
    /// Compartment thickness, in metres.
    pub thickness: f64,
    /// Volumetric water content as an m3/m3 fraction.
    pub theta: f64,
    /// Zero-based index of the associated soil layer.
    pub layer_index: usize,
    /// Field capacity adjusted for a water table, in volume percent.
    pub adjusted_field_capacity: f64,
    /// Consecutive days under anaerobic conditions.
    pub anaerobic_exposure: i32,
    /// Dissolved salt in each salinity cell, in g/m2.
    pub dissolved_salt: [f64; SALT_CELL_COUNT],
    /// Deposited salt in each salinity cell, in g/m2.
    pub deposited_salt: [f64; SALT_CELL_COUNT],
}

impl From<SoilCompartment> for SoilCompartmentState {
    fn from(compartment: SoilCompartment) -> Self {
        Self {
            thickness: compartment.thickness,
            theta: compartment.theta,
            layer_index: compartment.layer_index,
            adjusted_field_capacity: compartment.adjusted_field_capacity,
            anaerobic_exposure: compartment.anaerobic_exposure,
            dissolved_salt: compartment.dissolved_salt,
            deposited_salt: compartment.deposited_salt,
        }
    }
}

impl SoilCompartmentState {
    pub(crate) fn into_working(
        self,
        profile: &PreparedSoilProfile,
    ) -> Result<SoilCompartment, Error> {
        profile
            .layers
            .get(self.layer_index)
            .ok_or(Error::InvalidSimulationConfiguration(
                "initial soil compartment refers to a missing layer",
            ))?;
        let finite = [self.thickness, self.theta, self.adjusted_field_capacity]
            .into_iter()
            .chain(self.dissolved_salt)
            .chain(self.deposited_salt)
            .all(f64::is_finite);
        if !finite || self.thickness <= 0.0 || self.theta < 0.0 {
            return Err(Error::InvalidSimulationConfiguration(
                "initial soil compartment contains invalid state",
            ));
        }
        Ok(SoilCompartment {
            thickness: self.thickness,
            theta: self.theta,
            drainage: 0.0,
            layer_index: self.layer_index,
            maximum_root_extraction: 0.0,
            adjusted_field_capacity: self.adjusted_field_capacity,
            anaerobic_exposure: self.anaerobic_exposure.max(0),
            weighting_factor: 0.0,
            major_sink: 0.0,
            minor_sink: 0.0,
            dissolved_salt: self.dissolved_salt,
            deposited_salt: self.deposited_salt,
        })
    }
}

/// Derives the quantized drainage coefficient from saturated conductivity.
///
/// `ksat` is in millimetres per day; the result is a dimensionless
/// daily drainage fraction.
#[must_use]
pub fn drainage_coefficient_from_ksat(ksat: f64) -> f64 {
    if ksat.abs() < f64::EPSILON {
        return 0.0;
    }
    let percent = roundc_i32(100.0 * 0.086_6 * (0.35 * ksat.ln()).exp()).clamp(0, 100);
    f64::from(percent) / 100.0
}

fn gravel_volume_from_mass(porosity: f64, gravel_mass: i8) -> f64 {
    if gravel_mass <= 0 {
        return 0.0;
    }
    let mineral_bulk_density = 2.65;
    let matrix_bulk_density = mineral_bulk_density * (1.0 - porosity / 100.0);
    let soil_bulk_density = 100.0
        / (f64::from(gravel_mass) / mineral_bulk_density
            + f64::from(100_i16 - i16::from(gravel_mass)) / matrix_bulk_density);
    f64::from(gravel_mass) * (soil_bulk_density / mineral_bulk_density)
}

fn salinity_cells_from_ksat(ksat: f64) -> i8 {
    if ksat <= 112.0 {
        11
    } else {
        roundc_i8(1.6 + 1_000.0 / ksat).max(2)
    }
}

fn classify_soil(saturation: f64, field_capacity: f64, wilting_point: f64, ksat: f64) -> SoilClass {
    if saturation > 55.0 {
        return SoilClass::SiltyClayey;
    }
    if wilting_point >= 20.0 {
        if saturation >= 49.0 && field_capacity >= 40.0 {
            SoilClass::SiltyClayey
        } else {
            SoilClass::SandyClayey
        }
    } else if field_capacity < 23.0
        || (wilting_point < 6.0 && field_capacity < 28.0 && ksat > 750.0)
    {
        SoilClass::Sandy
    } else if wilting_point > 16.0 && ksat < 100.0 {
        SoilClass::SandyClayey
    } else {
        SoilClass::Loamy
    }
}

#[expect(
    clippy::approx_constant,
    reason = "0.6366 is the calibrated AquaCrop coefficient, not two divided by pi"
)]
fn derive_capillary_rise_parameters(class: &SoilClass, ksat: f64) -> Option<(f64, f64)> {
    if roundc_i32(ksat * 1_000.0) <= 0 {
        return None;
    }
    Some(match class {
        SoilClass::Sandy => (-0.311_2 - ksat / 100_000.0, -1.493_6 + 0.241_6 * ksat.ln()),
        SoilClass::Loamy => (
            -0.498_6 + 9.0 * ksat / 100_000.0,
            -2.132_0 + 0.477_8 * ksat.ln(),
        ),
        SoilClass::SandyClayey => (
            -0.567_7 - 4.0 * ksat / 100_000.0,
            -3.718_9 + 0.592_2 * ksat.ln(),
        ),
        SoilClass::SiltyClayey => (
            -0.636_6 + 8.0 * ksat / 10_000.0,
            -1.916_5 + 0.706_3 * ksat.ln(),
        ),
    })
}

fn salt_mobility(
    diffusion: i8,
    macro_porosity: i8,
    regular_cells: i8,
    cells_with_saturation: i8,
    upper_limit: f64,
) -> [f64; SALT_CELL_COUNT] {
    let mut mobility = [0.0; SALT_CELL_COUNT];
    let mix = f64::from(diffusion) / 100.0;
    let upper_limit = upper_limit * 100.0;
    let mut last_macro_cell = if f64::from(macro_porosity) > upper_limit {
        i32::from(cells_with_saturation)
    } else {
        roundc_i32((f64::from(macro_porosity) / upper_limit) * f64::from(regular_cells))
    };
    if last_macro_cell <= 0 {
        last_macro_cell = 1;
    }
    let (a, b) = if mix < 0.5 {
        (mix * 2.0, (10.0 * (0.5 - mix) * 10.0_f64.ln()).exp())
    } else {
        (
            2.0 * (1.0 - mix),
            (10.0 * (mix - 0.5) * 10.0_f64.ln()).exp(),
        )
    };
    for cell in 1..last_macro_cell {
        let x = f64::from(cell) / f64::from(last_macro_cell - 1);
        let value = if mix <= 0.0 {
            0.0
        } else if mix < 0.5 {
            let y = (a.ln() + x * b.ln()).exp();
            (y - a) / (a * b - a)
        } else if (mix - 0.5).abs() <= f64::EPSILON {
            x
        } else if mix < 1.0 {
            let y = (a.ln() + (1.0 - x) * b.ln()).exp();
            1.0 - (y - a) / (a * b - a)
        } else {
            1.0
        };
        if let Ok(index) = usize::try_from(cell - 1)
            && let Some(slot) = mobility.get_mut(index)
        {
            *slot = value;
        }
    }
    for cell in last_macro_cell..=i32::from(cells_with_saturation) {
        if let Ok(index) = usize::try_from(cell - 1)
            && let Some(slot) = mobility.get_mut(index)
        {
            *slot = 1.0;
        }
    }
    mobility
}

#[cfg(test)]
mod tests {
    use assertables::assert_eq_f64;

    use super::*;

    #[test]
    fn drainage_coefficient_is_quantized_to_hundredths() {
        assert_eq_f64!(drainage_coefficient_from_ksat(1200.0), 1.0_f64);
        assert_eq_f64!(drainage_coefficient_from_ksat(10.0), 0.19_f64);
    }

    #[test]
    fn zero_conductivity_has_no_drainage() {
        assert_eq_f64!(drainage_coefficient_from_ksat(0.0), 0.0_f64);
    }
}
