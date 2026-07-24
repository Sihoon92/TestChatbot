# Excel 분석 ReAct 에이전트 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 임의의 엑셀 파일을 주면 LLM이 스스로 도구를 호출하며 구조(헤더·날짜열·소계행)를 파악하고 내용을 분석하는 ReAct 에이전트를 만든다.

**Architecture:** LLM이 엑셀 전체를 프롬프트로 받지 않고, "일부만 들여다보고 → 추론 → 또 들여다보는" ReAct 루프로 동작한다. 도구는 `xlwings`(실제 Excel COM)로 셀을 읽고, 계산은 순수 파이썬 헬퍼가 담당한다(LLM에게 산수를 시키지 않는다). 순수 로직(`grid.py`)과 Excel I/O(`workbook.py`)를 분리해, Excel 없이도 로직을 단위 테스트할 수 있게 한다.

**Tech Stack:** Python 3.11+, xlwings, LangChain(`@tool`, `bind_tools`), LangGraph `create_react_agent`, 기존 `app.llm.get_chat_model`(사내 gemma / ChatOpenAI 호환).

## Global Constraints

- **openpyxl 사용 금지** — 엑셀 접근은 반드시 `xlwings` 로만 한다(사내 정책).
- **Excel 설치 전제** — xlwings 는 호스트에 Microsoft Excel 이 설치돼 있어야 동작한다. Excel COM 이 없는 환경(CI 등)에서는 xlwings 통합 테스트를 `skip` 한다.
- **Python >= 3.11**.
- **LLM 클라이언트는 신규 생성 금지** — 항상 `app.llm.get_chat_model(settings)` 를 재사용한다.
- **시스템 프롬프트는 입력 메시지로 주입** — `create_react_agent` 의 버전별 파라미터(`state_modifier`/`prompt`) 차이를 피하려고, 시스템 지시는 `SystemMessage` 로 `messages` 앞에 붙인다.
- **사전점검 통과가 전제** — `backend/examples/verify_tool_calling.py` 가 `[1]/[2] PASS` 여야 이 계획을 실행한다. FAIL 이면 "폴백" 섹션(덤프+프롬프트)으로 전환한다.
- **컨텍스트 보호** — 도구 반환은 항상 잘라서 준다(`read_range` 최대 30행×30열, `find_value` 최대 200건, 샘플값 최대 5개).
- **MVP 도구는 5개** — `list_sheets`, `read_range`, `find_value`, `column_profile`, `aggregate`. 그 외는 "향후 단계" 섹션으로 미룬다(YAGNI).

---

## File Structure

- `backend/app/excel/__init__.py` — 패키지 export.
- `backend/app/excel/grid.py` — 순수 헬퍼(Excel 불필요, 완전 단위테스트): A1 좌표 계산, 그리드 렌더, 검색, 열 프로파일, 집계.
- `backend/app/excel/workbook.py` — xlwings 세션 래퍼: `open_workbook` 컨텍스트매니저 + `Workbook` 클래스(값 읽기만).
- `backend/app/excel/tools.py` — `make_excel_tools(wb)` → 5개 LangChain `@tool`. workbook 읽기 + grid 헬퍼를 조합.
- `backend/app/excel/agent.py` — `build_excel_agent`, `EXCEL_SYSTEM_PROMPT`, `run_excel_agent`.
- `backend/examples/analyze_excel.py` — 실행 진입점(파일 경로 + 질문 → 분석 출력).
- `backend/tests/test_excel_grid.py` — grid.py 단위테스트(Excel 불필요).
- `backend/tests/test_excel_workbook.py` — xlwings 통합테스트(Excel 없으면 skip).
- `backend/tests/test_excel_tools.py` — 도구가 grid 헬퍼와 올바르게 연결되는지(가짜 Workbook 으로 단위테스트).
- `backend/pyproject.toml` — `xlwings` 의존성 추가.

**예시 엑셀 구조(분석 대상):** 1–4열 = 라인/제품명 등 메타데이터, 5열부터 헤더가 날짜, 각 셀은 (라인,제품,일자)별 jobchange 수치, 특정 행은 라인별 JC "소계".

---

## Task 1: 순수 그리드 헬퍼 (Excel 불필요)

**Files:**
- Create: `backend/app/excel/__init__.py`
- Create: `backend/app/excel/grid.py`
- Test: `backend/tests/test_excel_grid.py`

**Interfaces:**
- Consumes: 없음(표준 라이브러리만).
- Produces:
  - `col_to_letter(col_idx: int) -> str` (1→"A", 27→"AA")
  - `parse_a1(addr: str) -> tuple[int, int]` ("B3"→(3, 2), 즉 (row, col), 1-기반)
  - `a1_offset(top_left: str, row_off: int, col_off: int) -> str` ("A1",7,1→"B8")
  - `format_grid(values: list[list], top_left: str) -> str` (열문자 헤더 + 행번호가 붙은 탭 구분 표)
  - `search_values(rows: list[list], query: str, top_left: str, limit: int = 200) -> list[dict]` (`[{"cell":"B8","value":...}]`)
  - `profile_values(values: list, sample_limit: int = 5) -> dict` (`{"count","nonnull","nulls","unique","types","samples"}`)
  - `aggregate_values(values: list, op: str) -> float` (op ∈ {"sum","mean","min","max","count"})

- [ ] **Step 1: Write the failing test**

`backend/tests/test_excel_grid.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .\.venv\Scripts\python.exe -m pytest tests/test_excel_grid.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.excel'`

- [ ] **Step 3: Write minimal implementation**

`backend/app/excel/__init__.py`:
```python
```

`backend/app/excel/grid.py`:
```python
"""엑셀 값에 대한 순수 로직(좌표 계산·렌더·검색·프로파일·집계).

Excel/xlwings 에 의존하지 않는다 → Excel 없이도 단위 테스트할 수 있다.
LLM 이 산수를 하지 않도록 계산은 여기서 담당한다.
"""
from datetime import date, datetime
from typing import Any

_NUMERIC = (int, float)


def col_to_letter(col_idx: int) -> str:
    """1-기반 열 인덱스를 엑셀 열문자로 (1→'A', 27→'AA')."""
    letters = ""
    while col_idx > 0:
        col_idx, rem = divmod(col_idx - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _letter_to_col(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


def parse_a1(addr: str) -> tuple[int, int]:
    """'B3' → (row=3, col=2). 좌표 접두($) 는 무시."""
    addr = addr.replace("$", "").strip()
    i = 0
    while i < len(addr) and addr[i].isalpha():
        i += 1
    return int(addr[i:]), _letter_to_col(addr[:i])


def a1_offset(top_left: str, row_off: int, col_off: int) -> str:
    row, col = parse_a1(top_left)
    return f"{col_to_letter(col + col_off)}{row + row_off}"


def format_grid(values: list[list], top_left: str) -> str:
    """2D 값 목록을 '열문자 헤더 + 행번호' 가 붙은 탭 구분 표 문자열로."""
    base_row, base_col = parse_a1(top_left)
    if not values:
        return "(빈 범위)"
    ncols = max(len(r) for r in values)
    header = "\t" + "\t".join(col_to_letter(base_col + c) for c in range(ncols))
    lines = [header]
    for r, row in enumerate(values):
        cells = "\t".join("" if v is None else str(v) for v in row)
        lines.append(f"{base_row + r}\t{cells}")
    return "\n".join(lines)


def search_values(
    rows: list[list], query: str, top_left: str, limit: int = 200
) -> list[dict]:
    """2D 값에서 query(부분일치, 대소문자 무시)를 찾아 셀 주소와 값을 돌려준다."""
    q = query.strip().lower()
    hits: list[dict] = []
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            if val is None:
                continue
            if q in str(val).lower():
                hits.append({"cell": a1_offset(top_left, r, c), "value": val})
                if len(hits) >= limit:
                    return hits
    return hits


def profile_values(values: list, sample_limit: int = 5) -> dict:
    """한 열(1D) 값들의 개수/빈칸/고유수/타입분포/샘플을 요약."""
    types: dict[str, int] = {}
    nonnull: list[Any] = []
    for v in values:
        if v is None:
            continue
        nonnull.append(v)
        types[_type_name(v)] = types.get(_type_name(v), 0) + 1
    uniq = {str(v) for v in nonnull}
    return {
        "count": len(values),
        "nonnull": len(nonnull),
        "nulls": len(values) - len(nonnull),
        "unique": len(uniq),
        "types": types,
        "samples": [v for v in nonnull[:sample_limit]],
    }


def _type_name(v: Any) -> str:
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (datetime, date)):
        return "datetime"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    return "other"


def aggregate_values(values: list, op: str) -> float:
    """숫자 셀만 골라 sum/mean/min/max/count. bool 은 숫자에서 제외."""
    nums = [v for v in values if isinstance(v, _NUMERIC) and not isinstance(v, bool)]
    if op == "count":
        return float(len(nums))
    if not nums:
        return 0.0
    if op == "sum":
        return float(sum(nums))
    if op == "mean":
        return float(sum(nums) / len(nums))
    if op == "min":
        return float(min(nums))
    if op == "max":
        return float(max(nums))
    raise ValueError(f"지원하지 않는 연산: {op} (sum|mean|min|max|count)")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .\.venv\Scripts\python.exe -m pytest tests/test_excel_grid.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/excel/__init__.py backend/app/excel/grid.py backend/tests/test_excel_grid.py
git commit -m "feat(excel): 순수 그리드 헬퍼(좌표/렌더/검색/프로파일/집계) 추가"
```

---

## Task 2: xlwings 워크북 래퍼

**Files:**
- Create: `backend/app/excel/workbook.py`
- Modify: `backend/pyproject.toml:6-18` (dependencies 에 `xlwings` 추가)
- Test: `backend/tests/test_excel_workbook.py`

**Interfaces:**
- Consumes: 없음.
- Produces:
  - `open_workbook(path: str) -> ContextManager[Workbook]` (Excel App 을 숨김으로 띄우고, 읽기전용으로 열고, 끝나면 book.close + app.quit)
  - `class Workbook`:
    - `sheet_names() -> list[str]`
    - `used_shape(sheet: str) -> tuple[int, int]` (사용범위 (행수, 열수))
    - `range_values(sheet: str, address: str) -> list[list]` (항상 2D 로 정규화)
    - `used_values(sheet: str) -> tuple[list[list], str]` (사용범위 전체 2D + 좌상단 A1 주소)
    - `column_values(sheet: str, column: str, max_rows: int = 5000) -> list` (열문자 1개의 1D 값)

- [ ] **Step 1: Add xlwings dependency**

`backend/pyproject.toml` 의 `dependencies` 배열에 한 줄 추가:
```toml
    "xlwings>=0.31",
```
그리고 설치:
```bash
cd backend && .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

- [ ] **Step 2: Write the failing integration test**

`backend/tests/test_excel_workbook.py`:
```python
import pytest

xw = pytest.importorskip("xlwings")

from app.excel.workbook import open_workbook  # noqa: E402


def _excel_available() -> bool:
    try:
        app = xw.App(visible=False)
        app.quit()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _excel_available(), reason="Excel COM 미설치")


def test_open_and_read(tmp_path):
    path = tmp_path / "sample.xlsx"
    app = xw.App(visible=False)
    try:
        wb = app.books.add()
        sht = wb.sheets[0]
        sht.name = "데이터"
        sht.range("A1").value = [["라인", "제품", "2026-01-01"], ["A", "제품1", 3]]
        wb.save(str(path))
        wb.close()
    finally:
        app.quit()

    with open_workbook(str(path)) as book:
        assert "데이터" in book.sheet_names()
        rows, top_left = book.used_values("데이터")
        assert top_left == "A1"
        assert rows[0][0] == "라인"
        assert book.column_values("데이터", "A")[:2] == ["라인", "A"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && .\.venv\Scripts\python.exe -m pytest tests/test_excel_workbook.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.excel.workbook'` (Excel 없으면 SKIPPED — 그 경우 Step 4 는 Excel 있는 PC 에서 확인)

- [ ] **Step 4: Write minimal implementation**

`backend/app/excel/workbook.py`:
```python
"""xlwings 로 엑셀을 읽기 전용으로 여는 얇은 래퍼.

openpyxl 을 못 쓰는 환경을 위해 실제 Excel(COM) 을 구동한다. App 을 숨김으로
띄우고, 끝나면 반드시 닫아 유령 EXCEL.EXE 프로세스가 남지 않게 한다.
값 읽기만 제공한다(수정/저장 없음).
"""
from contextlib import contextmanager
from typing import Any, Iterator

import xlwings as xw

from app.excel.grid import col_to_letter


def _to_2d(value: Any) -> list[list]:
    """xlwings 반환(스칼라/1D/2D)을 항상 2D 리스트로 정규화."""
    if value is None:
        return [[None]]
    if not isinstance(value, list):
        return [[value]]
    if value and isinstance(value[0], list):
        return value
    return [value]


class Workbook:
    def __init__(self, book: "xw.Book") -> None:
        self._book = book

    def sheet_names(self) -> list[str]:
        return [s.name for s in self._book.sheets]

    def _sheet(self, sheet: str) -> "xw.Sheet":
        return self._book.sheets[sheet]

    def used_shape(self, sheet: str) -> tuple[int, int]:
        rng = self._sheet(sheet).used_range
        return (rng.rows.count, rng.columns.count)

    def range_values(self, sheet: str, address: str) -> list[list]:
        return _to_2d(self._sheet(sheet).range(address).value)

    def used_values(self, sheet: str) -> tuple[list[list], str]:
        rng = self._sheet(sheet).used_range
        top_left = rng[0, 0].get_address(False, False)  # 예: "A1"
        return _to_2d(rng.value), top_left

    def column_values(self, sheet: str, column: str, max_rows: int = 5000) -> list:
        sht = self._sheet(sheet)
        nrows = min(sht.used_range.rows.count, max_rows)
        rng = sht.range(f"{column}1:{column}{nrows}")
        flat = [row[0] for row in _to_2d(rng.value)]
        return flat


@contextmanager
def open_workbook(path: str) -> Iterator[Workbook]:
    app = xw.App(visible=False, add_book=False)
    app.display_alerts = False
    app.screen_updating = False
    try:
        book = app.books.open(path, read_only=True, update_links=False)
        try:
            yield Workbook(book)
        finally:
            book.close()
    finally:
        app.quit()


__all__ = ["open_workbook", "Workbook", "col_to_letter"]
```

- [ ] **Step 5: Run test to verify it passes (Excel 있는 PC 에서)**

Run: `cd backend && .\.venv\Scripts\python.exe -m pytest tests/test_excel_workbook.py -v`
Expected: PASS (Excel 없으면 SKIPPED — 정상)

- [ ] **Step 6: Commit**

```bash
git add backend/app/excel/workbook.py backend/tests/test_excel_workbook.py backend/pyproject.toml
git commit -m "feat(excel): xlwings 읽기전용 워크북 래퍼 + 통합테스트 추가"
```

---

## Task 3: LangChain 도구 5종

**Files:**
- Create: `backend/app/excel/tools.py`
- Test: `backend/tests/test_excel_tools.py`

**Interfaces:**
- Consumes: `Workbook`(Task 2, 덕타이핑으로 `sheet_names`/`range_values`/`used_values`/`column_values` 만 사용), grid 헬퍼(Task 1).
- Produces:
  - `make_excel_tools(wb) -> list` — 아래 5개 `@tool` 을 wb 에 바인딩해 리스트로 반환:
    - `list_sheets() -> str`
    - `read_range(sheet: str, address: str) -> str`
    - `find_value(sheet: str, query: str) -> str`
    - `column_profile(sheet: str, column: str) -> str`
    - `aggregate(sheet: str, address: str, op: str) -> str`

- [ ] **Step 1: Write the failing test (가짜 Workbook 으로 단위테스트 — Excel 불필요)**

`backend/tests/test_excel_tools.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .\.venv\Scripts\python.exe -m pytest tests/test_excel_tools.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.excel.tools'`

- [ ] **Step 3: Write minimal implementation**

`backend/app/excel/tools.py`:
```python
"""엑셀 탐색·분석 도구 5종(LangChain @tool).

ReAct 에이전트가 호출한다. workbook(값 읽기)과 grid(순수 계산)를 조합만 한다.
반환은 항상 사람이/LLM 이 읽기 좋은 짧은 문자열(컨텍스트 보호를 위해 잘라서 준다).
"""
import json

from langchain_core.tools import tool

from app.excel.grid import (
    aggregate_values,
    format_grid,
    profile_values,
    search_values,
)

_MAX_ROWS = 30
_MAX_COLS = 30


def make_excel_tools(wb) -> list:
    """열려 있는 workbook(wb)에 바인딩된 도구 리스트를 만든다."""

    @tool
    def list_sheets() -> str:
        """워크북의 시트 이름 목록을 돌려준다. 분석은 항상 이 도구로 시작하라."""
        return "시트 목록: " + ", ".join(wb.sheet_names())

    @tool
    def read_range(sheet: str, address: str) -> str:
        """시트의 특정 범위(예 'A1:H12')를 열문자·행번호가 붙은 표로 보여준다.
        구조(헤더가 어디인지, 어느 열이 날짜인지)를 파악할 때 좌상단부터 조금씩 읽어라."""
        rows = wb.range_values(sheet, address)
        rows = [r[:_MAX_COLS] for r in rows[:_MAX_ROWS]]
        top_left = address.split(":")[0]
        note = ""
        if len(wb.range_values(sheet, address)) > _MAX_ROWS:
            note = f"\n(주의: {_MAX_ROWS}행까지만 표시. 나머지는 범위를 나눠 다시 읽어라.)"
        return format_grid(rows, top_left) + note

    @tool
    def find_value(sheet: str, query: str) -> str:
        """시트에서 문자열(부분일치)을 찾아 셀 주소 목록을 돌려준다.
        예: '소계'/'합계' 를 찾아 소계행 위치를 파악할 때 쓴다."""
        rows, top_left = wb.used_values(sheet)
        hits = search_values(rows, query, top_left)
        if not hits:
            return f"'{query}' 를 찾지 못했다."
        return f"'{query}' {len(hits)}건: " + ", ".join(
            f"{h['cell']}={h['value']}" for h in hits
        )

    @tool
    def column_profile(sheet: str, column: str) -> str:
        """한 열(열문자, 예 'A')의 값 개수/빈칸/고유수/타입분포/샘플을 요약한다.
        어느 열이 라인명·제품명·날짜·숫자지표인지 판정할 때 쓴다."""
        values = wb.column_values(sheet, column)
        prof = profile_values(values)
        return f"{sheet}!{column}열 프로파일: " + json.dumps(
            prof, ensure_ascii=False, default=str
        )

    @tool
    def aggregate(sheet: str, address: str, op: str) -> str:
        """범위(예 'C2:C50')의 숫자 셀에 대해 sum|mean|min|max|count 를 계산한다.
        직접 더하지 말고 반드시 이 도구로 계산하라."""
        values = [v for row in wb.range_values(sheet, address) for v in row]
        try:
            result = aggregate_values(values, op)
        except ValueError as exc:
            return f"오류: {exc}"
        return f"{sheet}!{address} {op} = {result}"

    return [list_sheets, read_range, find_value, column_profile, aggregate]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .\.venv\Scripts\python.exe -m pytest tests/test_excel_tools.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/excel/tools.py backend/tests/test_excel_tools.py
git commit -m "feat(excel): LLM 탐색·분석 도구 5종(list/read/find/profile/aggregate) 추가"
```

---

## Task 4: ReAct 에이전트 조립

**Files:**
- Create: `backend/app/excel/agent.py`
- Modify: `backend/app/excel/__init__.py` (export 추가)

**Interfaces:**
- Consumes: `make_excel_tools`(Task 3), `create_react_agent`(langgraph), `get_chat_model`(app.llm).
- Produces:
  - `EXCEL_SYSTEM_PROMPT: str`
  - `build_excel_agent(model, wb)` — `create_react_agent(model, make_excel_tools(wb))` 반환
  - `run_excel_agent(model, wb, question: str) -> dict` — `{"answer": str, "tool_calls": list[str]}` (시스템 프롬프트를 SystemMessage 로 주입해 실행하고, 최종 답 + 사용한 도구 목록을 뽑아준다)

- [ ] **Step 1: Write implementation**

`backend/app/excel/agent.py`:
```python
"""엑셀 분석 ReAct 에이전트.

LLM 이 도구를 호출하며 스스로 구조를 파악하도록 시스템 프롬프트로 절차를 안내한다.
시스템 지시는 create_react_agent 의 버전별 파라미터 차이를 피하려고 입력 메시지
앞에 SystemMessage 로 붙인다.
"""
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

from app.excel.tools import make_excel_tools

EXCEL_SYSTEM_PROMPT = """너는 엑셀 데이터 분석가다. 엑셀 내용을 한 번에 다 볼 수 없으므로,
반드시 도구를 호출해 조금씩 살펴보며 스스로 구조를 파악한 뒤 분석한다.

권장 절차:
1) list_sheets 로 시트를 확인한다.
2) read_range 로 좌상단(예 A1:J6)부터 읽어 헤더 위치와 각 열의 의미를 파악한다.
   (예: 앞쪽 열은 라인/제품 같은 메타데이터, 뒤쪽 열 헤더는 날짜일 수 있다.)
3) column_profile 로 각 열이 라인명/제품명/날짜/숫자지표 중 무엇인지 확인한다.
4) find_value 로 '소계','합계' 같은 키워드 위치를 찾아 '집계행'을 식별한다.
   집계행은 원시 데이터 합산에서 제외해야 이중 계산을 피한다.
5) 숫자 계산은 직접 하지 말고 반드시 aggregate 도구로 한다.

규칙:
- 셀 값을 눈으로 더하지 마라. 합/평균/개수는 항상 aggregate 를 사용한다.
- 근거가 된 셀 주소(예 C2:C50, 소계행 위치)를 답변에 함께 제시한다.
- 확신이 없으면 범위를 더 읽어 확인한 뒤 결론을 낸다.
"""


def build_excel_agent(model, wb):
    """열린 workbook(wb)에 바인딩된 ReAct 에이전트를 만든다."""
    return create_react_agent(model, make_excel_tools(wb))


def run_excel_agent(model, wb, question: str) -> dict:
    """에이전트를 돌려 최종 답과 사용한 도구 목록을 돌려준다."""
    agent = build_excel_agent(model, wb)
    result = agent.invoke(
        {
            "messages": [
                SystemMessage(content=EXCEL_SYSTEM_PROMPT),
                HumanMessage(content=question),
            ]
        }
    )
    msgs = result["messages"]
    tool_calls: list[str] = []
    for m in msgs:
        for call in getattr(m, "tool_calls", None) or []:
            tool_calls.append(call.get("name", "?"))
    return {"answer": getattr(msgs[-1], "content", ""), "tool_calls": tool_calls}
```

`backend/app/excel/__init__.py` 를 다음으로 교체:
```python
from app.excel.agent import EXCEL_SYSTEM_PROMPT, build_excel_agent, run_excel_agent
from app.excel.tools import make_excel_tools
from app.excel.workbook import Workbook, open_workbook

__all__ = [
    "open_workbook",
    "Workbook",
    "make_excel_tools",
    "build_excel_agent",
    "run_excel_agent",
    "EXCEL_SYSTEM_PROMPT",
]
```

- [ ] **Step 2: Smoke import 확인**

Run: `cd backend && .\.venv\Scripts\python.exe -c "from app.excel import run_excel_agent, open_workbook; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add backend/app/excel/agent.py backend/app/excel/__init__.py
git commit -m "feat(excel): ReAct 엑셀 분석 에이전트(시스템 프롬프트 포함) 조립"
```

---

## Task 5: 실행 진입점 + 엔드투엔드 확인

**Files:**
- Create: `backend/examples/analyze_excel.py`

**Interfaces:**
- Consumes: `open_workbook`, `run_excel_agent`(Task 4), `get_chat_model`, `get_settings`.
- Produces: CLI — `python examples/analyze_excel.py <파일경로> "<질문>"`

- [ ] **Step 1: Write implementation**

`backend/examples/analyze_excel.py`:
```python
"""엑셀 파일을 ReAct 에이전트로 분석하는 실행 진입점.

실행 (backend/ 에서, venv 파이썬으로):
    python examples/analyze_excel.py data/jobchange.xlsx "라인별 JC 소계를 요약해줘"
    python examples/analyze_excel.py data/jobchange.xlsx "질문" --backend internal

전제:
- 사전점검(examples/verify_tool_calling.py)이 PASS 여야 한다.
- 이 PC 에 Microsoft Excel 이 설치돼 있어야 한다(xlwings COM).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from app.config import get_settings  # noqa: E402
from app.excel import open_workbook, run_excel_agent  # noqa: E402
from app.llm import get_chat_model  # noqa: E402


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) < 2:
        print('사용법: python examples/analyze_excel.py <파일경로> "<질문>" [--backend internal]')
        return 2
    path, question = args[0], args[1]

    backend = None
    if "--backend" in sys.argv:
        backend = sys.argv[sys.argv.index("--backend") + 1]

    settings = get_settings()
    if backend:
        settings = settings.model_copy(update={"llm_backend": backend})
    model = get_chat_model(settings)

    print(f"[분석] 파일={path}\n[질문] {question}\n" + "=" * 60)
    with open_workbook(path) as wb:
        out = run_excel_agent(model, wb, question)
    print("사용한 도구:", " → ".join(out["tool_calls"]) or "(없음)")
    print("=" * 60)
    print(out["answer"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: 수동 엔드투엔드 확인 (Excel + 사내 LLM 있는 PC)**

먼저 예시 엑셀(1–4열 메타데이터, 5열~ 날짜 헤더, 중간에 '소계'행)을 준비한 뒤:

Run:
```bash
cd backend
.\.venv\Scripts\python.exe examples/analyze_excel.py data/jobchange.xlsx "라인 A 의 JC 합계와 소계행 위치를 알려줘"
```
Expected: `사용한 도구:` 에 `list_sheets → read_range → find_value → aggregate` 류의 흐름이 찍히고, 답변에 소계행 주소와 aggregate 로 계산한 합계가 포함된다.

- [ ] **Step 3: 전체 회귀 테스트**

Run: `cd backend && .\.venv\Scripts\python.exe -m pytest -v`
Expected: grid/tools 단위테스트 PASS, workbook 통합테스트 PASS 또는 SKIP(Excel 유무에 따라)

- [ ] **Step 4: Commit**

```bash
git add backend/examples/analyze_excel.py
git commit -m "feat(excel): 엑셀 분석 CLI 진입점(analyze_excel) 추가"
```

---

## 향후 단계 (실무 고도화 — MVP 이후 별도 계획)

MVP(도구 5종)로 예시 시나리오를 커버한 뒤, 실무 견고성을 위해 아래를 단계적으로 추가한다:

1. **병합셀 인식** — `get_merged_cells(sheet)` 도구. 다중 행/열 헤더·그룹 경계 판별. (xlwings `sheet.api` 의 MergeArea 접근)
2. **다중 행 헤더 처리** — 헤더가 2행 이상일 때 열 의미를 합성.
3. **날짜 정규화** — 날짜 헤더를 실제 date 로 파싱해 기간 필터(`filter_by_date_range`) 지원.
4. **조건 필터 집계** — `aggregate` 에 `where`(예: 라인=="A") 추가 → 라인/제품별 소계를 도구가 직접 계산.
5. **대용량 페이지네이션** — `read_range` 자동 분할·요약, used_range 큰 시트 대비.
6. **소계행 자동 제외 휴리스틱** — '소계/합계/계' 키워드 + 병합셀 신호로 집계행을 자동 태깅해 이중계산 방지.
7. **웹 통합** — 파일 업로드 엔드포인트 + 기존 채팅 그래프에 엑셀 분석 노드로 편입(세션별 workbook 수명주기 관리).
8. **유령 프로세스 가드** — 예외/타임아웃 시에도 `app.quit()` 보장(EXCEL.EXE 누수 방지) + 동시 요청 직렬화.
9. **관측성** — 기존 DebugCallbackHandler 를 엑셀 에이전트에도 부착해 도구 호출/입출력 로그 남기기.

## 폴백 (사전점검 FAIL 시)

`verify_tool_calling.py` 가 FAIL 이면(모델이 tool_calls 를 못 만들면) ReAct 대신:
- workbook 래퍼로 시트 구조 요약(used_shape, 상단 몇 행, find_value('소계') 결과)을 **미리 만들어** 프롬프트에 넣고,
- LLM 에는 "이 구조 요약을 근거로 분석"만 시키는 **단일 호출** 방식으로 전환한다.
- 도구(grid/workbook)는 그대로 재사용 가능하므로 Task 1–2 는 폴백에서도 유효하다.

---

## Self-Review

- **Spec 커버리지:** LLM 자율 구조 파악(list_sheets/read_range/column_profile), 소계행 식별(find_value), 정확 계산(aggregate) → 예시 엑셀 시나리오의 4가지 요구(라인/제품 메타열, 날짜 헤더, 셀별 JC, 라인별 소계)를 모두 도구로 커버. xlwings 전용·ReAct·사전점검·실무 고도화 항목 반영됨.
- **Placeholder 스캔:** 모든 코드 스텝에 실제 코드 포함, TBD/TODO 없음.
- **타입 일관성:** `Workbook` 이 노출하는 `sheet_names/used_shape/range_values/used_values/column_values` 를 Task 3 도구가 동일 이름으로 소비. `make_excel_tools`→`build_excel_agent`→`run_excel_agent` 시그니처 일치. grid 헬퍼 이름(format_grid/search_values/profile_values/aggregate_values) Task 1 정의와 Task 3 사용 일치.
