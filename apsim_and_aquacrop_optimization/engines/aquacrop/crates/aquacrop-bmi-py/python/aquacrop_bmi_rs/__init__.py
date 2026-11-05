"""BMI 2.0 Python binding for the AquaCrop Rust core.

``AquaCropBmi`` is a thin PyO3 wrapper around the pure-Rust ``aquacrop-bmi`` engine crate, which
itself wraps ``aquacrop-core``. Nothing in this package touches the filesystem, network, or a
temporary directory: ``initialize()`` takes a JSON *string* containing the full scenario (every
AquaCrop text-format block as verbatim content, plus explicit dates), not a file path.

The Rust crate contains the variable registry and protocol tests.
"""

from ._aquacrop_bmi_rs import AquaCropBmi

# `pymt` reads component metadata from this package-relative path.
AquaCropBmi.METADATA = "data/aquacrop_bmi_rs"

__all__ = ["AquaCropBmi"]
