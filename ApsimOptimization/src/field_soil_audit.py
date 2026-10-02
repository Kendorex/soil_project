"""Compare field soil inputs without assigning agronomic meaning to differences."""
from __future__ import annotations

import hashlib
import json


IGNORED_KEYS = {'Name', 'Enabled', 'ReadOnly'}
INITIAL_STATE_KEYS = {'InitialValues', 'FractionFull', 'DepthWetSoil', 'RelativeTo'}


def normalized(node, ignore_initial_state=False):
    if isinstance(node, dict):
        return {key: normalized(value, ignore_initial_state)
                for key, value in sorted(node.items())
                if key not in IGNORED_KEYS and
                not (ignore_initial_state and key in INITIAL_STATE_KEYS)}
    if isinstance(node, list):
        return [normalized(value, ignore_initial_state) for value in node]
    return node


def digest(node, ignore_initial_state=False):
    payload = json.dumps(normalized(node, ignore_initial_state),
                         ensure_ascii=False, sort_keys=True,
                         separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def flatten(node, path=''):
    result = {}
    if isinstance(node, dict):
        for key, value in sorted(node.items()):
            if key in IGNORED_KEYS:
                continue
            child_path = f'{path}/{key}'
            result.update(flatten(value, child_path))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            label = value.get('Name') if isinstance(value, dict) else None
            result.update(flatten(value, f'{path}[{label or index}]'))
    else:
        result[path] = node
    return result


def differences(reference, candidate, limit=100):
    left, right = flatten(reference), flatten(candidate)
    rows = []
    for path in sorted(left.keys() | right.keys()):
        if left.get(path) != right.get(path):
            rows.append({'path': path, 'reference': left.get(path),
                         'candidate': right.get(path)})
            if len(rows) >= limit:
                break
    return rows
