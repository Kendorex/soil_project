use std::str::FromStr;

use super::InputError;

pub(super) fn required_line<'a>(
    lines: &'a [&str],
    line_number: usize,
    field: &'static str,
) -> Result<&'a str, InputError> {
    lines
        .get(line_number - 1)
        .copied()
        .ok_or(InputError::MissingLine {
            line: line_number,
            field,
        })
}

pub(super) fn first_token<'a>(
    line: &'a str,
    line_number: usize,
    field: &'static str,
) -> Result<&'a str, InputError> {
    line.split_whitespace()
        .next()
        .ok_or(InputError::MissingLine {
            line: line_number,
            field,
        })
}

pub(super) fn parse_token<T>(
    line: &str,
    line_number: usize,
    field: &'static str,
) -> Result<T, InputError>
where
    T: FromStr,
{
    let token = first_token(line, line_number, field)?;
    token.parse().map_err(|_| InputError::InvalidValue {
        line: line_number,
        field,
        value: token.to_owned(),
    })
}

pub(super) fn parse_next<'a, T>(
    tokens: &mut impl Iterator<Item = &'a str>,
    line_number: usize,
    field: &'static str,
) -> Result<T, InputError>
where
    T: FromStr,
{
    let token = tokens.next().ok_or(InputError::MissingLine {
        line: line_number,
        field,
    })?;
    token.parse().map_err(|_| InputError::InvalidValue {
        line: line_number,
        field,
        value: token.to_owned(),
    })
}
