"""앵커 대조 — 캐시된 레이아웃을 재사용해도 되는지 판정한다.

이게 너무 느슨하면 바뀐 양식을 옛 레이아웃으로 파싱해 조용히 틀린 값이
들어오고, 너무 빡빡하면 행이 추가될 때마다 에이전트가 다시 돈다."""
from app.ingest.layout import anchors_match, cell_at, pick_layout
from app.ingest.schemas import AnchorCheck, SheetLayout

# used_range 가 A1 에서 시작하지 않는 경우를 포함해 검증한다.
GRID = [
    ["No", "입고시간", "금형번호"],
    [1, "2026.07.01", "RX28312"],
]


def _layout(anchors, sheet="Sheet1"):
    return SheetLayout(sheet_name=sheet, anchors=anchors)


def test_cell_at_reads_absolute_address():
    assert cell_at(GRID, "A1", "C1") == "금형번호"
    assert cell_at(GRID, "A1", "A2") == 1


def test_cell_at_respects_grid_offset():
    """used_range 는 A1 이 아닌 곳에서 시작할 수 있다. 오프셋을 무시하면
    앵커가 전부 어긋나 캐시가 영원히 미스가 된다."""
    assert cell_at(GRID, "B3", "D3") == "금형번호"
    assert cell_at(GRID, "B3", "B4") == 1


def test_cell_at_out_of_range_is_none():
    """앵커가 격자 밖을 가리켜도 예외를 던지지 않는다 — 그냥 불일치다."""
    assert cell_at(GRID, "A1", "Z99") is None


def test_anchors_match_when_all_texts_equal():
    assert anchors_match(GRID, "A1", _layout([
        AnchorCheck(cell="A1", text="No"),
        AnchorCheck(cell="C1", text="금형번호"),
    ]))


def test_anchors_match_ignores_whitespace_and_case():
    assert anchors_match(GRID, "A1", _layout([
        AnchorCheck(cell="C1", text="  금형 번호 ")
    ])) is False, "공백 위치가 다르면 다른 헤더다"
    assert anchors_match(GRID, "A1", _layout([
        AnchorCheck(cell="B1", text=" 입고시간  ")
    ])), "앞뒤 공백만 다른 것은 같은 헤더다"


def test_anchors_mismatch_when_one_differs():
    """표 세 개 중 하나만 바뀌어도 감지돼야 한다."""
    assert not anchors_match(GRID, "A1", _layout([
        AnchorCheck(cell="A1", text="No"),
        AnchorCheck(cell="C1", text="관리번호"),
    ]))


def test_anchors_mismatch_when_out_of_range():
    assert not anchors_match(GRID, "A1", _layout([
        AnchorCheck(cell="Z99", text="금형번호")
    ]))


def test_empty_anchor_list_never_matches():
    """앵커가 없으면 '무엇이 같으면 같은 양식인가'를 판정할 근거가 없다.
    빈 목록을 참으로 처리하면 아무 레이아웃이나 재사용된다."""
    assert not anchors_match(GRID, "A1", _layout([]))


def test_pick_layout_returns_first_match():
    old = _layout([AnchorCheck(cell="C1", text="관리번호")])
    new = _layout([AnchorCheck(cell="C1", text="금형번호")])
    assert pick_layout(GRID, "A1", [old, new]) is new


def test_pick_layout_returns_none_when_nothing_matches():
    old = _layout([AnchorCheck(cell="C1", text="관리번호")])
    assert pick_layout(GRID, "A1", [old]) is None
