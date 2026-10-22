import json
from pathlib import Path

import yaml

from src.multifield_model import fingerprint


ROOT = Path(__file__).resolve().parents[1]
ACTIVE_MODEL = "full_field2019_2023_calibrated_v3.apsimx"
ACTIVE_PROVENANCE = "full_field2019_2023_calibrated_v3.provenance.json"
ACTIVE_CONFIGS = (
    ROOT / "configs" / "multifield.yaml",
    ROOT / "configs" / "multifield_2023_n_diagnostic.yaml",
    ROOT / "configs" / "multifield_historical_replay.yaml",
)


def test_active_configs_use_the_accepted_v3_model():
    for config_path in ACTIVE_CONFIGS:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        model = (config_path.parent / config["model"]).resolve()
        provenance = (config_path.parent / config["model_provenance"]).resolve()

        assert model.name == ACTIVE_MODEL, config_path.name
        assert provenance.name == ACTIVE_PROVENANCE, config_path.name
        assert model.is_file(), model
        assert provenance.is_file(), provenance


def test_active_v3_provenance_matches_the_model_file():
    provenance_path = ROOT / "simulation" / "calibrated" / ACTIVE_PROVENANCE
    model_path = ROOT / "simulation" / "calibrated" / ACTIVE_MODEL
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))

    assert provenance["output_model_sha256"] == fingerprint(model_path)
