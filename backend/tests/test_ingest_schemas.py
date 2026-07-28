"""수집 스키마의 계약 검증.

에이전트가 submit_layout 인자로 채우는 구조라, 필수/선택 필드가 잘못 잡히면
에이전트가 제출을 못 하거나 잘못된 레이아웃이 통과한다."""
import pytest
from pydantic import ValidationError

from app.ingest.schemas import (
    AnchorCheck,
    ColumnMap,
    MoldRecord,
    Row,
    SheetLayout,
    TableBlock,
)


def test_table_block_requires_role():
    """role 이 없으면 summary 표를 금형 레코드로 잘못 파싱한다 — '소계'가
    금형번호로 읽히는 사고의 원인이 바로 이 구분의 부재다."""
    with pytest.raises(ValidationError):
        TableBlock(
            name="표", header_rows=[1], data_start_row=2, columns=[]
        )


def test_table_block_accepts_multi_header_rows():
    t = TableBlock(
        name="대장 상세",
        role="detail",
        category="금형 측정 대장",
        header_rows=[13, 14],
        data_start_row=15,
        columns=[ColumnMap(field="성형부/정극 성형", column="D")],
    )
    assert t.header_rows == [13, 14]
    assert t.data_end_row is None  # None = 빈 행 만날 때까지


def test_sheet_layout_requires_anchors():
    """앵커가 없으면 캐시 재사용 여부를 판정할 수 없다 — 매번 에이전트를
    다시 돌리게 되고, 양식 변경도 감지하지 못한다."""
    with pytest.raises(ValidationError):
        SheetLayout(sheet_name="Sheet1")


def test_sheet_layout_minimal():
    layout = SheetLayout(
        sheet_name="Sheet1",
        anchors=[AnchorCheck(cell="B4", text="금형번호")],
    )
    assert layout.tables == []
    assert layout.key_values == []
    assert layout.notes is None


def test_row_allows_none_values():
    """빈 셀은 None 이다. 빈 문자열로 바꾸면 '값이 없음'과 '빈 문자열'이 섞인다."""
    r = Row(source_file="a.xlsx", sheet="Sheet1", row_no=15,
            values={"mold_no": "RX28312", "punch": None})
    assert r.values["punch"] is None


def test_mold_record_quantities_default_to_none():
    """수량은 미상이 기본이다. 0 을 기본값으로 두면 '신품'이라는 거짓말이 된다."""
    m = MoldRecord(mold_no="RX28312", status="in_use", source_file="mes.xlsx")
    assert m.shot_count is None
    assert m.total_installs is None
    assert m.total_production is None
    assert m.iqc_items == []
