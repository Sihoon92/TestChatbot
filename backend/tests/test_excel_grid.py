from datetime import datetime

import pytest

from app.excel.grid import (
    a1_offset,
    aggregate_values,
    col_to_letter,
    format_grid,
    parse_a1,
    profile_values,
    search_values,
)


def test_col_to_letter():
    assert col_to_letter(1) == "A"
    assert col_to_letter(26) == "Z"
    assert col_to_letter(27) == "AA"


def test_parse_a1():
    assert parse_a1("A1") == (1, 1)
    assert parse_a1("B3") == (3, 2)
    assert parse_a1("AA10") == (10, 27)


def test_a1_offset():
    assert a1_offset("A1", 0, 0) == "A1"
    assert a1_offset("A1", 7, 1) == "B8"


def test_format_grid_has_letters_and_row_numbers():
    grid = format_grid([["라인", "제품"], ["A", "제품1"]], "A1")
    lines = grid.splitlines()
    assert "A" in lines[0] and "B" in lines[0]      # 열문자 헤더
    assert lines[1].startswith("1")                  # 행번호
    assert "라인" in lines[1]


def test_search_values_returns_cell_addresses():
    rows = [["라인", "제품"], ["A", "소계"], ["B", "제품2"]]
    hits = search_values(rows, "소계", "A1")
    assert hits == [{"cell": "B2", "value": "소계"}]


def test_profile_values_classifies_types():
    prof = profile_values([1, 2, None, "x", datetime(2026, 1, 1)])
    assert prof["count"] == 5
    assert prof["nulls"] == 1
    assert prof["nonnull"] == 4
    assert prof["types"]["int"] == 2
    assert prof["types"]["datetime"] == 1


def test_aggregate_values_sum_ignores_nonnumeric():
    assert aggregate_values([1, 2, "소계", None, 3.0], "sum") == 6.0
    assert aggregate_values([1, 2, 3], "mean") == 2.0
    assert aggregate_values([1, 2, "x"], "count") == 2


def test_aggregate_values_rejects_unknown_op():
    with pytest.raises(ValueError):
        aggregate_values([1, 2], "median")


def test_aggregate_values_rejects_unknown_op_on_empty_list():
    """Regression test: unknown op should raise ValueError even with empty input."""
    with pytest.raises(ValueError):
        aggregate_values([], "median")
