"""Resolve file-based configuration paths before copying snapshots elsewhere."""
from copy import deepcopy
import json
import os
from pathlib import Path


def resolved_config(path, prefer_environment=False):
    config = deepcopy(json.loads(path.read_text(encoding='utf-8')))
    for key in ('model_root', 'model_config'):
        if key in config:
            config[key] = str((path.parent / config[key]).resolve())
    if config.get('command'):
        executable = config['command'][0]
        if not Path(executable).is_absolute() and ('/' in executable or '\\' in executable):
            config['command'][0] = str((path.parent / executable).resolve())
    if (prefer_environment and config['engine'] == 'apsim'
            and os.environ.get('APSIM_MODELS')):
        config['command'] = [os.environ['APSIM_MODELS']]
    return config
