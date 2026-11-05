"""Prevent a future horizon from being relabelled without updating initial states."""
from pathlib import Path

import pytest
import yaml

from agro.planning.cli import main

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / 'models/apsim/configs/rotation_planning_2025_2029.yaml'


def test_changed_horizon_requires_matching_initial_state_year(tmp_path):
    config = yaml.safe_load(CONFIG.read_text(encoding='utf-8'))
    config['rotation_rules'] = str((CONFIG.parent / config['rotation_rules']).resolve())
    config['start_year'] = 2027
    path = tmp_path / 'planning.yaml'
    path.write_text(yaml.safe_dump(config), encoding='utf-8')
    with pytest.raises(ValueError, match='initial_state_year'):
        main(['--config', str(path), '--output', str(tmp_path / 'plan')])
    assert not (tmp_path / 'plan').exists()


def test_existing_output_is_preserved(tmp_path):
    destination = tmp_path / 'plan'
    destination.mkdir()
    marker = destination / 'keep.txt'
    marker.write_text('existing result')
    with pytest.raises(FileExistsError):
        main(['--config', str(CONFIG), '--output', str(destination)])
    assert marker.read_text() == 'existing result'
