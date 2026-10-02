from src.field_soil_audit import differences, digest


def test_soil_digest_ignores_display_metadata_but_detects_values():
    first = {'Name': 'Soil A', 'Enabled': True,
             'Children': [{'Name': 'Physical', 'DUL': [0.2, 0.3]}]}
    renamed = {'Name': 'Soil B', 'Enabled': False,
               'Children': [{'Name': 'Other label', 'DUL': [0.2, 0.3]}]}
    changed = {'Name': 'Soil A', 'Enabled': True,
               'Children': [{'Name': 'Physical', 'DUL': [0.2, 0.31]}]}
    assert digest(first) == digest(renamed)
    assert digest(first) != digest(changed)
    assert differences(first, changed)[0]['path'].endswith('[Physical]/DUL[1]')


def test_static_digest_can_separate_initial_state_from_profile():
    first = {'Children': [{'Name': 'Water', 'InitialValues': [0.2], 'DUL': [0.3]}]}
    second = {'Children': [{'Name': 'Water', 'InitialValues': [0.25], 'DUL': [0.3]}]}
    assert digest(first) != digest(second)
    assert digest(first, ignore_initial_state=True) == digest(
        second, ignore_initial_state=True)
