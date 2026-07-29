"""EES 사용구간 추출과 MES 불량율 조인.

Excel 도 LLM 도 DB 도 모르는 순수 계산이다 — Row 리터럴만으로 전부 검증된다.
조인이 조용히 어긋나면 엉뚱한 날의 불량율이 금형에 붙는데, 화면만 봐서는
절대 알 수 없다. 그래서 경계 조건을 하나씩 못 박는다.

## 이 파일이 지키는 조인 방향

금형을 확정하는 것은 **관리대장의 JIG ID 열**이다. 설비명은 식별이 아니라
"그 구간에 어느 설비의 MES 실적을 붙일지" 를 정하는 데만 쓴다. 두 역할이
섞이면 사람이 고칠 수 있는 설비명 문자열이 금형 정체성을 좌우하게 된다.
"""
from datetime import date

from app.ingest.join import (
    attach_defect_rates,
    build_equipment_index,
    build_jig_index,
    covered_dates,
    extract_runs,
    index_mes,
    latest_locations,
)
from app.ingest.schemas import Row


def _row(values: dict, *, sheet="관리대장", file="ledger.xlsx", no=1) -> Row:
    return Row(source_file=file, sheet=sheet, row_no=no, values=values)


# ── 기준정보 색인 ────────────────────────────────────────────────────
MASTER = [
    _row({
        "mold_no": "#RX39513", "jig_name": "음극 Notching 금형",
        "equipment": "POU WND10_Stack(1차)_01",
        "equipment_code": "21004780", "line": "톈진 Pouch #10(S)",
    }, file="master.xlsx"),
    _row({
        "mold_no": "RX28312", "jig_name": "양극 Notching 금형",
        "equipment": "POU WND10_Stack(1차)_02",
        "equipment_code": "21004781", "line": "톈진 Pouch #11(S)",
    }, file="master.xlsx", no=2),
]


def test_jig_index_is_keyed_by_jig_id():
    """관리대장이 아는 것은 JIG ID 다. 그것으로 기준정보를 직접 조회한다."""
    index, dropped = build_jig_index(MASTER)

    assert set(index) == {"RX39513", "RX28312"}
    assert index["RX39513"].equipment == "POU WND10_Stack(1차)_01"
    assert index["RX39513"].equipment_code == "21004780"
    assert dropped == [], "# 가 떨어져 정상 조회되므로 버릴 행이 없다"


def test_jig_index_keeps_rows_that_have_no_equipment_name():
    """설비명이 비어도 JIG ID 조회는 성립한다. 필수 키는 JIG ID 하나다."""
    rows = MASTER + [
        _row({"mold_no": "RX99999", "equipment": None,
              "equipment_code": "21009999", "line": "천안 Pouch #1(L)"},
             file="master.xlsx", no=3),
    ]

    index, dropped = build_jig_index(rows)

    assert index["RX99999"].equipment_code == "21009999"
    assert dropped == []


def test_jig_index_drops_rows_without_a_jig_id_and_says_so():
    """JIG ID 가 없는 행은 어느 금형의 것인지 알 수 없다. 조용히 버리면
    나중에 '왜 이 금형이 안 나오지' 를 추적할 수 없다."""
    rows = MASTER + [
        _row({"mold_no": None, "equipment": "POU WND99_X_01"},
             file="master.xlsx", no=4),
        _row({"mold_no": "소계", "equipment": "POU WND99_X_02"},
             file="master.xlsx", no=5),
    ]

    index, dropped = build_jig_index(rows)

    assert len(index) == 2
    assert len(dropped) == 2


def test_jig_index_last_row_wins_for_duplicate_jig_id():
    """같은 JIG ID 가 두 번 나오면 뒤에 나온 것이 최신이라고 본다."""
    rows = MASTER + [
        _row({
            "mold_no": "RX39513", "equipment": "POU WND99_New_01",
            "equipment_code": "21009999", "line": "천안 Pouch #1(L)",
        }, file="master.xlsx", no=9),
    ]

    index, _ = build_jig_index(rows)

    assert index["RX39513"].equipment_code == "21009999"


def test_equipment_index_is_keyed_by_equipment_name():
    """설비명 색인은 MES 조회 키(설비코드·라인)를 얻는 용도다. 여기서 나오는
    mold_no 는 '그 설비에 등록된 금형'이지 조회 중인 금형이 아니다."""
    index = build_equipment_index(MASTER)

    assert set(index) == {
        "POU WND10_Stack(1차)_01", "POU WND10_Stack(1차)_02"
    }
    assert index["POU WND10_Stack(1차)_02"].equipment_code == "21004781"


def test_equipment_index_skips_rows_without_an_equipment_name():
    rows = MASTER + [
        _row({"mold_no": "RX99999", "equipment": None}, file="master.xlsx", no=3),
    ]

    assert len(build_equipment_index(rows)) == 2


# ── 사용구간 추출 ────────────────────────────────────────────────────
JIG_INDEX = build_jig_index(MASTER)[0]
EQUIP_INDEX = build_equipment_index(MASTER)


def _event(when: str, location: str, *, mold_no="#RX39513",
           equipment="POU WND10_Stack(1차)_01", file="ledger.xlsx",
           no=1) -> Row:
    return _row(
        {"mold_no": mold_no, "event_at": when,
         "location": location, "equipment": equipment},
        file=file, no=no,
    )


def test_run_starts_at_설비_and_ends_at_the_next_event():
    """위치가 '설비' 인 이벤트가 투입, **바로 다음 이벤트**가 종료다.
    다음 이벤트의 위치가 무엇이든 상관없다 — 설비를 떠났다는 사실만 중요하다."""
    rows = [
        _event("2026-07-01T06:00:00", "통합 Jig Room", no=1),
        _event("2026-07-01T07:00:00", "설비", no=2),
        _event("2026-07-05T07:00:00", "내부 수리", no=3),
    ]

    runs, losses = extract_runs(rows, JIG_INDEX, EQUIP_INDEX)

    assert len(runs) == 1
    assert runs[0].started_at == "2026-07-01T07:00:00"
    assert runs[0].ended_at == "2026-07-05T07:00:00"
    assert losses.open_runs == 0


def test_last_event_still_at_설비_is_an_open_run():
    """아직 설비에 있으면 종료가 없다. 이걸 '조인 실패'와 같은 None 으로
    뭉개면 사람이 원인을 구분할 수 없다."""
    rows = [_event("2026-07-14T09:00:00", "설비", no=1)]

    runs, losses = extract_runs(rows, JIG_INDEX, EQUIP_INDEX)

    assert runs[0].ended_at is None
    assert losses.open_runs == 1


def test_multiple_runs_for_one_mold():
    rows = [
        _event("2026-07-02T08:30:00", "설비", no=1),
        _event("2026-07-03T09:00:00", "사용 대기 보관함", no=2),
        _event("2026-07-11T06:00:00", "설비", no=3),
        _event("2026-07-11T20:00:00", "반납 대기 보관함", no=4),
    ]

    runs, _ = extract_runs(rows, JIG_INDEX, EQUIP_INDEX)

    assert [r.started_at for r in runs] == [
        "2026-07-02T08:30:00", "2026-07-11T06:00:00"
    ]


def test_events_are_sorted_before_pairing():
    """엑셀 행 순서가 시간순이라는 보장이 없다. 정렬하지 않으면 '다음 이벤트'가
    엉뚱한 행이 되어 사용구간이 음수 길이가 된다."""
    rows = [
        _event("2026-07-05T07:00:00", "내부 수리", no=1),
        _event("2026-07-01T07:00:00", "설비", no=2),
    ]

    runs, _ = extract_runs(rows, JIG_INDEX, EQUIP_INDEX)

    assert runs[0].started_at == "2026-07-01T07:00:00"
    assert runs[0].ended_at == "2026-07-05T07:00:00"


def test_two_molds_in_one_sheet_do_not_close_each_other():
    """한 시트에 여러 금형의 이벤트가 시간순으로 섞여 있다. JIG ID 로 묶지
    않으면 A 금형의 설비 진입이 B 금형의 다음 이벤트로 닫힌다."""
    rows = [
        _event("2026-07-01T07:00:00", "설비", mold_no="#RX39513", no=1),
        _event("2026-07-01T08:00:00", "설비", mold_no="RX28312",
               equipment="POU WND10_Stack(1차)_02", no=2),
        _event("2026-07-09T07:00:00", "통합 Jig Room", mold_no="#RX39513", no=3),
    ]

    runs, _ = extract_runs(rows, JIG_INDEX, EQUIP_INDEX)

    by_mold = {r.mold_no: r for r in runs}
    assert by_mold["RX39513"].ended_at == "2026-07-09T07:00:00"
    assert by_mold["RX28312"].ended_at is None, "B 는 아직 설비에 있다"


def test_mold_no_comes_from_the_row_and_mes_keys_come_from_the_equipment():
    """식별은 행의 JIG ID, MES 조회 키는 이벤트 설비명 — 둘의 출처가 다르다.

    이 금형이 다른 JIG ID 에 등록된 설비에서 돌아도 실적은 **그 설비**의
    것이어야 하고, 금형번호는 여전히 행의 JIG ID 여야 한다."""
    rows = [
        _event("2026-07-01T07:00:00", "설비", mold_no="#RX39513",
               equipment="POU WND10_Stack(1차)_02", no=1),
        _event("2026-07-02T07:00:00", "통합 Jig Room", mold_no="#RX39513", no=2),
    ]

    runs, losses = extract_runs(rows, JIG_INDEX, EQUIP_INDEX)

    assert runs[0].mold_no == "RX39513", "기준정보가 아니라 행의 JIG ID 다"
    assert runs[0].equipment_code == "21004781", "그 설비의 코드로 MES 를 본다"
    assert runs[0].line == "톈진 Pouch #11(S)", "라인도 그 설비 기준"
    assert losses.unknown_equipment == []


def test_equipment_code_follows_the_mold_as_it_moves_between_machines():
    """금형이 설비를 옮겨 다니면 구간마다 조회할 설비코드가 달라진다.
    기준정보의 현재 설비 하나로 고정하면 과거 구간에 남의 실적이 붙는다."""
    rows = [
        _event("2026-07-01T07:00:00", "설비", no=1),
        _event("2026-07-02T07:00:00", "통합 Jig Room", no=2),
        _event("2026-07-10T07:00:00", "설비",
               equipment="POU WND10_Stack(1차)_02", no=3),
        _event("2026-07-11T07:00:00", "통합 Jig Room", no=4),
    ]

    runs, _ = extract_runs(rows, JIG_INDEX, EQUIP_INDEX)

    assert [r.equipment_code for r in runs] == ["21004780", "21004781"]
    assert {r.mold_no for r in runs} == {"RX39513"}, "금형은 내내 하나다"


def test_unknown_equipment_falls_back_to_the_jig_id_row_and_warns():
    """설비명이 기준정보에 없어도 금형은 사라지지 않는다 — JIG ID 행의
    설비코드로 조회한다. 다만 그 조회가 '현재 설비 기준'이라는 사실이
    드러나야 사람이 기준정보를 고칠지 판단할 수 있다."""
    rows = [
        _event("2026-07-01T07:00:00", "설비", equipment="POU 신규설비_01", no=1),
        _event("2026-07-02T07:00:00", "통합 Jig Room", no=2),
    ]

    runs, losses = extract_runs(rows, JIG_INDEX, EQUIP_INDEX)

    assert runs[0].mold_no == "RX39513"
    assert runs[0].equipment_code == "21004780", "JIG ID 행으로 폴백"
    assert runs[0].equipment == "POU 신규설비_01", "설비명 원문은 남긴다"
    assert losses.unknown_equipment == ["POU 신규설비_01"]
    assert losses.unknown_jig_id == [], "금형이 빠진 것은 아니다"


def test_jig_id_missing_from_the_master_makes_no_runs_and_is_reported():
    """기준정보에 없는 JIG ID 는 MES 조회 키를 얻을 수 없어 금형이 통째로
    빠진다. 가장 흔한 사고이므로 번호를 모아 화면에 띄운다."""
    rows = [
        _event("2026-07-01T07:00:00", "설비", mold_no="#RX77777",
               equipment="POU WND99_Unknown_01", no=1),
        _event("2026-07-02T07:00:00", "통합 Jig Room", mold_no="#RX77777",
               equipment="POU WND99_Unknown_01", no=2),
    ]

    runs, losses = extract_runs(rows, JIG_INDEX, EQUIP_INDEX)

    assert runs == []
    assert losses.unknown_jig_id == ["RX77777"], "# 를 뗀 정규형으로 보고한다"
    assert losses.unknown_equipment == [], "설비명 경고로 이중 보고하지 않는다"


def test_rows_without_a_jig_id_are_counted():
    """JIG ID 를 못 읽은 행은 어느 금형에도 못 붙는다. 버리되 센다."""
    rows = [
        _event("2026-07-01T07:00:00", "설비", mold_no=None, no=1),
        _event("2026-07-01T07:00:00", "설비", no=2),
    ]

    runs, losses = extract_runs(rows, JIG_INDEX, EQUIP_INDEX)

    assert len(runs) == 1
    assert losses.rows_without_mold_no == 1


def test_unreadable_event_time_is_counted():
    """시각을 못 읽으면 조회할 날짜를 정할 수 없다. 추측하지 않고 버리되 센다."""
    rows = [
        _event("언제였더라", "설비", no=1),
        _event("2026-07-01T07:00:00", "설비", no=2),
    ]

    runs, losses = extract_runs(rows, JIG_INDEX, EQUIP_INDEX)

    assert len(runs) == 1
    assert losses.bad_event_times == 1


def test_the_same_event_in_two_files_is_folded_into_one():
    """금형 이력은 파일 경계를 넘어 이어진다. 그래서 파일로 나누지 않는데,
    관리대장이 겹쳐 올라오면 같은 이벤트가 두 번 세어져 길이 0 짜리 유령
    구간이 생긴다. 완전히 같은 이벤트는 하나로 접는다."""
    rows = [
        _event("2026-07-01T07:00:00", "설비", file="ledger.xlsx", no=1),
        _event("2026-07-05T07:00:00", "통합 Jig Room", file="ledger.xlsx", no=2),
        _event("2026-07-01T07:00:00", "설비", file="ledger_2026.xlsx", no=1),
        _event("2026-07-05T07:00:00", "통합 Jig Room", file="ledger_2026.xlsx", no=2),
    ]

    runs, _ = extract_runs(rows, JIG_INDEX, EQUIP_INDEX)

    assert len(runs) == 1
    assert runs[0].ended_at == "2026-07-05T07:00:00"


# ── 현재 위치 ────────────────────────────────────────────────────────
def test_latest_locations_is_keyed_by_jig_id():
    """상태는 '그 금형이 지금 어디에 있는가' 다. 설비가 아닌 위치도 필요하다 —
    수리실에 있는 금형도 화면에 나와야 한다."""
    rows = [
        _event("2026-07-01T07:00:00", "설비", no=1),
        _event("2026-07-05T07:00:00", "내부 수리", no=2),
        _event("2026-07-02T08:00:00", "설비", mold_no="RX28312", no=3),
    ]

    assert latest_locations(rows) == {
        "RX39513": "내부 수리", "RX28312": "설비"
    }


def test_latest_locations_keeps_events_without_an_equipment_name():
    """보관함에 있는 이벤트는 설비명이 비어 있을 수 있다. 그 행을 버리면
    금형이 목록에서 사라진다."""
    rows = [
        _event("2026-07-05T07:00:00", "통합 Jig Room", equipment=None, no=1),
    ]

    assert latest_locations(rows) == {"RX39513": "통합 Jig Room"}


# ── 날짜 계산 ────────────────────────────────────────────────────────
def test_covered_dates_uses_24h_windows_from_the_start_time():
    """투입 시각 기준 24시간 단위. 96시간은 정확히 4일이고 5일째는 안 들어간다."""
    assert covered_dates("2026-07-01T07:00:00", "2026-07-05T07:00:00") == [
        date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3), date(2026, 7, 4)
    ]


def test_covered_dates_includes_next_day_once_past_the_same_clock_time():
    """07-02 08:30 투입이 07-03 09:00 에 끝나면 다음날 08:30 을 넘었으므로
    07-03 도 대조 대상이다."""
    assert covered_dates("2026-07-02T08:30:00", "2026-07-03T09:00:00") == [
        date(2026, 7, 2), date(2026, 7, 3)
    ]


def test_covered_dates_within_one_day_is_a_single_date():
    assert covered_dates("2026-07-08T06:00:00", "2026-07-08T18:00:00") == [
        date(2026, 7, 8)
    ]


def test_covered_dates_crossing_midnight_but_under_24h_is_one_day():
    """자정을 넘겼다고 이틀이 아니다 — 기준은 자정이 아니라 24시간이다."""
    assert covered_dates("2026-07-10T22:00:00", "2026-07-11T03:00:00") == [
        date(2026, 7, 10)
    ]


def test_covered_dates_is_empty_for_an_open_run():
    """아직 가동 중이면 대조할 구간이 확정되지 않았다."""
    assert covered_dates("2026-07-14T09:00:00", None) == []


# ── MES 색인과 합산 ──────────────────────────────────────────────────
def _mes(day: str, code: str, produced: int, defects: int, line="톈진 Pouch #10(S)"):
    return _row({
        "run_date": day, "line": line, "equipment_code": code,
        "produced": str(produced), "defects": str(defects),
    }, file=f"{day}.xlsx", sheet="불량현황")


def _runs_between(start: str, end: str, **kw):
    """사용구간 하나를 만든다. MES 합산 테스트의 공통 준비다."""
    runs, _ = extract_runs(
        [_event(start, "설비", no=1, **kw),
         _event(end, "통합 Jig Room", no=2, **kw)],
        JIG_INDEX, EQUIP_INDEX,
    )
    return runs


def test_index_mes_is_keyed_by_date_and_equipment_code():
    rows = [
        _mes("2026.07.01-2026.07.01", "21004780", 9000, 100),
        _mes("2026.07.02-2026.07.02", "21004780", 11000, 90),
    ]

    index, dropped = index_mes(rows)

    assert index[(date(2026, 7, 1), "21004780")] == (9000, 100)
    assert index[(date(2026, 7, 2), "21004780")] == (11000, 90)
    assert dropped == []


def test_index_mes_skips_the_total_row():
    """TOTAL 은 라인이 아니다. 설비코드가 없으므로 자연히 빠져야 한다."""
    rows = [
        _mes("2026.07.01-2026.07.01", "21004780", 9000, 100),
        _row({"run_date": "2026.07.01-2026.07.01", "line": "TOTAL",
              "equipment_code": None, "produced": "50000", "defects": "600"},
             file="d.xlsx", sheet="불량현황"),
    ]

    index, _ = index_mes(rows)

    assert len(index) == 1


def test_defect_rate_is_recomputed_from_summed_raw_counts():
    """비율의 평균이 아니라 raw 를 합쳐 다시 계산한다. 생산량이 다른 날을
    같은 무게로 세면 틀린 값이 나온다 — 아래 숫자가 실제로 갈린다."""
    index, _ = index_mes([
        _mes("2026.07.01-2026.07.01", "21004780", 10000, 300),   # 3.0%
        _mes("2026.07.02-2026.07.02", "21004780", 1000, 10),     # 1.0%
    ])
    runs = _runs_between("2026-07-01T07:00:00", "2026-07-03T07:00:00")

    losses = attach_defect_rates(runs, index)

    run = runs[0]
    assert run.produced == 11000 and run.defects == 310
    # 합산: 310/11000 = 2.818%. 단순 평균이면 2.0% 라 확연히 다르다.
    assert round(run.defect_rate, 5) == round(310 / 11000, 5)
    assert abs(run.defect_rate - 0.02) > 0.005, "단순 평균이 아님이 드러나야 한다"
    assert [d.date for d in run.daily] == ["2026-07-01", "2026-07-02"]
    assert losses.unmatched_runs == 0


def test_a_moved_mold_reads_each_run_from_its_own_machine():
    """설비 이동이 실제로 다른 실적을 가져오는지 — 이 재설계의 핵심 증거다."""
    index, _ = index_mes([
        _mes("2026.07.01-2026.07.01", "21004780", 10000, 300),   # 설비 01
        _mes("2026.07.10-2026.07.10", "21004781", 10000, 50),    # 설비 02
    ])
    runs, _ = extract_runs([
        _event("2026-07-01T07:00:00", "설비", no=1),
        _event("2026-07-02T07:00:00", "통합 Jig Room", no=2),
        _event("2026-07-10T07:00:00", "설비",
               equipment="POU WND10_Stack(1차)_02", no=3),
        _event("2026-07-11T07:00:00", "통합 Jig Room", no=4),
    ], JIG_INDEX, EQUIP_INDEX)

    attach_defect_rates(runs, index)

    assert [r.defects for r in runs] == [300, 50]
    assert runs[0].defect_rate != runs[1].defect_rate


def test_missing_mes_day_is_reported_and_the_rest_still_counts():
    """하루 파일이 없다고 그 구간을 통째로 버리지 않는다. 다만 일부만 반영된
    값이라는 사실이 드러나야 한다."""
    index, _ = index_mes([_mes("2026.07.01-2026.07.01", "21004780", 10000, 300)])
    runs = _runs_between("2026-07-01T07:00:00", "2026-07-03T07:00:00")

    losses = attach_defect_rates(runs, index)

    assert runs[0].produced == 10000
    assert losses.missing_mes_days == ["2026-07-02"]


def test_run_with_no_matching_mes_rows_at_all_is_counted():
    runs = _runs_between("2026-07-01T07:00:00", "2026-07-02T07:00:00")

    losses = attach_defect_rates(runs, {})

    assert runs[0].defect_rate is None
    assert losses.unmatched_runs == 1


def test_open_run_is_not_counted_as_a_join_failure():
    """가동 중이라 불량율이 없는 것과 조인이 깨진 것은 다른 사건이다."""
    runs, _ = extract_runs([_event("2026-07-14T09:00:00", "설비", no=1)],
                           JIG_INDEX, EQUIP_INDEX)

    losses = attach_defect_rates(runs, {})

    assert runs[0].defect_rate is None
    assert losses.unmatched_runs == 0, "가동 중은 실패가 아니다"
