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
        full_rows = wb.range_values(sheet, address)  # COM 경계는 한 번만 넘는다
        total_rows = len(full_rows)
        total_cols = max((len(r) for r in full_rows), default=0)
        rows = [r[:_MAX_COLS] for r in full_rows[:_MAX_ROWS]]
        top_left = address.split(":")[0]
        note = ""
        if total_rows > _MAX_ROWS:
            note += f"\n(주의: {_MAX_ROWS}행까지만 표시. 나머지는 범위를 나눠 다시 읽어라.)"
        if total_cols > _MAX_COLS:
            note += f"\n(주의: {_MAX_COLS}열까지만 표시. 나머지는 범위를 나눠 다시 읽어라.)"
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
