from app.excel.tools import make_excel_tools


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
        return [[3], [3], [5]]  # C2:C4 흉내

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
