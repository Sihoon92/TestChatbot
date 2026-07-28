"""MES/IQC 행 → 금형 레코드.

MES 가 마스터다 — 금형의 존재를 선언하는 것은 MES 뿐이다. IQC 는 이미 있는
금형에 정보를 덧붙일 뿐이다. 이 방향이 뒤집히면 대시보드 목록의 정의가
흔들린다."""
from app.ingest.assemble import assemble
from app.ingest.schemas import Row


def _mes(mold_no, status="사용중", **extra):
    values = {"mold_no": mold_no, "status": status, **extra}
    return Row(source_file="mes.xlsx", sheet="Sheet1", row_no=2, values=values)


def _iqc(mold_no, **extra):
    values = {"mold_no": mold_no, **extra}
    return Row(source_file="iqc.xlsx", sheet="Sheet1", row_no=15, values=values)


def test_mold_list_comes_from_mes():
    result = assemble([_mes("RX28312"), _mes("RX28315")], [])
    assert [r.mold_no for r in result.records] == ["RX28312", "RX28315"]
    assert result.records[0].status == "in_use"


def test_duplicate_mes_rows_collapse_to_one_mold():
    """MES 한 행 = 생산 이벤트 1건이므로 같은 금형이 여러 번 나온다.
    금형 목록에는 하나만 있어야 한다."""
    result = assemble([_mes("RX28312"), _mes("RX28312")], [])
    assert len(result.records) == 1


def test_later_mes_row_wins_for_current_state():
    """뒤에 나온 생산 이벤트가 더 최신 상태다."""
    rows = [
        _mes("RX28312", status="대기중", machine="2"),
        _mes("RX28312", status="사용중", machine="5"),
    ]
    result = assemble(rows, [])
    assert result.records[0].status == "in_use"
    assert result.records[0].machine == "5"


def test_iqc_items_attach_to_matching_mold():
    result = assemble(
        [_mes("RX28312")],
        [_iqc("RX28312", punch="12.5", die="12.1", diff="0.4", gap="0.05")],
    )
    labels = [i.label for i in result.records[0].iqc_items]
    assert labels == ["punch", "die", "diff", "gap"]
    assert result.records[0].iqc_items[0].value == "12.5"
    assert result.records[0].iqc_items[0].source_file == "iqc.xlsx"
    assert result.iqc_matched == 1


def test_iqc_hash_prefix_matches_mes_number():
    """이력표는 '#RX41194', MES 는 'RX41194'. # 만 떼면 같은 금형이다."""
    result = assemble([_mes("RX41194")], [_iqc("#RX41194", punch="9.0")])
    assert len(result.records[0].iqc_items) == 1
    assert not result.orphan_mold_nos


def test_iqc_extra_columns_become_items_too():
    """IQC 의 자유 컬럼(측정자·조립자 등)도 대시보드에 보여야 한다 —
    미리 고정 필드로 잡을 수 없는 항목들이다."""
    result = assemble([_mes("RX28312")], [_iqc("RX28312", 측정자="홍길동")])
    labels = [i.label for i in result.records[0].iqc_items]
    assert "측정자" in labels


def test_orphan_iqc_mold_is_recorded_not_silently_dropped():
    """MES 에 없는 금형은 대시보드에 넣지 않는다(MES 가 마스터). 다만
    조용히 버리면 진짜 MES 누락인지 오타인지 아무도 모른다."""
    result = assemble([_mes("RX28312")], [_iqc("RX99999", punch="1.0")])
    assert [r.mold_no for r in result.records] == ["RX28312"]
    assert result.orphan_mold_nos == ["RX99999"]


def test_unknown_status_excludes_mold_and_records_raw_text():
    """인식 못 한 상태는 추측하지 않는다. 그 금형은 제외하고 원문을 모아
    사람이 STATUS_MAP 을 고치게 한다."""
    result = assemble([_mes("RX28312", status="가동")], [])
    assert result.records == []
    assert result.unknown_statuses == ["가동"]


def test_rows_without_mold_no_are_counted_as_skipped():
    """소계 행·빈 행은 정상이다. 하지만 몇 건이 빠졌는지는 보여야 한다."""
    result = assemble([_mes("소계"), _mes(None), _mes("RX28312")], [])
    assert [r.mold_no for r in result.records] == ["RX28312"]
    assert result.skipped_rows == 2


def test_quantities_are_none_when_mes_lacks_them():
    """MES 에 타수·생산량 컬럼이 없으면 미상이다. 0 으로 채우면 '신품'이라는
    거짓말이 된다."""
    result = assemble([_mes("RX28312")], [])
    m = result.records[0]
    assert m.shot_count is None
    assert m.total_production is None
    assert m.total_installs is None
    assert m.latest_defect_rate is None


def test_quantities_are_parsed_when_present():
    result = assemble(
        [_mes("RX28312", shot_count="8,412", total_production="1204500",
              defect_rate="0.8%")],
        [],
    )
    m = result.records[0]
    assert m.shot_count == 8412
    assert m.total_production == 1204500
    assert m.latest_defect_rate == 0.008


def test_line_and_machine_cleared_when_not_in_use():
    """대기중·수리중 금형은 호기가 없다. 남아 있으면 화면이 '3-2에 걸려 있다'는
    거짓 정보를 보여준다."""
    result = assemble([_mes("RX28312", status="대기중", line="3", machine="2")], [])
    assert result.records[0].line is None
    assert result.records[0].machine is None
