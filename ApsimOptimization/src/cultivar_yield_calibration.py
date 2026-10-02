"""Helpers for evidence-bounded cultivar yield calibration experiments."""
from __future__ import annotations

import re

from .historical_readiness import spearman_r
from .multifield_quality import metrics


COMMAND = re.compile(r'^\s*(?P<path>\[[^=]+?)\s*=\s*(?P<value>.*?)\s*$')


def patch_named_cultivar(model, cultivar_name, replacements, expected_count=None):
    """Patch existing cultivar commands on every field, failing on ambiguity."""
    changed = []

    def visit(node, path=''):
        if isinstance(node, dict):
            name = node.get('Name')
            current = f'{path}/{name}' if name else path
            if name == cultivar_name and 'Cultivar' in str(node.get('$type') or ''):
                commands = node.get('Command')
                if not isinstance(commands, list):
                    raise ValueError(f'{current}: cultivar has no Command list')
                positions = {}
                for index, command in enumerate(commands):
                    match = COMMAND.match(command)
                    if match:
                        positions[match.group('path').strip()] = (index, match.group('value'))
                missing = sorted(set(replacements) - set(positions))
                if missing:
                    raise ValueError(f'{current}: missing commands {missing}')
                for command_path, new_value in replacements.items():
                    index, old_value = positions[command_path]
                    commands[index] = f'{command_path} = {new_value}'
                    changed.append({'path': current, 'command': command_path,
                                    'old': old_value, 'new': str(new_value)})
            for value in node.values():
                visit(value, current)
        elif isinstance(node, list):
            for value in node:
                visit(value, path)

    visit(model)
    cultivar_count = len(changed) // len(replacements) if replacements else 0
    if expected_count is not None and cultivar_count != expected_count:
        raise ValueError(
            f'Expected {expected_count} {cultivar_name} cultivars, found {cultivar_count}')
    return changed


def upsert_named_cultivar_commands(model, cultivar_name, replacements,
                                   expected_count=None):
    """Replace or add cultivar commands on every matching field cultivar.

    This is intended for controlled APSIM-reference sensitivity profiles where
    the source cultivar contains a command which is absent from the local
    cultivar override.  Every addition is recorded explicitly in the returned
    audit trail.
    """
    changed = []
    cultivar_count = 0

    def visit(node, path=''):
        nonlocal cultivar_count
        if isinstance(node, dict):
            name = node.get('Name')
            current = f'{path}/{name}' if name else path
            if name == cultivar_name and 'Cultivar' in str(node.get('$type') or ''):
                commands = node.get('Command')
                if not isinstance(commands, list):
                    raise ValueError(f'{current}: cultivar has no Command list')
                cultivar_count += 1
                positions = {}
                for index, command in enumerate(commands):
                    match = COMMAND.match(command)
                    if match:
                        positions[match.group('path').strip()] = (
                            index, match.group('value'))
                for command_path, new_value in replacements.items():
                    if command_path in positions:
                        index, old_value = positions[command_path]
                        commands[index] = f'{command_path} = {new_value}'
                        operation = 'replaced'
                    else:
                        old_value = None
                        commands.append(f'{command_path} = {new_value}')
                        operation = 'added'
                    changed.append({
                        'path': current,
                        'command': command_path,
                        'old': old_value,
                        'new': str(new_value),
                        'operation': operation,
                    })
            for value in node.values():
                visit(value, current)
        elif isinstance(node, list):
            for value in node:
                visit(value, path)

    visit(model)
    if expected_count is not None and cultivar_count != expected_count:
        raise ValueError(
            f'Expected {expected_count} {cultivar_name} cultivars, found {cultivar_count}')
    return changed


def crop_scopes(rows, crop='Oats', validation_year=2023):
    selected = [row for row in rows
                if crop in str(row.get('yield_components') or '').split('+')]
    calibration = [row for row in selected if int(row['year']) < validation_year]
    validation = [row for row in selected if int(row['year']) == validation_year]
    result = {
        'calibration': metrics(calibration),
        'validation': metrics(validation),
        'all': metrics(selected),
    }
    pairs = [(float(row['observed_yield_t_ha']), float(row['simulated_yield_t_ha']))
             for row in validation
             if row.get('observed_yield_t_ha') not in (None, '') and
             row.get('simulated_yield_t_ha') not in (None, '')]
    result['validation']['spearman_r'] = spearman_r(
        [left for left, _ in pairs], [right for _, right in pairs])
    return result


def select_by_calibration(candidates, baseline_name='current_yakov'):
    """Select on pre-validation RMSE, then audit the untouched validation year."""
    by_name = {row['name']: row for row in candidates}
    if baseline_name not in by_name:
        raise ValueError(f'Missing baseline candidate {baseline_name}')
    ranked = sorted(candidates, key=lambda row: (
        row['metrics']['calibration']['rmse_t_ha'], row['name']))
    selected = ranked[0]
    baseline = by_name[baseline_name]
    cal_gain = (selected['metrics']['calibration']['rmse_t_ha'] <
                baseline['metrics']['calibration']['rmse_t_ha'])
    val_gain = (selected['metrics']['validation']['rmse_t_ha'] <
                baseline['metrics']['validation']['rmse_t_ha'])
    rank_positive = ((selected['metrics']['validation'].get('spearman_r') or 0) > 0)
    accepted = selected['name'] != baseline_name and cal_gain and val_gain and rank_positive
    return {
        'selection_rule': ('minimum oats RMSE on 2019-2022 only; 2023 is used once '
                           'for the final acceptance audit'),
        'selected_on_calibration': selected['name'],
        'baseline': baseline_name,
        'checks': {
            'calibration_rmse_improved': cal_gain,
            'validation_rmse_improved': val_gain,
            'validation_field_ranking_positive': rank_positive,
        },
        'accepted': accepted,
        'status': ('accepted_for_model_assembly' if accepted else
                   'rejected_or_no_change_after_held_out_validation'),
    }
