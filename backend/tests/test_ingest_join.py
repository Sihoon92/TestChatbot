"""EES 사용구간 추출과 MES 불량율 조인.

Excel 도 LLM 도 DB 도 모르는 순수 계산이다 — Row 리터럴만으로 전부 검증된다.
조인이 조용히 어긋나면 엉뚱한 날의 불량율이 금형에 붙는데, 화면만 봐서는
절대 알 수 없다. 그래서 경계 조건을 하나씩 못 박는다.
"""
from datetime import date

from app.ingest.join import (
    attach_defect_rates,
    build_jig_index,
    covered_dates,
    extract_runs,
    index_mes,
)
from app.ingest.schemas import Row


def _row(values: dict, *, sheet="Sheet1", file="ledger.xlsx", no=1) -> Row:
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
        "equipment_code": "21004781", "line": "톈진 Pouch #10(S)",
    }, file="master.xlsx", no=2),
]


def test_jig_index_is_keyed_by_equipment_name():
    """관리대장이 아는 것은 설비명뿐이다. 그것으로 못 찾으면 금형이 없다."""
    index, dropped = build_jig_index(MASTER)

    assert set(index) == {"POU WND10_Stack(1차)_01", "POU WND10_Stack(1차)_02"}
    assert index["POU WND10_Stack(1차)_01"].mold_no == "RX39513", "# 가 떨어져야 한다"
    assert index["POU WND10_Stack(1차)_01"].equipment_code == "21004780"
    assert dropped == []


def test_jig_index_drops_rows_without_a_key_and_says_so():
    """설비명이나 금형번호가 없는 행은 다리 역할을 못 한다. 조용히 버리면
    나중에 '왜 이 금형이 안 나오지' 를 추적할 수 없다."""
    rows = MASTER + [
        _row({"mold_no": "RX99999", "equipment": None}, file="master.xlsx", no=3),
        _row({"mold_no": None, "equipment": "POU WND99_X_01"}, file="master.xlsx", no=4),
    ]

    index, dropped = build_jig_index(rows)

    assert len(index) == 2
    assert len(dropped) == 2


def test_jig_index_last_row_wins_for_duplicate_equipment():
    """같은 설비명이 두 번 나오면 뒤에 나온 것이 최신이라고 본다."""
    rows = MASTER + [
        _row({
            "mold_no": "RX00001", "equipment": "POU WND10_Stack(1차)_01",
            "equipment_code": "21009999", "line": "천안 Pouch #1(L)",
        }, file="master.xlsx", no=9),
    ]

    index, _ = build_jig_index(rows)

    assert index["POU WND10_Stack(1차)_01"].mold_no == "RX00001"


# ── 사용구간 추출 ────────────────────────────────────────────────────
# 설비명이 기준정보에 없으면 금형번호를 얻을 수 없어 구간이 아예 안 만들어진다.
# 그래서 대부분의 테스트는 실제 색인을 넘긴다(빈 dict 를 넘기면 '기준정보에
# 없는 설비' 경로를 타게 되어 의도와 다른 것을 검증하게 된다).
INDEX = build_jig_index(MASTER)[0]


def _event(when: str, location: str, equipment="POU WND10_Stack(1차)_01",
           sheet="음극 Notching 금형", no=1) -> Row:
    return _row(
        {"event_at": when, "location": location, "equipment": equipment},
        sheet=sheet, no=no,
    )


def test_run_starts_at_설비_and_ends_at_the_next_event():
    """위치가 '설비' 인 이벤트가 투입, **바로 다음 이벤트**가 종료다.
    다음 이벤트의 위치가 무엇이든 상관없다 — 설비를 떠났다는 사실만 중요하다."""
    rows = [
        _event("2026-07-01T06:00:00", "통합 Jig Room", no=1),
        _event("2026-07-01T07:00:00", "설비", no=2),
        _event("2026-07-05T07:00:00", "내부 수리", no=3),
    ]

    runs, losses = extract_runs(rows, INDEX)

    assert len(runs) == 1
    assert runs[0].started_at == "2026-07-01T07:00:00"
    assert runs[0].ended_at == "2026-07-05T07:00:00"
    assert losses.open_runs == 0


def test_last_event_still_at_설비_is_an_open_run():
    """아직 설비에 있으면 종료가 없다. 이걸 '조인 실패'와 같은 None 으로
    뭉개면 사람이 원인을 구분할 수 없다."""
    rows = [
        _event("2026-07-14T09:00:00", "설비", no=1),
    ]

    runs, losses = extract_runs(rows, INDEX)

    assert runs[0].ended_at is None
    assert losses.open_runs == 1


def test_multiple_runs_in_one_sheet():
    rows = [
        _event("2026-07-02T08:30:00", "설비", no=1),
        _event("2026-07-03T09:00:00", "사용 대기 보관함", no=2),
        _event("2026-07-11T06:00:00", "설비", no=3),
        _event("2026-07-11T20:00:00", "반납 대기 보관함", no=4),
    ]

    runs, _ = extract_runs(rows, INDEX)

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

    runs, _ = extract_runs(rows, INDEX)

    assert runs[0].started_at == "2026-07-01T07:00:00"
    assert runs[0].ended_at == "2026-07-05T07:00:00"


def test_each_sheet_is_a_separate_mold():
    """시트를 섞으면 A 금형의 설비 진입이 B 금형의 이벤트로 닫힌다."""
    two, _ = build_jig_index([
        _row({"mold_no": "RX00001", "equipment": "EQ_A",
              "equipment_code": "1", "line": "L1"}, file="master.xlsx"),
        _row({"mold_no": "RX00002", "equipment": "EQ_B",
              "equipment_code": "2", "line": "L2"}, file="master.xlsx", no=2),
    ])
    rows = [
        _event("2026-07-01T07:00:00", "설비", sheet="금형A", equipment="EQ_A", no=1),
        _event("2026-07-01T08:00:00", "설비", sheet="금형B", equipment="EQ_B", no=1),
        _event("2026-07-09T07:00:00", "Jig Room", sheet="금형A", equipment="EQ_A", no=2),
    ]

    runs, _ = extract_runs(rows, two)

    by_equip = {r.equipment: r for r in runs}
    assert by_equip["EQ_A"].ended_at == "2026-07-09T07:00:00"
    assert by_equip["EQ_B"].ended_at is None, "B 는 아직 설비에 있다"


def test_runs_get_mold_no_from_the_jig_index():
    index, _ = build_jig_index(MASTER)
    rows = [_event("2026-07-01T07:00:00", "설비", no=1)]

    runs, losses = extract_runs(rows, index)

    assert runs[0].mold_no == "RX39513"
    assert runs[0].equipment_code == "21004780"
    assert runs[0].line == "톈진 Pouch #10(S)", "라인은 기준정보 것을 쓴다"
    assert losses.unknown_equipment == []


def test_equipment_missing_from_the_master_is_reported_not_dropped_silently():
    """기준정보가 낡으면 금형이 통째로 사라진다. 가장 흔한 사고이므로
    설비명 원문을 모아 화면에 띄운다."""
    rows = [_event("2026-07-01T07:00:00", "설비", equipment="POU 신규설비_01", no=1)]

    runs, losses = extract_runs(rows, INDEX)

    assert runs == []
    assert losses.unknown_equipment == ["POU 신규설비_01"]


def test_unreadable_event_time_is_counted():
    """시각을 못 읽으면 조회할 날짜를 정할 수 없다. 추측하지 않고 버리되 센다."""
    rows = [
        _event("언제였더라", "설비", no=1),
        _event("2026-07-01T07:00:00", "설비", no=2),
    ]

    runs, losses = extract_runs(rows, INDEX)

    assert len(runs) == 1
    assert losses.bad_event_times == 1


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


def test_index_mes_is_keyed_by_date_and_equipment_code():
    rows = [
        _mes("2026.07.01-2026.07.01", "21004780", 9000, 100),
        _mes("2026.07.02-2026.07.02", "21004780", 11000, 90),
    ]

    index, losses = index_mes(rows)

    assert index[(date(2026, 7, 1), "21004780")] == (9000, 100)
    assert index[(date(2026, 7, 2), "21004780")] == (11000, 90)
    assert losses == []


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
    runs, _ = extract_runs(
        [_event("2026-07-01T07:00:00", "설비", no=1),
         _event("2026-07-03T07:00:00", "Jig Room", no=2)],
        build_jig_index(MASTER)[0],
    )

    losses = attach_defect_rates(runs, index)

    run = runs[0]
    assert run.produced == 11000 and run.defects == 310
    # 합산: 310/11000 = 2.818%. 단순 평균이면 2.0% 라 확연히 다르다.
    assert round(run.defect_rate, 5) == round(310 / 11000, 5)
    assert abs(run.defect_rate - 0.02) > 0.005, "단순 평균이 아님이 드러나야 한다"
    assert [d.date for d in run.daily] == ["2026-07-01", "2026-07-02"]
    assert losses.unmatched_runs == 0


def test_missing_mes_day_is_reported_and_the_rest_still_counts():
    """하루 파일이 없다고 그 구간을 통째로 버리지 않는다. 다만 일부만 반영된
    값이라는 사실이 드러나야 한다."""
    index, _ = index_mes([_mes("2026.07.01-2026.07.01", "21004780", 10000, 300)])
    runs, _ = extract_runs(
        [_event("2026-07-01T07:00:00", "설비", no=1),
         _event("2026-07-03T07:00:00", "Jig Room", no=2)],
        build_jig_index(MASTER)[0],
    )

    losses = attach_defect_rates(runs, index)

    assert runs[0].produced == 10000
    assert losses.missing_mes_days == ["2026-07-02"]


def test_run_with_no_matching_mes_rows_at_all_is_counted():
    runs, _ = extract_runs(
        [_event("2026-07-01T07:00:00", "설비", no=1),
         _event("2026-07-02T07:00:00", "Jig Room", no=2)],
        build_jig_index(MASTER)[0],
    )

    losses = attach_defect_rates(runs, {})

    assert runs[0].defect_rate is None
    assert losses.unmatched_runs == 1


def test_open_run_is_not_counted_as_a_join_failure():
    """가동 중이라 불량율이 없는 것과 조인이 깨진 것은 다른 사건이다."""
    runs, _ = extract_runs([_event("2026-07-14T09:00:00", "설비", no=1)],
                           build_jig_index(MASTER)[0])

    losses = attach_defect_rates(runs, {})

    assert runs[0].defect_rate is None
    assert losses.unmatched_runs == 0, "가동 중은 실패가 아니다"
