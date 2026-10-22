import pytest

from scripts.manual_multiyear_apsim import parse_vector


def test_parse_vector_accepts_valid_indices():
    assert parse_vector("0, 2, 1", [1, 3, 2]) == [0, 2, 1]


def test_parse_vector_rejects_wrong_length():
    with pytest.raises(ValueError, match="expected 3"):
        parse_vector("0,1", [1, 2, 3])


def test_parse_vector_rejects_out_of_range_index():
    with pytest.raises(ValueError, match="allowed range"):
        parse_vector("0,3", [1, 3])
