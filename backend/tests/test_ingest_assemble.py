"""관리대장 + 기준정보 + MES + IQC → 금형 레코드.

마스터는 **JIG 관리대장**이다. 행의 JIG ID 가 금형을 확정하고, 그 금형에
이벤트가 있어야 실재한다. 기준정보는 매핑표일 뿐이라 거기에만 있는 JIG 는
아직 들어온 적 없는 금형이다. 이 방향이 뒤집히면 대시보드 목록의 정의가
흔들린다.

조인이 조용히 어긋나면 엉뚱한 금형에 엉뚱한 불량율이 붙는데 화면만 봐서는
알 수 없다. 그래서 손실 경로를 하나씩 못 박는다.
"""
from app.ingest.assemble import assemble
from app.ingest.schemas import Row

EQUIP_A = "POU WND10_Stack(1차)_01"
EQUIP_B = "POU WND10_Stack(1차)_02"


def _row(values, *, sheet="Sheet1", file="f.xlsx", no=1):
    return Row(source_file=file, sheet=sheet, row_no=no, values=values)


def _master(mold_no, equipment, code, line="톈진 Pouch #10(S)", no=1):
    return _row({
        "mold_no": mold_no, "equipment": equipment,
        "equipment_code": code, "line": line, "jig_name": "테스트 금형",
    }, file="master.xlsx", no=no)


def _event(when, location, equipment=EQUIP_A, mold_no="#RX39513",
           sheet="관리대장", no=1):
    return _row(
        {"mold_no": mold_no, "event_at": when,
         "location": location, "equipment": equipment},
        sheet=sheet, file="ledger.xlsx", no=no,
    )


def _mes(day, code, produced, defects, no=1):
    return _row({
        "run_date": day, "equipment_code": code,
        "produced": str(produced), "defects": str(defects),
        "line": "톈진 Pouch #10(S)",
    }, file=f"{day}.xlsx", sheet="불량현황", no=no)


def _iqc(mold_no, values=None, no=1):
    base = {"mold_no": mold_no}
    base.update(values or {"punch": "12.48"})
    return _row(base, file="iqc.xlsx", sheet="Sheet1", no=no)


MASTER = [_master("#RX39513", EQUIP_A, "21004780"),
          _master("RX28312", EQUIP_B, "21004781",
                  line="톈진 Pouch #11(S)", no=2)]


# ── 마스터: 관리대장이 금형의 존재를 선언한다 ────────────────────────
def test_mold_list_comes_from_the_ledger():
    result = assemble(MASTER, [_event("2026-07-01T07:00:00", "설비")], [], [])

    assert [r.mold_no for r in result.records] == ["RX39513"]


def test_master_only_jig_never_appears():
    """기준정보에는 있지만 관리대장에 이력이 없으면 아직 들어온 적 없는 금형이다."""
    result = assemble(MASTER, [_event("2026-07-01T07:00:00", "설비")], [], [])

    assert "RX28312" not in [r.mold_no for r in result.records]


def test_jig_id_missing_from_master_is_reported_not_silently_dropped():
    """기준정보가 낡으면 그 금형이 MES 조회 키를 못 얻어 통째로 사라진다 —
    가장 흔한 사고다."""
    result = assemble(
        MASTER, [_event("2026-07-01T07:00:00", "설비", mold_no="#RX77777")], [], []
    )

    assert result.records == []
    assert result.losses.unknown_jig_id == ["RX77777"]


def test_unknown_equipment_keeps_the_mold_and_only_warns():
    """설비명을 못 찾아도 금형은 사라지지 않는다 — JIG ID 행으로 폴백한다.
    이게 예전 설계와 갈리는 지점이다(예전에는 금형이 통째로 빠졌다)."""
    result = assemble(
        MASTER,
        [_event("2026-07-01T07:00:00", "설비", equipment="신규설비_01"),
         _event("2026-07-02T07:00:00", "Jig Room", no=2)],
        [_mes("2026.07.01-2026.07.01", "21004780", 10000, 300)],
        [],
    )

    assert [r.mold_no for r in result.records] == ["RX39513"]
    assert result.records[0].runs[0].defect_rate == 300 / 10000, "폴백으로 조회된다"
    assert result.losses.unknown_equipment == ["신규설비_01"]
    assert result.losses.unknown_jig_id == []


def test_master_rows_without_a_jig_id_are_reported():
    rows = MASTER + [_master(None, "설비명만_있음", "999", no=3)]

    result = assemble(rows, [_event("2026-07-01T07:00:00", "설비")], [], [])

    assert len(result.dropped_master_rows) == 1


# ── 상태는 마지막 이벤트의 위치에서 온다 ─────────────────────────────
def test_status_comes_from_the_last_location():
    """관리대장에 상태 열이 없다. 금형이 어디에 있는가가 곧 상태다."""
    result = assemble(MASTER, [
        _event("2026-07-01T07:00:00", "설비", no=1),
        _event("2026-07-05T07:00:00", "내부 수리", no=2),
    ], [], [])

    assert result.records[0].status == "repair"


def test_still_at_the_equipment_means_in_use():
    result = assemble(MASTER, [_event("2026-07-14T09:00:00", "설비")], [], [])

    assert result.records[0].status == "in_use"


def test_unknown_storage_location_falls_back_to_standby():
    """보관함 이름은 공장마다 다르다. 어휘가 하나 늘 때마다 금형이 화면에서
    사라지면 안 되므로 설비·수리·폐기만 알아보고 나머지는 대기로 본다."""
    result = assemble(MASTER, [
        _event("2026-07-01T07:00:00", "설비", no=1),
        _event("2026-07-05T07:00:00", "3층 임시 선반", no=2),
    ], [], [])

    assert result.records[0].status == "standby"


def test_line_and_machine_cleared_when_not_in_use():
    """가동 중이 아닌데 라인을 남기면 화면이 '지금 저기 걸려 있다'고 거짓말한다."""
    result = assemble(MASTER, [
        _event("2026-07-01T07:00:00", "설비", no=1),
        _event("2026-07-05T07:00:00", "반납 대기 보관함", no=2),
    ], [], [])

    record = result.records[0]
    assert record.status == "standby"
    assert record.line is None and record.machine is None


def test_line_comes_from_the_master_not_the_ledger():
    """관리대장 라인은 공장 접두사가 없어 MES 와 대조할 수 없다."""
    result = assemble(MASTER, [_event("2026-07-01T07:00:00", "설비")], [], [])

    assert result.records[0].line == "톈진 Pouch #10(S)"


def test_line_and_machine_follow_the_mold_to_its_current_machine():
    """금형이 옮겨 다니면 '지금 어디에 걸려 있는가'는 마지막 구간이 답한다.
    기준정보에 등록된 현재 설비를 그대로 쓰면 옮겨간 사실이 화면에서 지워진다."""
    result = assemble(MASTER, [
        _event("2026-07-01T07:00:00", "설비", no=1),
        _event("2026-07-02T07:00:00", "Jig Room", no=2),
        _event("2026-07-10T07:00:00", "설비", equipment=EQUIP_B, no=3),
    ], [], [])

    record = result.records[0]
    assert record.status == "in_use"
    assert record.machine == EQUIP_B, "기준정보의 EQUIP_A 가 아니다"
    assert record.line == "톈진 Pouch #11(S)", "라인도 옮겨간 설비 기준"


# ── 사용구간과 불량율 ────────────────────────────────────────────────
def test_runs_are_attached_with_defect_rate_from_mes():
    result = assemble(
        MASTER,
        [_event("2026-07-01T07:00:00", "설비", no=1),
         _event("2026-07-02T07:00:00", "Jig Room", no=2)],
        [_mes("2026.07.01-2026.07.01", "21004780", 10000, 300)],
        [],
    )

    record = result.records[0]
    assert len(record.runs) == 1
    assert record.runs[0].defect_rate == 300 / 10000
    assert record.latest_defect_rate == 300 / 10000


def test_total_installs_counts_equipment_runs():
    """설치 횟수를 따로 적은 열이 없다 — 설비에 들어간 횟수가 곧 그 값이다."""
    result = assemble(MASTER, [
        _event("2026-07-01T07:00:00", "설비", no=1),
        _event("2026-07-02T07:00:00", "Jig Room", no=2),
        _event("2026-07-05T07:00:00", "설비", no=3),
        _event("2026-07-06T07:00:00", "Jig Room", no=4),
    ], [], [])

    assert result.records[0].total_installs == 2


def test_total_production_sums_mes_input_quantities():
    result = assemble(
        MASTER,
        [_event("2026-07-01T07:00:00", "설비", no=1),
         _event("2026-07-03T07:00:00", "Jig Room", no=2)],
        [_mes("2026.07.01-2026.07.01", "21004780", 10000, 300),
         _mes("2026.07.02-2026.07.02", "21004780", 12000, 100, no=2)],
        [],
    )

    assert result.records[0].total_production == 22000


def test_shot_count_stays_unknown():
    """어느 문서에도 사용타수가 없다. 0 으로 두면 '신품'이라는 거짓말이 된다."""
    result = assemble(MASTER, [_event("2026-07-01T07:00:00", "설비")], [], [])

    assert result.records[0].shot_count is None


def test_latest_defect_rate_falls_back_past_an_open_run():
    """마지막 구간이 아직 가동 중이면 불량율이 없다. 그렇다고 화면을 비우면
    직전 가동의 실적까지 사라진다."""
    result = assemble(
        MASTER,
        [_event("2026-07-01T07:00:00", "설비", no=1),
         _event("2026-07-02T07:00:00", "Jig Room", no=2),
         _event("2026-07-10T07:00:00", "설비", no=3)],
        [_mes("2026.07.01-2026.07.01", "21004780", 10000, 300)],
        [],
    )

    assert result.records[0].latest_defect_rate == 300 / 10000
    assert result.losses.open_runs == 1


def test_missing_mes_day_is_surfaced():
    result = assemble(
        MASTER,
        [_event("2026-07-01T07:00:00", "설비", no=1),
         _event("2026-07-03T07:00:00", "Jig Room", no=2)],
        [_mes("2026.07.01-2026.07.01", "21004780", 10000, 300)],
        [],
    )

    assert result.losses.missing_mes_days == ["2026-07-02"]


# ── IQC 부착 ─────────────────────────────────────────────────────────
def test_iqc_items_attach_to_matching_mold():
    result = assemble(
        MASTER, [_event("2026-07-01T07:00:00", "설비")], [], [_iqc("RX39513")]
    )

    record = result.records[0]
    assert [i.label for i in record.iqc_items] == ["punch"]
    assert result.iqc_matched == 1


def test_iqc_hash_prefix_matches_the_master_number():
    """대장의 '#RX39513' 과 기준정보의 'RX39513' 은 같은 금형이다."""
    result = assemble(
        MASTER, [_event("2026-07-01T07:00:00", "설비")], [], [_iqc("#RX39513")]
    )

    assert result.iqc_matched == 1


def test_iqc_extra_columns_become_items_too():
    """미리 고정 필드로 잡을 수 없는 항목을 버리지 않는 것이 유연 스키마의 목적이다."""
    result = assemble(
        MASTER, [_event("2026-07-01T07:00:00", "설비")], [],
        [_iqc("RX39513", {"punch": "12.48", "측정자": "홍길동"})],
    )

    labels = [i.label for i in result.records[0].iqc_items]
    assert "측정자" in labels
    assert "mold_no" not in labels, "귀속에만 쓰이고 화면에는 필요 없다"


def test_orphan_iqc_mold_is_recorded_not_silently_dropped():
    """관리대장이 마스터이므로 넣지 않는다. 다만 조용히 버리지도 않는다."""
    result = assemble(
        MASTER, [_event("2026-07-01T07:00:00", "설비")], [], [_iqc("RX99999")]
    )

    assert result.orphan_mold_nos == ["RX99999"]


def test_orphan_mold_no_is_listed_once_across_many_rows():
    result = assemble(
        MASTER, [_event("2026-07-01T07:00:00", "설비")], [],
        [_iqc("RX99999"), _iqc("RX99999", no=2)],
    )

    assert result.orphan_mold_nos == ["RX99999"]


def test_iqc_row_without_mold_no_counts_as_skipped():
    result = assemble(
        MASTER, [_event("2026-07-01T07:00:00", "설비")], [], [_iqc("소계")]
    )

    assert result.skipped_rows == 1
    assert result.orphan_mold_nos == []
