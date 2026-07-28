"""앵커 대조 — 캐시된 레이아웃을 재사용해도 되는지 판정한다.

이게 너무 느슨하면 바뀐 양식을 옛 레이아웃으로 파싱해 조용히 틀린 값이
들어오고, 너무 빡빡하면 행이 추가될 때마다 에이전트가 다시 돈다."""
from app.ingest.layout import (
    anchors_match,
    cell_at,
    find_layout_gaps,
    pick_layout,
    table_end_row,
)
from app.ingest.schemas import (
    AnchorCheck,
    ColumnMap,
    KeyValueItem,
    SheetLayout,
    TableBlock,
)

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
    # 위 두 단언은 모두 한글이라 casefold 가 빠져도 통과한다 — 대소문자
    # 무시는 영문 앵커로만 검증된다. 실물 시트에 punch/die 같은 영문
    # 헤더가 있고, 대소문자가 흔들리면 캐시가 영원히 미스가 난다.
    en_grid = [["Punch", "Die"]]
    assert anchors_match(en_grid, "A1", _layout([
        AnchorCheck(cell="A1", text="PUNCH")
    ])), "대소문자만 다른 것은 같은 헤더다"


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


# ── 제출 검증 (find_layout_gaps) ────────────────────────────────────────
# 프롬프트로 "모든 컬럼을 매핑하라" 고 요구해도 모델은 표의 오른쪽 끝을 찍는다.
# 실물에서 20열짜리 표를 9열만 매핑하고 끊었고, 빠졌다는 사실이 어디에도
# 남지 않았다. 격자는 끝이 어디인지 알고 있으므로 아는 쪽이 확인한다.

# 실물 IQC 대장 상세표를 축약한 격자 (B~H 에 값, 2단 헤더).
IQC_GRID = [
    [None, "No", "금형 번호", "성형부", "측정자", "PUNCH", "DIE", "간극"],
    [None, None, None, "정극 성형", None, None, None, None],
    [None, 1, "RX28312", "체크", "홍길동", 12.48, 12.11, 0.05],
    [None, 2, "RX28315", "체크", "김영수", 9.02, 8.71, 0.04],
]


def _detail_table(columns, **kwargs):
    return TableBlock(
        name="대장 상세",
        role="detail",
        header_rows=kwargs.pop("header_rows", [1, 2]),
        data_start_row=kwargs.pop("data_start_row", 3),
        columns=[ColumnMap(field=f, column=c) for f, c in columns],
        **kwargs,
    )


def test_no_gaps_when_every_filled_column_is_mapped():
    table = _detail_table([
        ("No", "B"), ("mold_no", "C"), ("성형부/정극 성형", "D"),
        ("측정자", "E"), ("punch", "F"), ("die", "G"), ("gap", "H"),
    ])
    layout = SheetLayout(sheet_name="Sheet1", anchors=[], tables=[table])

    assert find_layout_gaps(IQC_GRID, "A1", layout) == []


def test_reports_columns_that_have_values_but_no_mapping():
    """실물 실패 그대로 — 왼쪽 몇 열만 매핑하고 오른쪽을 끊은 경우."""
    table = _detail_table([("No", "B"), ("mold_no", "C")])
    layout = SheetLayout(sheet_name="Sheet1", anchors=[], tables=[table])

    problems = find_layout_gaps(IQC_GRID, "A1", layout)

    assert len(problems) == 1
    # 어느 열인지와 그 열이 무엇인지가 함께 나와야 에이전트가 고칠 수 있다.
    assert "D(성형부)" in problems[0]
    assert "F(PUNCH)" in problems[0] and "H(간극)" in problems[0]
    assert "대장 상세" in problems[0]


def test_missing_column_hint_prefers_the_header_text():
    """설명 문구가 데이터 값('체크')이면 에이전트가 무슨 열인지 모른다."""
    table = _detail_table([("No", "B"), ("mold_no", "C")])
    layout = SheetLayout(sheet_name="Sheet1", anchors=[], tables=[table])

    problems = find_layout_gaps(IQC_GRID, "A1", layout)

    assert "D(성형부)" in problems[0], "헤더 행을 먼저 봐야 한다"
    assert "D(체크)" not in problems[0]


def test_summary_tables_are_not_checked_for_missing_columns():
    """summary 는 파싱하지 않는다. 요구를 넓히면 왕복만 늘어난다."""
    table = _detail_table([("구분", "B")])
    table.role = "summary"
    layout = SheetLayout(sheet_name="Sheet1", anchors=[], tables=[table])

    assert find_layout_gaps(IQC_GRID, "A1", layout) == []


def test_reports_rows_that_no_table_covers():
    """표를 통째로 빠뜨린 경우 — 실물에서 이력표(17행)가 사라져 그 표의
    금형 5건이 전부 화면에 안 나왔다."""
    grid = [
        [None, "No", "모델", "관리 번호"],     # 1행: 표 A 헤더
        [None, 1, "양극 성형", "#RX41194"],    # 2행
        [None, None, None, None],              # 3행: 빈 행
        [None, "No", "금형 번호", "PUNCH"],    # 4행: 표 B 헤더
        [None, 1, "RX28312", 12.48],           # 5행
    ]
    # 표 B 만 선언했다 — 1~2행이 아무데도 안 들어간다.
    table = _detail_table(
        [("No", "B"), ("mold_no", "C"), ("punch", "D")],
        header_rows=[4], data_start_row=5,
    )
    layout = SheetLayout(sheet_name="Sheet1", anchors=[], tables=[table])

    problems = find_layout_gaps(grid, "A1", layout)

    assert any("1-2" in p for p in problems), problems


def test_thin_rows_are_not_demanded_to_be_covered():
    """제목 줄(1칸)과 소계 꼬리(1칸)까지 표에 넣으라고 하면 에이전트가
    만족시킬 수 없는 조건이 되어 결국 아무 레이아웃도 못 얻는다."""
    grid = [
        [None, "Stack 금형 측정 대장", None],  # 제목 1칸
        [None, "No", "금형 번호"],
        [None, 1, "RX28312"],
        [None, None, "소계"],                  # 꼬리 1칸
    ]
    table = _detail_table(
        [("No", "B"), ("mold_no", "C")], header_rows=[2], data_start_row=3,
    )
    layout = SheetLayout(sheet_name="Sheet1", anchors=[], tables=[table])

    assert find_layout_gaps(grid, "A1", layout) == []


def test_key_values_cover_their_own_rows():
    grid = [
        [None, "작성자", "홍길동", "부서", "품질"],  # key_values 로 잡은 4칸 행
        [None, "No", "금형 번호", None, None],
        [None, 1, "RX28312", None, None],
    ]
    table = _detail_table(
        [("No", "B"), ("mold_no", "C")], header_rows=[2], data_start_row=3,
    )
    layout = SheetLayout(
        sheet_name="Sheet1", anchors=[], tables=[table],
        key_values=[KeyValueItem(field="작성자", value_cell="C1", label_cell="B1")],
    )

    assert find_layout_gaps(grid, "A1", layout) == []


def test_gap_check_respects_grid_offset():
    """used_range 가 A1 에서 시작하지 않으면 열 문자가 전부 어긋난다."""
    table = _detail_table(
        [("No", "C"), ("mold_no", "D")], header_rows=[3, 4], data_start_row=5,
    )
    layout = SheetLayout(sheet_name="Sheet1", anchors=[], tables=[table])

    problems = find_layout_gaps(IQC_GRID, "B3", layout)

    assert "E(성형부)" in problems[0], problems


def test_table_end_row_matches_what_the_parser_consumes():
    """검증과 파서가 다른 끝을 보면, 검증은 '덮였다'는데 파서는 안 읽는
    행이 생긴다 — 통과하고도 데이터가 안 들어온다."""
    grid = [
        [None, "No", "금형 번호"],
        [None, 1, "RX28312"],
        [None, 2, "RX28315"],
        [None, None, None],          # 여기서 파서가 멈춘다
        [None, "비고", "없음"],
    ]
    table = _detail_table(
        [("No", "B"), ("mold_no", "C")], header_rows=[1], data_start_row=2,
    )

    assert table_end_row(grid, "A1", table) == 3


def test_no_gaps_for_empty_grid():
    layout = SheetLayout(sheet_name="Sheet1", anchors=[])
    assert find_layout_gaps([], "A1", layout) == []
