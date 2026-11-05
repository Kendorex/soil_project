//! Drop-in-compatible command-line interface for AquaCrop.

use std::path::PathBuf;
use std::process::ExitCode;

use aquacrop_core::AQUACROP_COMPATIBLE_VERSION;
use aquacrop_core::io::run_project_directory;
use clap::Parser;

const ABOUT: &str = const_format::formatcp!(
    "A Rust port of the FAO AquaCrop model

Run AquaCrop projects from a standard project directory

Compatible AquaCrop model version: {AQUACROP_COMPATIBLE_VERSION}"
);

#[derive(Parser)]
#[command(
    name = std::env!("CARGO_BIN_NAME"),
    version,
    about = ABOUT,
)]
struct Cli {
    /// AquaCrop directory containing `LIST`, `DATA`, `PARAM`, `SIMUL`, and `OUTP`.
    #[arg(short = 'C', long, value_name = "DIRECTORY", default_value = ".")]
    root: PathBuf,
}

fn main() -> ExitCode {
    let cli = Cli::parse();
    match run_project_directory(&cli.root) {
        Ok(_) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("{error}");
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod tests {
    use clap::CommandFactory;

    use super::*;

    #[test]
    fn help_reports_compatible_aquacrop_version() {
        let help = Cli::command().render_long_help().to_string();

        assert!(help.contains(AQUACROP_COMPATIBLE_VERSION));
    }
}
