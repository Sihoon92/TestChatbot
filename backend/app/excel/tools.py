"""엑셀 탐색·분석 도구 6종(LangChain @tool).

ReAct 에이전트가 호출한다. workbook(값 읽기)과 grid(순수 계산)를 조합만 한다.
반환은 항상 사람이/LLM 이 읽기 좋은 짧은 문자열(컨텍스트 보호를 위해 잘라서 준다).
"""
import json

from langchain_core.tools import tool

from app.excel.grid import (
    aggregate_values,
    format_grid,
    outline_grid,
    numeric_count,
    profile_values,
    search_values,
)

_MAX_ROWS = 30
_MAX_COLS = 30
_FIND_VALUE_LIMIT = 200  # search_values 의 기본 limit 과 맞춰둔다


def make_excel_tools(wb) -> list:
    """열려 있는 workbook(wb)에 바인딩된 도구 리스트를 만든다."""

    @tool
    def list_sheets() -> str:
        """워크북의 시트 이름 목록을 돌려준다. 분석은 항상 이 도구로 시작하라."""
        return "시트 목록: " + ", ".join(wb.sheet_names())

    @tool
    def sheet_outline(sheet: str) -> str:
        """시트 전체의 행별 윤곽 — 각 행에 값이 몇 칸 있고 앞쪽 값이 무엇인지.
        표가 몇 행에서 시작하고 끝나는지, 헤더가 몇 행인지 한눈에 보여준다.
        **무엇을 읽을지 정하기 전에 이 도구를 먼저 호출하라.**"""
        rows, top_left = wb.used_values(sheet)
        return outline_grid(rows, top_left)

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
        # search_values 의 기본 상한(200)보다 하나 더 요청해, 실제로 200건을
        # 넘겼는지(잘림)와 정확히 200건이라 안 잘렸는지를 구분한다 — 그냥
        # limit=200 으로 부르면 두 경우가 똑같이 "200건" 으로 보여 어느 쪽인지
        # LLM 이 알 수 없다(read_range 등 이 계층의 다른 잘림 신호는 항상
        # "총 개수 vs 표시 개수" 를 비교해 알려준다).
        hits = search_values(rows, query, top_left, limit=_FIND_VALUE_LIMIT + 1)
        if not hits:
            return f"'{query}' 를 찾지 못했다."
        truncated = len(hits) > _FIND_VALUE_LIMIT
        if truncated:
            hits = hits[:_FIND_VALUE_LIMIT]
        note = ""
        if truncated:
            note = (
                f"\n(주의: {_FIND_VALUE_LIMIT}건까지만 표시. "
                "검색어를 좁혀 다시 찾아라.)"
            )
        return (
            f"'{query}' {len(hits)}건: "
            + ", ".join(f"{h['cell']}={h['value']}" for h in hits)
            + note
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
        """범위(예 'E2:J20' 처럼 여러 열·행에 걸친 사각형 범위도 가능)의 숫자
        셀에 대해 sum|mean|min|max|count 를 계산한다.
        직접 더하지 말고 반드시 이 도구로 계산하라."""
        values = [v for row in wb.range_values(sheet, address) for v in row]
        try:
            result = aggregate_values(values, op)
        except ValueError as exc:
            return f"오류: {exc}"
        # 결과 숫자만 주면 좁은 열 하나만 집계했는지 의도한 범위를 전부
        # 집계했는지 LLM 도, 이 답을 보는 사람도 판단할 근거가 없다. 몇 개의
        # 숫자 셀을 근거로 삼았는지 함께 보여준다(계산은 여전히
        # aggregate_values 하나뿐 — numeric_count 는 세기만 한다).
        n = numeric_count(values)
        return f"{sheet}!{address} {op} = {result} (숫자셀 {n}개)"

    # sheet_outline 을 앞에 둔다 — 이 도구를 먼저 부르는 것이 권장 절차이고,
    # 목록 순서가 모델의 선택에 영향을 준다.
    return [list_sheets, sheet_outline, read_range, find_value, column_profile, aggregate]
