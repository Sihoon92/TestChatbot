from datetime import datetime

import pytest

from app.excel.grid import (
    EMPTY_CELL,
    a1_offset,
    aggregate_values,
    col_to_letter,
    format_grid,
    outline_grid,
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


# ── 시트 윤곽 ──────────────────────────────────────────────────────
# read_range 는 30행까지만 보여준다. 실물 IQC 시트는 40행이고 정작 필요한
# 대장 상세표가 33행부터라, 한 번 읽어서는 그 표가 아예 안 보였다. 에이전트가
# "나머지는 나눠 읽어라"는 안내를 받고도 두 번째 읽기를 하지 않아 표 하나가
# 통째로 누락됐다.
#
# outline_grid 는 각 행의 "채워진 칸"만 요약해 시트 전체를 한 화면에 보여준다.
# 값 전체를 싣지 않으므로 40행이든 94행이든 컨텍스트를 거의 안 먹는다.


def test_outline_grid_shows_row_numbers_and_first_values():
    out = outline_grid([[None, "구분", "항목"], [None, "유형1", 3]], "A1")
    lines = out.splitlines()
    assert lines[0].split()[0] == "1" and "B=구분" in lines[0] and "C=항목" in lines[0]
    assert lines[1].split()[0] == "2" and "B=유형1" in lines[1]


def test_outline_grid_separates_header_from_data_by_cell_type():
    """헤더 행이 데이터 행과 같은 열을 채워도 접히면 안 된다.

    열 위치만 보면 둘이 같은 모양이라 한 줄로 접히고, 정작 필요한 "헤더가
    몇 행인가"가 사라진다. 헤더는 보통 전부 문자열이고 데이터에는 숫자·날짜가
    섞이므로 타입까지 보면 경계가 저절로 드러난다.
    """
    rows = [
        [None, "No", "금형번호"],        # 헤더 — 전부 문자열
        [None, 1, "RX28312"],            # 데이터 — 숫자 섞임
        [None, 2, "RX28315"],
    ]
    lines = outline_grid(rows, "A1").splitlines()
    assert len(lines) == 2, "헤더 1줄 + 데이터 1줄(접힘)"
    assert "B=No" in lines[0]
    assert "2-3" in lines[1]


def test_outline_grid_collapses_consecutive_rows_of_same_shape():
    """같은 칸이 채워진 행이 이어지면 한 줄로 접는다.

    데이터 행 수백 개가 같은 모양으로 반복되는데 그걸 다 찍으면 윤곽이
    묻힌다. 접으면 '어디서 표가 시작하고 끝나는가' 만 남는다.
    """
    rows = [[None, "No", "금형번호"]] + [[None, i, f"RX{i}"] for i in range(1, 51)]
    out = outline_grid(rows, "A1")
    lines = out.splitlines()
    assert len(lines) == 2, f"헤더 1줄 + 데이터 50행 1줄이어야 하는데 {len(lines)}줄"
    assert "2-51" in lines[1], "접힌 행 범위가 보여야 한다"


def test_outline_grid_does_not_collapse_different_shapes():
    """모양이 달라지는 지점이 곧 표의 경계다 — 접으면 안 된다."""
    rows = [
        [None, "제목"],              # 1칸
        [None, None],                # 빈 행
        [None, "No", "금형번호"],     # 2칸  ← 새 표 헤더
    ]
    out = outline_grid(rows, "A1")
    assert len(out.splitlines()) == 3


def test_outline_grid_marks_blank_rows():
    out = outline_grid([[None, "a"], [None, None], [None, "b"]], "A1")
    assert "(빈 행)" in out.splitlines()[1]


def test_outline_grid_respects_offset():
    """used_range 가 A1 이 아닌 곳에서 시작해도 행번호·열문자가 맞아야 한다."""
    out = outline_grid([[None, "금형번호"]], "C3")
    assert out.splitlines()[0].startswith("      3")
    assert "D=금형번호" in out


def test_outline_grid_truncates_long_values_and_limits_cells():
    """윤곽은 구조를 보는 용도라 값을 다 보여줄 필요가 없다."""
    rows = [["짧게" * 30, "b", "c", "d", "e", "f", "g"]]
    out = outline_grid(rows, "A1", max_cells=3, max_len=10)
    line = out.splitlines()[0]
    assert "…" in line, "긴 값은 잘려야 한다"
    assert line.count("=") == 3, "표시할 셀 개수를 제한해야 한다"
    assert "7칸" in line, "실제 채워진 칸 수는 그대로 알려줘야 한다"


def test_outline_grid_shows_the_column_span_of_each_row():
    """칸 **개수**만으로는 표가 어느 열에서 끝나는지 알 수 없다.

    실물에서 에이전트가 20열짜리 표를 'A33:J41' 로 찍어 읽어 K 열 이후를
    존재조차 모른 채 지나갔다 — PUNCH/DIE/차이/간극이 통째로 빠졌다.
    끝 열을 알려주면 추측할 이유가 없어진다."""
    rows = [[None, "No", "금형번호", None, "PUNCH", "간극"]]

    line = outline_grid(rows, "A1").splitlines()[0]

    assert "4칸" in line
    assert "B~F" in line


def test_outline_grid_span_follows_the_offset():
    out = outline_grid([[None, "금형번호", "업체"]], "C3")

    assert "D~E" in out


def test_outline_grid_blank_rows_have_no_span():
    """빈 행에 열 범위를 붙이면 없는 표의 경계처럼 보인다."""
    line = outline_grid([[None, "a"], [None, None]], "A1").splitlines()[1]

    assert "(빈 행)" in line
    assert "~" not in line


def test_outline_grid_empty_input():
    assert outline_grid([], "A1") == "(빈 범위)"


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
