//! Rooted-compartment profiles and AquaCrop 7.3 transpiration extraction.

use crate::Error;
use crate::soil::{PreparedSoilProfile, SoilCompartment};
use crate::water_balance::WaterBalanceError;

/// Gravel selection for conversion of extracted millimetres to compartment water content.
#[derive(Debug)]
pub enum ExtractionGravelMode {
    /// Reproduce 7.3 behavior by retaining the last layer from the capacity loop.
    StrictReference,
    /// Use each compartment's own soil layer.
    CompartmentLayer,
}

/// Root-profile parameters used by the extraction distribution.
#[derive(Debug)]
pub struct RootExtractionParameters {
    /// Current rooting depth, in metres.
    pub rooting_depth: f64,
    /// Potential transpiration after root-zone stress, in millimetres.
    pub potential_transpiration: f64,
    /// Maximum extraction at the top of the root zone, in m3/m3/day.
    pub extraction_top: f64,
    /// Maximum extraction at the bottom of the root zone, in m3/m3/day.
    pub extraction_bottom: f64,
    /// Root-density correction when deepening was restricted.
    pub root_density_correction: f64,
    /// Gravel compatibility behavior.
    pub gravel_mode: ExtractionGravelMode,
}

/// Actual transpiration extracted by the 7.3 distribution pass.
#[derive(Debug)]
pub struct RootExtraction {
    /// Water removed from rooted compartments, in millimetres.
    pub actual_transpiration: f64,
    /// Unmet target left after all rooted compartments, in millimetres.
    pub unmet_transpiration: f64,
}

/// Distributes transpiration targets and extracts water using local stress capacities.
///
/// Each local stress factor corresponds to one compartment and normally combines water,
/// salinity, and anaerobic effects before this routine is called. These factors
/// are dimensionless; parameter, profile, and compartment fields retain their
/// field-documented units. Returned transpiration amounts are in millimetres.
///
/// # Errors
///
/// Returns [`Error`] for invalid layers, incomplete factors, or zero unrestricted rooted
/// capacity with positive potential transpiration.
pub fn extract_root_water(
    profile: &PreparedSoilProfile,
    compartments: &mut [SoilCompartment],
    parameters: &RootExtractionParameters,
    local_stress_factors: &[f64],
) -> Result<RootExtraction, Error> {
    if local_stress_factors.len() < compartments.len() {
        return Err(WaterBalanceError::StateLengthMismatch {
            state: "local root extraction stress",
            expected: compartments.len(),
            actual: local_stress_factors.len(),
        }
        .into());
    }
    assign_rooted_fractions(compartments, parameters.rooting_depth);
    assign_maximum_extraction(compartments, parameters);
    let mut unrestricted_capacity = 0.0;
    let mut retained_layer_index = 0;
    for (index, compartment) in compartments.iter_mut().enumerate() {
        retained_layer_index = compartment.layer_index;
        profile
            .layers
            .get(retained_layer_index)
            .ok_or(WaterBalanceError::MissingLayer {
                compartment: index,
                layer: retained_layer_index,
            })?;
        compartment.major_sink = 1_000.0
            * local_stress_factors[index]
            * compartment.weighting_factor
            * compartment.maximum_root_extraction
            * compartment.thickness;
        if compartment.weighting_factor > 0.000_01 {
            unrestricted_capacity += 1_000.0
                * compartment.weighting_factor
                * compartment.maximum_root_extraction
                * compartment.thickness;
        }
    }
    if parameters.potential_transpiration > 0.0 && unrestricted_capacity <= 0.0 {
        return Err(WaterBalanceError::ZeroRootExtractionCapacity.into());
    }
    let target_ratio = if unrestricted_capacity > 0.0 {
        parameters.potential_transpiration / unrestricted_capacity
    } else {
        0.0
    };
    for compartment in compartments.iter_mut() {
        compartment.minor_sink = if compartment.weighting_factor > 0.000_01 {
            target_ratio
                * 1_000.0
                * compartment.weighting_factor
                * compartment.maximum_root_extraction
                * compartment.thickness
        } else {
            0.0
        };
    }

    let mut actual = 0.0;
    let mut residue = 0.0;
    for (index, compartment) in compartments.iter_mut().enumerate() {
        let target = compartment.minor_sink;
        if target <= 1.0e-5 || parameters.potential_transpiration - actual <= 1.0e-5 {
            continue;
        }
        let capacity = compartment.major_sink;
        let mut sink = if target < capacity {
            if target + residue > capacity {
                residue -= capacity - target;
                capacity
            } else {
                let value = target + residue;
                residue = 0.0;
                value
            }
        } else {
            residue += target - capacity;
            capacity
        };
        sink = sink.min(parameters.potential_transpiration - actual);
        let layer_index = match parameters.gravel_mode {
            ExtractionGravelMode::StrictReference => retained_layer_index,
            ExtractionGravelMode::CompartmentLayer => compartment.layer_index,
        };
        let layer = profile
            .layers
            .get(layer_index)
            .ok_or(WaterBalanceError::MissingLayer {
                compartment: index,
                layer: layer_index,
            })?;
        compartment.theta -= sink / (1_000.0 * compartment.thickness * layer.fine_earth_fraction());
        actual += sink;
    }
    Ok(RootExtraction {
        actual_transpiration: actual,
        unmet_transpiration: parameters.potential_transpiration - actual,
    })
}

fn assign_rooted_fractions(compartments: &mut [SoilCompartment], rooting_depth: f64) {
    let mut cumulative_depth = 0.0;
    let mut finished = false;
    for compartment in compartments {
        if finished {
            compartment.weighting_factor = 0.0;
            continue;
        }
        cumulative_depth += compartment.thickness;
        compartment.weighting_factor = if cumulative_depth <= rooting_depth {
            1.0
        } else {
            ((rooting_depth - (cumulative_depth - compartment.thickness)) / compartment.thickness)
                .max(0.0)
        };
        finished = cumulative_depth >= rooting_depth;
    }
}

#[expect(
    clippy::manual_midpoint,
    reason = "addition before division preserves the reference Fortran operation order"
)]
fn assign_maximum_extraction(
    compartments: &mut [SoilCompartment],
    parameters: &RootExtractionParameters,
) {
    let mut cumulative_depth = 0.0;
    let mut bottom_value = parameters.extraction_top;
    let mut finished = false;
    for compartment in compartments {
        if finished {
            compartment.maximum_root_extraction = 0.0;
            continue;
        }
        let top_value = bottom_value;
        cumulative_depth += compartment.thickness;
        bottom_value = if cumulative_depth <= parameters.rooting_depth {
            parameters.extraction_bottom * parameters.root_density_correction
                + (parameters.extraction_top
                    - parameters.extraction_bottom * parameters.root_density_correction)
                    * (parameters.rooting_depth - cumulative_depth)
                    / parameters.rooting_depth
        } else {
            parameters.extraction_bottom * parameters.root_density_correction
        };
        compartment.maximum_root_extraction = ((top_value + bottom_value) / 2.0).min(0.06);
        finished = cumulative_depth >= parameters.rooting_depth;
    }
}
