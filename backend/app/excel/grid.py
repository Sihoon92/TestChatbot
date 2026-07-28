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


# 빈 셀 자리표시자. 빈 문자열로 두면 렌더 결과에 탭이 연달아 붙는데
# ("4\t\tNo"), LLM 은 연속 구분자 사이의 빈 자리를 세지 못해 그 열을 건너뛰고
# 이후 모든 열 문자가 한 칸씩 밀린다. 실제로 A 열이 비어 있는 시트에서
# gemma4:26b 가 필드 11개를 이름으로는 전부 맞히고도 열 문자는 11개 전부 한 칸씩
# 왼쪽으로 지목했다(mold_no 를 I 가 아니라 H 로). 4B 모델도 같은 패턴이었으니
# 모델 크기가 아니라 렌더링이 원인이다.
#
# 값으로 오해되지 않으면서 한 글자인 기호를 쓴다 — 숫자·문자·하이픈은 실제
# 셀 값일 수 있고, 여러 글자면 폭이 들쭉날쭉해진다.
EMPTY_CELL = "·"


def format_grid(values: list[list], top_left: str) -> str:
    """2D 값 목록을 '열문자 헤더 + 행번호' 가 붙은 탭 구분 표 문자열로.

    이 함수의 존재 이유는 **열 문자와 값의 정렬**이다. 에이전트는 여기서 읽은
    열 문자를 그대로 레이아웃에 담고, 파서가 그 주소로 값을 꺼낸다. 정렬이
    한 칸이라도 어긋나면 전혀 다른 열의 값이 대시보드에 올라간다.

    그래서 두 가지를 보장한다.
    - 빈 셀을 EMPTY_CELL 로 채워 연속 구분자가 생기지 않게 한다.
    - 짧은 행을 가장 긴 행에 맞춰 패딩한다. 안 그러면 그 행의 뒤쪽 열이 통째로
      사라져 헤더의 열 문자와 값의 인덱스가 어긋난다.
    """
    base_row, base_col = parse_a1(top_left)
    if not values:
        return "(빈 범위)"
    ncols = max(len(r) for r in values)
    header = "\t" + "\t".join(col_to_letter(base_col + c) for c in range(ncols))
    lines = [header]
    for r, row in enumerate(values):
        cells = [EMPTY_CELL if v is None else str(v) for v in row]
        cells.extend([EMPTY_CELL] * (ncols - len(cells)))
        lines.append(f"{base_row + r}\t" + "\t".join(cells))
    return "\n".join(lines)


def outline_grid(
    values: list[list],
    top_left: str,
    *,
    max_cells: int = 4,
    max_len: int = 24,
) -> str:
    """시트의 행별 윤곽 — 각 행에 값이 몇 칸 있고 앞쪽 값이 무엇인지.

    read_range 는 컨텍스트를 지키려고 30행까지만 보여준다. 그런데 실물 IQC
    시트는 40행이고 정작 필요한 대장 상세표가 33행부터라, 한 번 읽어서는 그
    표가 아예 안 보였다 — 에이전트가 "나머지는 나눠 읽어라"는 안내를 받고도
    두 번째 읽기를 하지 않아 표 하나가 통째로 누락됐다.

    값을 전부 싣는 대신 **모양만** 보여주면 시트 전체가 한 화면에 들어온다.
    표의 경계는 "채워진 칸의 조합이 달라지는 지점"에 드러나므로, 같은 조합이
    이어지는 행은 한 줄로 접는다 — 94행짜리 MES 시트가 네 줄이 된다.

    출력 예:
          2   1칸  B=MES 생산 이벤트 조회 결과 (2026-07)
          3   0칸  (빈 행)
          4  13칸  B=No · C=날짜 · D=공정 · E=기종
       5-95  13칸  B=1.0 · C=2026-07-01 00:00:00 · D=음극 성형 · E=H104
    """
    base_row, base_col = parse_a1(top_left)
    if not values:
        return "(빈 범위)"

    def _kind(v: Any) -> str:
        # bool 은 int 의 하위 타입이라 숫자보다 먼저, datetime 은 date 의
        # 하위 타입이라 date 보다 먼저 본다.
        if isinstance(v, bool):
            return "b"
        if isinstance(v, _NUMERIC):
            return "n"
        if isinstance(v, (datetime, date)):
            return "d"
        return "s"

    def _shape(row: list) -> tuple[tuple[str, str], ...]:
        """어느 칸이 어떤 타입으로 채워졌는지. 이게 같으면 같은 모양이다.

        열 위치만 보면 헤더 행과 그 아래 데이터 행이 같은 모양이 되어 한 줄로
        접히고, 정작 필요한 "헤더가 몇 행인가"가 사라진다. 헤더는 보통 전부
        문자열이고 데이터에는 숫자·날짜가 섞이므로, 타입까지 넣으면 그 경계가
        저절로 드러난다.
        """
        return tuple(
            (col_to_letter(base_col + c), _kind(v))
            for c, v in enumerate(row)
            if v is not None
        )

    def _sample(row: list) -> str:
        filled = [
            (col_to_letter(base_col + c), v) for c, v in enumerate(row) if v is not None
        ]
        if not filled:
            return "(빈 행)"
        parts = []
        for letter, value in filled[:max_cells]:
            text = str(value)
            if len(text) > max_len:
                text = text[:max_len] + "…"
            parts.append(f"{letter}={text}")
        return " · ".join(parts)

    lines: list[str] = []
    start = 0
    for i in range(1, len(values) + 1):
        # 모양이 바뀌는 지점(또는 끝)에서 지금까지의 묶음을 한 줄로 낸다.
        if i < len(values) and _shape(values[i]) == _shape(values[start]):
            continue
        first, last = base_row + start, base_row + i - 1
        label = str(first) if first == last else f"{first}-{last}"
        lines.append(
            f"{label:>7}  {len(_shape(values[start])):>2}칸  {_sample(values[start])}"
        )
        start = i
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
