from app.excel.grid import parse_a1
from app.excel.tools import make_excel_tools


def _slice_rows(rows: list[list], address: str) -> list[list]:
    """A1 anchored 2D `rows` 에서 `address`(예 'C2:C4', 'A1:C2')가 가리키는
    부분 사각형만 잘라 돌려준다. `Workbook.range_values` 의 최소한의 동작을
    Excel 없이 흉내내기 위한 테스트 전용 헬퍼 — 실제 range_values 의 address
    인자를 무시하고 고정된 값을 돌려주던 예전 가짜(하드코딩 [[3],[3],[5]])는
    다중 열 범위를 넘겨도 똑같은 값을 돌려줘, 사각형 범위 집계(B1) 회귀를
    전혀 잡지 못했다."""
    start, _, end = address.partition(":")
    end = end or start
    r1, c1 = parse_a1(start)
    r2, c2 = parse_a1(end)
    out = []
    for r in range(r1, r2 + 1):
        row_vals = []
        for c in range(c1, c2 + 1):
            ri, ci = r - 1, c - 1
            if 0 <= ri < len(rows) and 0 <= ci < len(rows[ri]):
                row_vals.append(rows[ri][ci])
            else:
                row_vals.append(None)
        out.append(row_vals)
    return out


class FakeWorkbook:
    """Task 2 Workbook 의 읽기 인터페이스만 흉내내는 가짜 — Excel 없이 도구 배선 검증."""

    def __init__(self):
        self._rows = [
            ["라인", "제품", "2026-01-01"],
            ["A", "제품1", 3],
            ["A", "소계", 3],
            ["B", "제품2", 5],
        ]

    def sheet_names(self):
        return ["데이터"]

    def used_values(self, sheet):
        return self._rows, "A1"

    def range_values(self, sheet, address):
        return _slice_rows(self._rows, address)

    def column_values(self, sheet, column, max_rows=5000):
        return [r[0] for r in self._rows]  # A열


def _tool(tools, name):
    return next(t for t in tools if t.name == name)


def test_list_sheets():
    tools = make_excel_tools(FakeWorkbook())
    assert "데이터" in _tool(tools, "list_sheets").invoke({})


def test_find_value_locates_subtotal():
    tools = make_excel_tools(FakeWorkbook())
    out = _tool(tools, "find_value").invoke({"sheet": "데이터", "query": "소계"})
    assert "B3" in out  # 3번째 행 2번째 열 = B3


def test_aggregate_sum():
    tools = make_excel_tools(FakeWorkbook())
    out = _tool(tools, "aggregate").invoke(
        {"sheet": "데이터", "address": "C2:C4", "op": "sum"}
    )
    assert "11" in out  # 3+3+5


def test_column_profile_reports_types():
    tools = make_excel_tools(FakeWorkbook())
    out = _tool(tools, "column_profile").invoke({"sheet": "데이터", "column": "A"})
    assert "unique" in out or "고유" in out


# --- read_range: 잘라내기 동작 및 COM 왕복 최소화 (브리프에 없는 테스트, Task 3 지시에 따라 추가) ---


class CountingWorkbook:
    """range_values 호출 횟수를 세는 가짜 — read_range 가 같은 범위를 두 번
    읽지 않는지(COM 왕복 최소화) 검증한다."""

    def __init__(self, rows):
        self._rows = rows
        self.range_values_calls = 0

    def sheet_names(self):
        return ["시트1"]

    def used_values(self, sheet):
        return self._rows, "A1"

    def range_values(self, sheet, address):
        self.range_values_calls += 1
        return self._rows

    def column_values(self, sheet, column, max_rows=5000):
        return [r[0] for r in self._rows]


def test_read_range_no_truncation_reads_range_values_once():
    rows = [[1, 2], [3, 4]]
    wb = CountingWorkbook(rows)
    tools = make_excel_tools(wb)
    out = _tool(tools, "read_range").invoke({"sheet": "시트1", "address": "A1:B2"})
    assert "주의" not in out
    assert wb.range_values_calls == 1


def test_read_range_truncates_rows_with_note_and_single_call():
    rows = [[i] for i in range(35)]  # 35행 1열 → 30행 초과
    wb = CountingWorkbook(rows)
    tools = make_excel_tools(wb)
    out = _tool(tools, "read_range").invoke({"sheet": "시트1", "address": "A1:A35"})
    assert "30행까지만 표시" in out
    assert wb.range_values_calls == 1  # COM 범위를 두 번 읽지 않는다


def test_read_range_truncates_columns_with_note():
    rows = [[i for i in range(35)]]  # 1행 35열 → 30열 초과
    wb = CountingWorkbook(rows)
    tools = make_excel_tools(wb)
    out = _tool(tools, "read_range").invoke({"sheet": "시트1", "address": "A1:AI1"})
    assert "30열까지만 표시" in out


# --- aggregate: 사각형(여러 열) 범위 (Group B1/B4 회귀) ---


class _RectWorkbook:
    """A1 기준 사각형 숫자 그리드 하나만 제공하는 최소 가짜.

    이전 FakeWorkbook.range_values 는 address 인자를 무시하고 항상
    [[3],[3],[5]] 를 돌려줬으므로, aggregate 에 몇 열짜리 범위를 넘기든 결과가
    똑같아 여러 열에 걸친 사각형 범위 집계(B1) 가 실제로 동작하는지 전혀
    검증하지 못했다."""

    def __init__(self, rows):
        self._rows = rows

    def sheet_names(self):
        return ["시트1"]

    def used_values(self, sheet):
        return self._rows, "A1"

    def range_values(self, sheet, address):
        return _slice_rows(self._rows, address)

    def column_values(self, sheet, column, max_rows=5000):
        return [r[0] for r in self._rows]


def test_aggregate_rectangular_range_sums_all_columns_and_reports_count():
    rows = [
        [1, 2, 3],
        [4, 5, 6],
    ]
    wb = _RectWorkbook(rows)
    tools = make_excel_tools(wb)
    out = _tool(tools, "aggregate").invoke(
        {"sheet": "시트1", "address": "A1:C2", "op": "sum"}
    )
    # 한 열만 집계했다면(예 A열만) 5 가 나왔을 것 — 6개 셀 전체(1+2+3+4+5+6=21)가
    # 더해졌는지로 사각형 범위가 실제로 반영됐는지 확인한다.
    assert "21" in out
    assert "숫자셀 6개" in out


# --- find_value: 200건 초과 잘림 신호 (Group A1 회귀) ---


class _FindValueWorkbook:
    """find_value 가 쓰는 used_values 만 제공하는 최소 가짜."""

    def __init__(self, rows):
        self._rows = rows

    def used_values(self, sheet):
        return self._rows, "A1"


def test_find_value_truncates_over_200_hits_with_note():
    rows = [[f"항목{i}"] for i in range(205)]  # 205건 모두 '항목' 부분일치
    wb = _FindValueWorkbook(rows)
    tools = make_excel_tools(wb)
    out = _tool(tools, "find_value").invoke({"sheet": "시트1", "query": "항목"})
    assert "200건" in out
    assert "200건까지만 표시" in out


def test_find_value_exact_200_hits_has_no_truncation_note():
    rows = [[f"항목{i}"] for i in range(200)]  # 정확히 200건 — 잘림이 아니다
    wb = _FindValueWorkbook(rows)
    tools = make_excel_tools(wb)
    out = _tool(tools, "find_value").invoke({"sheet": "시트1", "query": "항목"})
    assert "200건" in out
    assert "까지만 표시" not in out
