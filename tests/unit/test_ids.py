import re

import pytest

from shared.ids import IdPrefix, sequential_id, unique_id


def test_sequential_id_uses_prefix_and_zero_padding():
    assert sequential_id(IdPrefix.ORDER, 1) == "ORD_000001"
    assert sequential_id(IdPrefix.TASK, 187) == "TASK_000187"


def test_sequential_id_accepts_custom_width():
    assert sequential_id(IdPrefix.PILOT_EXECUTION, 1, width=4) == "PILOT_0001"


def test_sequential_id_rejects_non_positive_numbers():
    with pytest.raises(ValueError):
        sequential_id(IdPrefix.ORDER, 0)


def test_unique_id_has_prefix_and_hex_suffix():
    assert re.fullmatch(r"MSG_[0-9a-f]{32}", unique_id(IdPrefix.MESSAGE))


def test_unique_ids_do_not_repeat():
    ids = {unique_id(IdPrefix.MESSAGE) for _ in range(1000)}

    assert len(ids) == 1000
