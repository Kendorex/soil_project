use std::fs;
use std::path::Path;
use std::str::FromStr;

use crate::soil::{SoilLayer, SoilProfile};
use crate::{AquaCropVersion, Error};

use super::InputError;
use super::parse::{first_token, parse_next, parse_token, required_line};

const PROFILE_HEADER_LINES: usize = 8;

/// Reads an AquaCrop `.SOL` file into user-facing soil-profile configuration.
///
/// # Errors
///
/// Returns [`Error`] when the file cannot be read or decoded.
pub fn read_soil_profile(path: impl AsRef<Path>) -> Result<SoilProfile, Error> {
    parse_soil_profile(&fs::read_to_string(path).map_err(InputError::from)?)
}

/// Parses an AquaCrop `.SOL` file into user-facing soil-profile configuration.
///
/// # Errors
///
/// Returns [`Error`] when the header is incomplete, a field is malformed, or no layers are
/// declared.
pub fn parse_soil_profile(input: &str) -> Result<SoilProfile, Error> {
    let lines: Vec<_> = input.lines().collect();
    required_line(&lines, PROFILE_HEADER_LINES, "soil profile header")?;
    let version_token = first_token(
        required_line(&lines, 2, "AquaCrop version")?,
        2,
        "AquaCrop version",
    )?;
    let version =
        AquaCropVersion::from_str(version_token).map_err(|_| InputError::InvalidValue {
            line: 2,
            field: "AquaCrop version",
            value: version_token.to_owned(),
        })?;
    let version_code = i32::from(version.major) * 10 + i32::from(version.minor);
    let curve_number = parse_token(lines[2], 3, "curve number")?;
    let readily_evaporable_water = parse_token(lines[3], 4, "readily evaporable water")?;
    let layer_count = parse_token::<usize>(lines[4], 5, "soil-layer count")?;
    if layer_count == 0 {
        return Err(InputError::InvalidValue {
            line: 5,
            field: "soil-layer count",
            value: layer_count.to_string(),
        }
        .into());
    }
    let available_layers = lines.len().saturating_sub(PROFILE_HEADER_LINES);
    if available_layers < layer_count {
        return Err(InputError::IncompleteSection {
            section: "soil layers",
            complete_items: available_layers,
            expected_lines: 1,
            actual_lines: 0,
        }
        .into());
    }

    let mut layers = Vec::with_capacity(layer_count);
    for index in 0..layer_count {
        let line_number = PROFILE_HEADER_LINES + index + 1;
        layers.push(parse_layer(
            lines[PROFILE_HEADER_LINES + index],
            line_number,
            version_code,
        )?);
    }

    Ok(SoilProfile {
        curve_number,
        readily_evaporable_water,
        layers,
    })
}

fn parse_layer(line: &str, line_number: usize, version_code: i32) -> Result<SoilLayer, Error> {
    let mut tokens = line.split_whitespace();
    let thickness = parse_next(&mut tokens, line_number, "layer thickness")?;
    let saturation = parse_next(&mut tokens, line_number, "saturation")?;
    let field_capacity = parse_next(&mut tokens, line_number, "field capacity")?;
    let wilting_point = parse_next(&mut tokens, line_number, "wilting point")?;
    let saturated_hydraulic_conductivity =
        parse_next(&mut tokens, line_number, "saturated hydraulic conductivity")?;
    let (penetrability, gravel_mass, capillary_rise_parameters) = if version_code < 40 {
        parse_next::<String>(&mut tokens, line_number, "legacy separator")?;
        (100, 0, None)
    } else if version_code < 60 {
        let capillary_rise_a = parse_next(&mut tokens, line_number, "capillary-rise parameter a")?;
        let capillary_rise_b = parse_next(&mut tokens, line_number, "capillary-rise parameter b")?;
        parse_next::<String>(&mut tokens, line_number, "legacy separator")?;
        (100, 0, Some((capillary_rise_a, capillary_rise_b)))
    } else {
        let penetrability = parse_next(&mut tokens, line_number, "root penetrability")?;
        let gravel_mass = parse_next(&mut tokens, line_number, "gravel mass")?;
        let capillary_rise_a = parse_next(&mut tokens, line_number, "capillary-rise parameter a")?;
        let capillary_rise_b = parse_next(&mut tokens, line_number, "capillary-rise parameter b")?;
        (
            penetrability,
            gravel_mass,
            Some((capillary_rise_a, capillary_rise_b)),
        )
    };
    parse_next::<String>(&mut tokens, line_number, "layer description")?;
    let invalid_physical_values = thickness <= 0.0
        || !(0.0..=100.0).contains(&saturation)
        || !(0.0..=saturation).contains(&field_capacity)
        || !(0.0..=field_capacity).contains(&wilting_point)
        || saturated_hydraulic_conductivity < 0.0
        || !(0..=100).contains(&penetrability)
        || !(0..=100).contains(&gravel_mass);
    if invalid_physical_values {
        return Err(InputError::InvalidValue {
            line: line_number,
            field: "soil-layer physical values",
            value: line.to_owned(),
        }
        .into());
    }

    Ok(SoilLayer {
        thickness,
        saturation,
        field_capacity,
        wilting_point,
        saturated_hydraulic_conductivity,
        penetrability,
        gravel_mass,
        capillary_rise_parameters,
    })
}
