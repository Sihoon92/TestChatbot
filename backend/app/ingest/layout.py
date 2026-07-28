"""캐시된 레이아웃을 재사용해도 되는지 앵커로 판정한다.

에이전트가 제출한 anchors 는 "여기에 이 텍스트가 있으면 같은 양식이다" 라는
서술이다. 행이 추가되는 것은 앵커를 건드리지 않으므로 통과하고, 누가 컬럼을
바꾸면 즉시 불일치가 나서 에이전트가 재해석한다.

Excel 에도 LLM 에도 의존하지 않는다 — 격자 리터럴만으로 전부 테스트된다.
"""
from app.excel.grid import parse_a1
from app.ingest.normalize import cell_to_text, normalize_text
from app.ingest.schemas import SheetLayout


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

    앵커 목록이 비어 있으면 False 다 — 판정 근거가 없는 레이아웃을 참으로
    처리하면 아무거나 재사용된다.
    """
    if not layout.anchors:
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
