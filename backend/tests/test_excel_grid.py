from datetime import datetime

import pytest

from app.excel.grid import (
    EMPTY_CELL,
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


# ── 빈 칸을 눈에 보이게 하는 이유 ────────────────────────────────────
# 빈 셀을 빈 문자열로 두면 렌더 결과에 탭이 연달아 붙는다("4\t\tNo"). LLM 은
# 연속 구분자 사이의 빈 자리를 세지 못해 그 열을 건너뛰고, 이후 모든 열 문자가
# 한 칸씩 밀린다. 실제로 A 열이 비어 있는 시트에서 gemma4:26b 가 필드 11개를
# 이름으로는 전부 맞히고도 열 문자는 11개 전부 한 칸씩 왼쪽으로 지목했다
# (mold_no 를 I 가 아니라 H 로). 4B 모델도 같은 패턴이었다 — 모델 크기가
# 아니라 렌더링이 원인이다.


def test_format_grid_marks_empty_cells():
    """빈 셀은 눈에 보이는 자리표시자가 된다. 그래야 열을 셀 수 있다."""
    grid = format_grid([[None, "No", None, "날짜"]], "A1")
    body = grid.splitlines()[1]
    assert "\t\t" not in body, "연속된 탭이 남으면 LLM 이 그 자리를 못 센다"
    assert body.split("\t") == ["1", EMPTY_CELL, "No", EMPTY_CELL, "날짜"]


def test_format_grid_pads_short_rows():
    """행마다 길이가 다르면 짧은 행의 뒤쪽 열이 통째로 사라져 정렬이 깨진다.

    xlwings 는 보통 직사각형을 주지만, range_values 가 잘린 범위를 돌려주거나
    호출자가 리스트를 손보면 들쭉날쭉해질 수 있다.
    """
    grid = format_grid([["a", "b", "c"], ["d"]], "A1")
    header, first, second = grid.splitlines()
    assert len(header.split("\t")) == len(first.split("\t")) == len(second.split("\t"))
    assert second.split("\t") == ["2", "d", EMPTY_CELL, EMPTY_CELL]


def test_format_grid_column_letters_align_with_values():
    """헤더의 열 문자와 각 행의 값이 같은 인덱스로 맞아야 한다.

    이 정렬이 이 함수의 존재 이유다 — 에이전트는 여기서 읽은 열 문자를
    그대로 레이아웃에 담고, 파서가 그 주소로 값을 꺼낸다.
    """
    # A 열이 비어 있는 실제 시트 모양(데이터가 B 부터 시작)
    grid = format_grid([[None, "No", "날짜", "공정"]], "A1")
    letters = grid.splitlines()[0].split("\t")   # ["", "A", "B", "C", "D"]
    values = grid.splitlines()[1].split("\t")    # ["1", "·", "No", "날짜", "공정"]
    assert dict(zip(letters, values))["B"] == "No"
    assert dict(zip(letters, values))["C"] == "날짜"


def test_format_grid_respects_offset_with_empty_cells():
    """used_range 가 A1 이 아닌 곳에서 시작해도 열 문자가 맞아야 한다."""
    grid = format_grid([[None, "금형번호"]], "C3")
    letters = grid.splitlines()[0].split("\t")
    values = grid.splitlines()[1].split("\t")
    assert values[0] == "3"
    assert dict(zip(letters, values))["D"] == "금형번호"


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
