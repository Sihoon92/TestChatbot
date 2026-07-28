"""격자 + 레이아웃 → 행. LLM 도 Excel 도 없이 전부 검증한다.

여기가 틀리면 대시보드의 모든 숫자가 틀린다. 실물에서 겪을 상황(멀티헤더,
summary 표, 소계 행, 빈 행, 오프셋)을 리터럴로 미리 겪어둔다."""
from app.ingest.parser import parse_rows
from app.ingest.schemas import (
    AnchorCheck,
    ColumnMap,
    KeyValueItem,
    SheetLayout,
    TableBlock,
)

ANCHOR = [AnchorCheck(cell="A1", text="No")]


def test_parses_simple_table():
    grid = [
        ["No", "금형번호", "punch"],
        [1, "RX28312", 12.5],
        [2, "RX28315", 9.0],
    ]
    layout = SheetLayout(
        sheet_name="Sheet1",
        anchors=ANCHOR,
        tables=[TableBlock(
            name="상세", role="detail", header_rows=[1], data_start_row=2,
            columns=[
                ColumnMap(field="mold_no", column="B"),
                ColumnMap(field="punch", column="C"),
            ],
        )],
    )
    rows = parse_rows(grid, "A1", layout, "iqc.xlsx")
    assert [r.values["mold_no"] for r in rows] == ["RX28312", "RX28315"]
    assert rows[0].values["punch"] == "12.5"
    assert rows[0].row_no == 2
    assert rows[0].source_file == "iqc.xlsx"
    assert rows[0].sheet == "Sheet1"


def test_skips_summary_tables():
    """summary 표를 파싱하면 '소계'가 금형번호로 읽힌다 — 실물 IQC 시트에
    summary 표가 두 개나 있어서 이 구분이 없으면 즉시 사고가 난다."""
    grid = [
        ["구분", "값"],
        ["양극 성형", 3],
        ["소계", 3],
    ]
    layout = SheetLayout(
        sheet_name="Sheet1",
        anchors=[AnchorCheck(cell="A1", text="구분")],
        tables=[TableBlock(
            name="집계", role="summary", header_rows=[1], data_start_row=2,
            columns=[ColumnMap(field="mold_no", column="A")],
        )],
    )
    assert parse_rows(grid, "A1", layout, "iqc.xlsx") == []


def test_parses_multiple_detail_tables_in_one_sheet():
    """실물 IQC sheet1 은 카테고리 2개 × 표 2개 = 표 4개다."""
    grid = [
        ["No", "관리번호"],
        [1, "#RX41194"],
        [],
        ["No", "금형번호"],
        [1, "RX28312"],
    ]
    layout = SheetLayout(
        sheet_name="Sheet1",
        anchors=ANCHOR,
        tables=[
            TableBlock(
                name="이력", role="detail", category="측정 이력",
                header_rows=[1], data_start_row=2, data_end_row=2,
                columns=[ColumnMap(field="mold_no", column="B")],
            ),
            TableBlock(
                name="대장", role="detail", category="금형 측정 대장",
                header_rows=[4], data_start_row=5,
                columns=[ColumnMap(field="mold_no", column="B")],
            ),
        ],
    )
    rows = parse_rows(grid, "A1", layout, "iqc.xlsx")
    assert [r.values["mold_no"] for r in rows] == ["#RX41194", "RX28312"]


def test_stops_at_blank_row_when_no_end_specified():
    """data_end_row 가 None 이면 빈 행에서 멈춘다. 안 멈추면 표 아래의
    다른 블록(비고·서명란)까지 금형인 척 딸려 들어온다."""
    grid = [
        ["No", "금형번호"],
        [1, "RX28312"],
        [None, None],
        ["비고", "아래는 서명란"],
    ]
    layout = SheetLayout(
        sheet_name="Sheet1", anchors=ANCHOR,
        tables=[TableBlock(
            name="상세", role="detail", header_rows=[1], data_start_row=2,
            columns=[ColumnMap(field="mold_no", column="B")],
        )],
    )
    rows = parse_rows(grid, "A1", layout, "iqc.xlsx")
    assert len(rows) == 1


def test_respects_explicit_end_row():
    grid = [
        ["No", "금형번호"],
        [1, "RX28312"],
        [2, "RX28315"],
        [3, "RX41194"],
    ]
    layout = SheetLayout(
        sheet_name="Sheet1", anchors=ANCHOR,
        tables=[TableBlock(
            name="상세", role="detail", header_rows=[1],
            data_start_row=2, data_end_row=3,
            columns=[ColumnMap(field="mold_no", column="B")],
        )],
    )
    rows = parse_rows(grid, "A1", layout, "iqc.xlsx")
    assert len(rows) == 2


def test_multi_header_columns_use_combined_labels():
    """2단 병합 헤더는 에이전트가 '성형부/정극 성형' 으로 결합해 제출한다.
    파서는 그 라벨을 그대로 키로 쓴다."""
    grid = [
        ["No", "금형번호", "성형부", None],
        [None, None, "정극 성형", "부극 성형"],
        [1, "RX28312", "체크", None],
    ]
    layout = SheetLayout(
        sheet_name="Sheet1", anchors=ANCHOR,
        tables=[TableBlock(
            name="상세", role="detail", header_rows=[1, 2], data_start_row=3,
            columns=[
                ColumnMap(field="mold_no", column="B"),
                ColumnMap(field="성형부/정극 성형", column="C"),
                ColumnMap(field="성형부/부극 성형", column="D"),
            ],
        )],
    )
    rows = parse_rows(grid, "A1", layout, "iqc.xlsx")
    assert rows[0].values["성형부/정극 성형"] == "체크"
    assert rows[0].values["성형부/부극 성형"] is None


def test_key_values_merge_into_every_row():
    """성적서형 시트는 상단 블록에 공통 정보(기종·업체)가 있다.
    표 컬럼이 같은 필드를 주면 표가 이긴다 — 행별 값이 더 구체적이다."""
    grid = [
        ["기종", "H104"],
        ["No", "금형번호", "기종"],
        [1, "RX28312", "H999"],
    ]
    layout = SheetLayout(
        sheet_name="Sheet1", anchors=[AnchorCheck(cell="A1", text="기종")],
        key_values=[KeyValueItem(field="기종", value_cell="B1", label_cell="A1")],
        tables=[TableBlock(
            name="상세", role="detail", header_rows=[2], data_start_row=3,
            columns=[
                ColumnMap(field="mold_no", column="B"),
                ColumnMap(field="기종", column="C"),
            ],
        )],
    )
    rows = parse_rows(grid, "A1", layout, "iqc.xlsx")
    assert rows[0].values["기종"] == "H999"


def test_key_values_used_when_table_lacks_field():
    grid = [
        ["업체", "일신"],
        ["No", "금형번호"],
        [1, "RX28312"],
    ]
    layout = SheetLayout(
        sheet_name="Sheet1", anchors=[AnchorCheck(cell="A1", text="업체")],
        key_values=[KeyValueItem(field="업체", value_cell="B1")],
        tables=[TableBlock(
            name="상세", role="detail", header_rows=[2], data_start_row=3,
            columns=[ColumnMap(field="mold_no", column="B")],
        )],
    )
    rows = parse_rows(grid, "A1", layout, "iqc.xlsx")
    assert rows[0].values["업체"] == "일신"


def test_handles_grid_offset():
    """used_range 가 C3 에서 시작해도 절대 행/열 번호로 동작해야 한다."""
    grid = [
        ["No", "금형번호"],
        [1, "RX28312"],
    ]
    layout = SheetLayout(
        sheet_name="Sheet1",
        anchors=[AnchorCheck(cell="C3", text="No")],
        tables=[TableBlock(
            name="상세", role="detail", header_rows=[3], data_start_row=4,
            columns=[ColumnMap(field="mold_no", column="D")],
        )],
    )
    rows = parse_rows(grid, "C3", layout, "iqc.xlsx")
    assert rows[0].values["mold_no"] == "RX28312"
    assert rows[0].row_no == 4


def test_row_beyond_grid_is_not_produced():
    """data_end_row 가 격자보다 크게 잡혀도 없는 행을 만들어내지 않는다."""
    grid = [
        ["No", "금형번호"],
        [1, "RX28312"],
    ]
    layout = SheetLayout(
        sheet_name="Sheet1", anchors=ANCHOR,
        tables=[TableBlock(
            name="상세", role="detail", header_rows=[1],
            data_start_row=2, data_end_row=99,
            columns=[ColumnMap(field="mold_no", column="B")],
        )],
    )
    assert len(parse_rows(grid, "A1", layout, "iqc.xlsx")) == 1


def test_layout_without_tables_yields_single_key_value_row():
    """표 없이 키-값 블록만 있는 성적서(시트 전체가 금형 하나)도 한 행이 된다."""
    grid = [["금형번호", "RX28312"], ["punch", 12.5]]
    layout = SheetLayout(
        sheet_name="Sheet1",
        anchors=[AnchorCheck(cell="A1", text="금형번호")],
        key_values=[
            KeyValueItem(field="mold_no", value_cell="B1", label_cell="A1"),
            KeyValueItem(field="punch", value_cell="B2", label_cell="A2"),
        ],
    )
    rows = parse_rows(grid, "A1", layout, "iqc.xlsx")
    assert len(rows) == 1
    assert rows[0].values == {"mold_no": "RX28312", "punch": "12.5"}
