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


def numeric_count(values: list) -> int:
    """집계에 실제로 쓰인(숫자로 인정된) 셀 개수. bool 은 제외 — aggregate_values
    와 동일한 필터 기준.

    LLM 이 무슨 값을 넘겼는지도 모른 채 결과 숫자만 받으면, 좁은 열 하나만
    집계했는지 의도한 사각형 범위를 전부 집계했는지 구분할 근거가 없다.
    aggregate 도구가 결과와 함께 "숫자셀 n개" 를 보여줄 때 쓴다 — 값을 다시
    계산(sum/mean 등)하지 않고 개수만 세므로 "LLM 에게도, 도구 자체에도
    산수를 시키지 않는다" 는 aggregate_values 의 계약을 건드리지 않는다.
    """
    return sum(1 for v in values if isinstance(v, _NUMERIC) and not isinstance(v, bool))


def aggregate_values(values: list, op: str) -> float:
    """숫자 셀만 골라 sum/mean/min/max/count. bool 은 숫자에서 제외."""
    nums = [v for v in values if isinstance(v, _NUMERIC) and not isinstance(v, bool)]
    if op == "count":
        return float(len(nums))
    # FIX: validate op for non-count operations BEFORE checking if nums is empty,
    # so unknown ops always raise ValueError regardless of input
    if op not in {"sum", "mean", "min", "max"}:
        raise ValueError(f"지원하지 않는 연산: {op} (sum|mean|min|max|count)")
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
