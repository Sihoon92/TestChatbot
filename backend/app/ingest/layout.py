"""레이아웃을 격자와 대조한다 — 재사용 판정(앵커)과 제출 검증(누락).

에이전트가 제출한 anchors 는 "여기에 이 텍스트가 있으면 같은 양식이다" 라는
서술이다. 행이 추가되는 것은 앵커를 건드리지 않으므로 통과하고, 누가 컬럼을
바꾸면 즉시 불일치가 나서 에이전트가 재해석한다.

find_layout_gaps 는 방향이 반대다. 앵커가 "이 레이아웃을 다시 써도 되나" 라면
이쪽은 "이 레이아웃이 시트를 다 덮었나" 를 묻는다. 프롬프트로 완전성을
요구해도 모델은 표의 오른쪽 끝을 찍는데, 격자는 끝이 어디인지 알고 있다 —
아는 쪽이 확인해야 한다.

Excel 에도 LLM 에도 의존하지 않는다 — 격자 리터럴만으로 전부 테스트된다.
"""
from app.excel.grid import col_to_letter, parse_a1
from app.ingest.normalize import cell_to_text, normalize_text
from app.ingest.schemas import SheetLayout, TableBlock

# 검증에서 "표의 행"으로 볼 최소 채움 칸 수. 제목 줄(1칸)이나 표 꼬리의 소계
# (1~2칸)까지 표에 넣으라고 요구하면 에이전트가 만족시킬 수 없는 조건이 되어
# 왕복만 늘고 결국 아무 레이아웃도 못 얻는다.
_MIN_FILLED_FOR_TABLE_ROW = 3

# 누락 열을 알려줄 때 붙이는 헤더 텍스트의 최대 길이.
_HINT_MAX_LEN = 12


def cell_at(grid: list[list], top_left: str, cell: str) -> object | None:
    """절대 셀 주소로 격자에서 값을 꺼낸다.

    grid 는 used_range 의 값이고 top_left 는 그 좌상단 주소다 — used_range 는
    A1 에서 시작하지 않을 수 있으므로 오프셋을 빼야 한다. 범위 밖이면 None:
    앵커가 격자 밖을 가리키는 것은 예외 상황이 아니라 그냥 불일치다.
    """
    base_row, base_col = parse_a1(top_left)
    row, col = parse_a1(cell)
    r, c = row - base_row, col - base_col
    if r < 0 or c < 0 or r >= len(grid):
        return None
    line = grid[r]
    if c >= len(line):
        return None
    return line[c]


def anchors_match(grid: list[list], top_left: str, layout: SheetLayout) -> bool:
    """레이아웃의 앵커가 전부 일치하면 True.

    앵커가 3개 미만이면 False 다 — 판정 근거가 약한 레이아웃을 참으로 처리하면
    아무거나 재사용된다. 캐시가 (kind, sheet_name) 이 아니라 kind 로만 좁혀진
    지금은 이 판정이 그 kind 의 **모든 시트**에 대한 유일한 문지기라, 예전처럼
    "잘못 걸려도 시트 하나만 오염된다"는 안전망이 없다.

    이 최소 개수를 `SheetLayout.anchors` 에 Pydantic 제약(예: min_length=3)으로
    걸지 않는 이유: 그 모델은 에이전트 제출 검증뿐 아니라
    `registry.load_layouts` 의 역직렬화도 겸한다. 제약을 걸면 앵커가 3개
    미만으로 이미 저장된 옛 캐시 행을 읽을 때 `ValidationError` 가 터져
    레이아웃 로딩 전체가 죽는다. 여기 판정부에서만 거르면 그런 레이아웃은
    그냥 캐시 미스로 취급돼 다시 발견될 뿐이라 안전하게 퇴화한다.
    """
    if len(layout.anchors) < 3:
        return False
    for anchor in layout.anchors:
        actual = cell_to_text(cell_at(grid, top_left, anchor.cell))
        if normalize_text(actual) != normalize_text(anchor.text):
            return False
    return True


def pick_layout(
    grid: list[list], top_left: str, candidates: list[SheetLayout]
) -> SheetLayout | None:
    """후보 중 앵커가 일치하는 첫 레이아웃. 호출자는 최신순으로 넘긴다."""
    for layout in candidates:
        if anchors_match(grid, top_left, layout):
            return layout
    return None


def last_row_no(grid: list[list], top_left: str) -> int:
    """격자가 담고 있는 마지막 절대 행 번호."""
    base_row, _ = parse_a1(top_left)
    return base_row + len(grid) - 1


def _filled_cells(
    grid: list[list], top_left: str, row_no: int
) -> list[tuple[str, object]]:
    """그 행에서 값이 있는 (열문자, 값) 목록."""
    base_row, base_col = parse_a1(top_left)
    r = row_no - base_row
    if r < 0 or r >= len(grid):
        return []
    return [
        (col_to_letter(base_col + c), v)
        for c, v in enumerate(grid[r])
        if v is not None
    ]


def table_end_row(grid: list[list], top_left: str, table: TableBlock) -> int:
    """parse_rows 가 실제로 소비할 마지막 데이터 행.

    파서와 같은 규칙이어야 한다 — 다르면 검증은 "이 행은 덮였다" 고 보는데
    파서는 안 읽는, 검증을 통과하고도 데이터가 안 들어오는 상태가 생긴다.
    """
    hard_end = last_row_no(grid, top_left)
    if table.data_end_row is not None:
        return min(table.data_end_row, hard_end)
    # data_end_row 가 없으면 파서는 "매핑된 칸이 전부 빈 행" 에서 멈춘다.
    row_no = table.data_start_row
    while row_no <= hard_end:
        if all(
            cell_at(grid, top_left, f"{col.column}{row_no}") is None
            for col in table.columns
        ):
            break
        row_no += 1
    return row_no - 1


def _col_index(letter: str) -> int:
    return parse_a1(f"{letter}1")[1]


def _hint(value: object) -> str:
    text = cell_to_text(value) or ""
    return text[:_HINT_MAX_LEN] + "…" if len(text) > _HINT_MAX_LEN else text


def _as_ranges(numbers: list[int]) -> str:
    """[4,5,6,17,18] → '4-6, 17-18'. 행 번호를 사람이 읽을 형태로."""
    out: list[str] = []
    start = prev = numbers[0]
    for n in numbers[1:]:
        if n == prev + 1:
            prev = n
            continue
        out.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = n
    out.append(str(start) if start == prev else f"{start}-{prev}")
    return ", ".join(out)


def _missing_columns(
    grid: list[list], top_left: str, table: TableBlock
) -> list[str]:
    """detail 표에서 값이 있는데 columns 에 없는 열들(설명 문구 포함)."""
    declared = {col.column.strip().upper() for col in table.columns}
    seen: dict[str, object] = {}
    rows = [*table.header_rows, *range(
        table.data_start_row, table_end_row(grid, top_left, table) + 1
    )]
    for row_no in rows:
        for letter, value in _filled_cells(grid, top_left, row_no):
            # 헤더 행을 먼저 훑으므로 헤더 텍스트가 설명 문구로 남는다.
            seen.setdefault(letter, value)
    missing = sorted(
        (letter for letter in seen if letter not in declared), key=_col_index
    )
    return [f"{letter}({_hint(seen[letter])})" for letter in missing]


def find_layout_gaps(
    grid: list[list], top_left: str, layout: SheetLayout
) -> list[str]:
    """레이아웃이 시트에서 덮지 못한 곳을 사람이 읽을 문장으로 돌려준다.

    빈 목록이면 완전하다. 두 가지를 본다.
    1. detail 표 안에서 값이 있는데 매핑되지 않은 **열** — 그 열의 값은 그 뒤
       어디에도 나타나지 않고, 빠졌다는 사실조차 드러나지 않는다.
    2. 어느 표에도 들어가지 않은 **행** — 표를 통째로 놓친 경우다.

    summary 표는 파싱하지 않으므로 열 누락을 따지지 않는다. 요구를 넓히면
    에이전트가 만족시키느라 왕복만 늘어난다.
    """
    if not grid:
        return []

    problems: list[str] = []

    for table in layout.tables:
        if table.role != "detail":
            continue
        missing = _missing_columns(grid, top_left, table)
        if missing:
            problems.append(
                f"표 '{table.name}' 에서 값이 있는데 columns 에 없는 열: "
                + ", ".join(missing)
            )

    covered: set[int] = set()
    for table in layout.tables:
        start = min([*table.header_rows, table.data_start_row])
        covered.update(range(start, table_end_row(grid, top_left, table) + 1))
    for item in layout.key_values:
        covered.add(parse_a1(item.value_cell)[0])
        if item.label_cell:
            covered.add(parse_a1(item.label_cell)[0])

    base_row, _ = parse_a1(top_left)
    uncovered = [
        row_no
        for row_no in range(base_row, last_row_no(grid, top_left) + 1)
        if row_no not in covered
        and len(_filled_cells(grid, top_left, row_no)) >= _MIN_FILLED_FOR_TABLE_ROW
    ]
    if uncovered:
        problems.append(
            "어느 표에도 key_values 에도 들어가지 않은 행: "
            + _as_ranges(uncovered)
            + " (표를 통째로 빠뜨렸는지 확인하라)"
        )
    return problems
