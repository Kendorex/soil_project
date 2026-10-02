from scripts.repair_rotation_manager_parameters import REQUIRED_KEYS, manager_index, parameter_keys


def test_indexes_manager_and_reads_parameter_keys():
    data = {"Name": "Root", "Children": [{"Name": "Field", "Children": [{
        "Name": "Sow using a variable rule",
        "Parameters": [{"Key": key, "Value": "x"} for key in sorted(REQUIRED_KEYS)],
    }]}]}
    managers = manager_index(data)
    assert list(managers) == ["/Root/Field/Sow using a variable rule"]
    assert REQUIRED_KEYS <= parameter_keys(next(iter(managers.values())))
