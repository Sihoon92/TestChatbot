"""query.py 가 DB 를 본다. 시그니처는 그대로여서 라우터·프론트는 안 바뀐다."""
import pytest

from app.ingest import db
from app.ingest.schemas import (
    DailyDefect,
    MoldRecord,
    StageItemRecord,
    UsageRun,
)
from app.ingest.store import replace_all
from app.molds import query


@pytest.fixture
def seeded(monkeypatch, tmp_path):
    path = str(tmp_path / "molds.db")
    db.init_db(path)
    conn = db.connect(path)
    replace_all(conn, [
        MoldRecord(mold_no="RX28312", status="in_use", line="3", machine="2",
                   shot_count=8412, latest_defect_rate=0.008,
                   source_file="mes.xlsx",
                   iqc_source_file="iqc.xlsx",
                   iqc_items=[StageItemRecord(label="punch", value="12.5",
                                              source_file="iqc.xlsx",
                                              source_sheet="Sheet1")]),
        MoldRecord(mold_no="RX41194", status="standby", source_file="mes.xlsx"),
    ])
    conn.close()

    monkeypatch.setenv("MOLDS_DB_PATH", path)
    from app.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_list_molds_reads_from_db(seeded):
    nos = [m.mold_no for m in query.list_molds()]
    assert nos == ["RX28312", "RX41194"]


def test_list_molds_filters(seeded):
    assert [m.mold_no for m in query.list_molds(status="standby")] == ["RX41194"]
    assert [m.mold_no for m in query.list_molds(machine="2")] == ["RX28312"]
    assert [m.mold_no for m in query.list_molds(q="41194")] == ["RX41194"]
    assert [m.mold_no for m in query.list_molds(q=" rx28312 ")] == ["RX28312"]


def test_list_molds_combines_filters(seeded):
    """조건을 동시에 걸 때 파라미터 순서가 어긋나지 않는지 확인한다.
    list_molds 는 SQL 문자열과 params 리스트를 각각 순서대로 이어붙이므로,
    나중에 조건을 추가·재배치할 때 둘이 어긋나면 엉뚱한 열로 걸러진다."""
    assert [m.mold_no for m in query.list_molds(status="in_use", line="3", machine="2")] == ["RX28312"]
    assert [m.mold_no for m in query.list_molds(status="in_use", q="283")] == ["RX28312"]


def test_list_molds_impossible_combination_is_empty(seeded):
    """존재하지 않는 조합은 예외가 아니라 0건이다.
    RX28312 는 사용중/3-2 이고 RX41194 는 대기중이라 호기가 없다."""
    assert query.list_molds(status="standby", line="3") == []
    assert query.list_molds(status="in_use", machine="99") == []


def test_get_mold_builds_detail_with_iqc_items(seeded):
    detail = query.get_mold("RX28312")
    assert detail is not None
    assert detail.summary.shot_count == 8412
    iqc = [s for s in detail.stages if s.stage == "iqc"][0]
    assert iqc.status == "ok"
    assert iqc.items[0].label == "punch"
    assert iqc.items[0].source.file == "iqc.xlsx"


def test_stage_without_row_is_missing(seeded):
    """행이 없는 것이 곧 missing 이다. 5단계가 모두 채워져야 탭 배지가 뜬다."""
    detail = query.get_mold("RX41194")
    assert detail.summary.stage_status["iqc"] == "missing"
    assert detail.summary.stage_status["pqc"] == "missing"
    assert set(detail.summary.stage_status) == {
        "design", "iqc", "pqc", "install", "ai_recheck"
    }


def test_get_mold_unknown_returns_none(seeded):
    assert query.get_mold("RX00000") is None


def test_filter_options_statuses_are_fixed_even_when_db_lacks_them(seeded):
    """상태는 도메인 어휘이지 데이터의 산물이 아니다 — '폐기' 금형이 지금
    없다고 '폐기'로 조회할 수단이 사라지면 안 된다."""
    opts = query.filter_options()
    assert [s for s in opts.statuses] == ["in_use", "standby", "repair", "retired"]
    assert [(i.line, i.machine) for i in opts.installations] == [("3", "2")]


def test_empty_db_returns_empty_not_error(monkeypatch, tmp_path):
    path = str(tmp_path / "empty.db")
    db.init_db(path)
    monkeypatch.setenv("MOLDS_DB_PATH", path)
    from app.config import get_settings
    get_settings.cache_clear()
    try:
        assert query.list_molds() == []
        assert query.get_mold("RX28312") is None
        assert query.filter_options().installations == []
    finally:
        get_settings.cache_clear()


# ── 설비 사용구간 (production_run → MoldDetail.productions) ──────────────
# 수집이 production_run 에 쓰고 있었는데 조회가 한 번도 안 꺼냈다. 화면의
# "기간별 불량율" 표가 이 경로 하나에 달려 있다.


@pytest.fixture
def with_runs(monkeypatch, tmp_path):
    """사용구간이 있는 금형을 시드한다.

    replace_all 이 이미 production_run 을 쓰므로 MoldRecord(runs=[...]) 만
    넘기면 된다 — 테스트가 INSERT 를 직접 짜면 저장 형식이 바뀔 때 조용히
    어긋난다.
    """
    path = str(tmp_path / "molds.db")
    db.init_db(path)
    conn = db.connect(path)
    replace_all(conn, [
        MoldRecord(
            mold_no="RX39513", status="standby", source_file="ledger.xlsx",
            runs=[
                # 4일짜리 구간인데 MES 가 3일치뿐 — 일부만 반영된 불량율이다.
                UsageRun(
                    mold_no="RX39513", equipment="POU WND10_Stack(1차)_01",
                    equipment_code="21004780", line="톈진 Pouch #10(S)",
                    started_at="2026-07-01T07:00:00",
                    ended_at="2026-07-05T07:00:00",
                    source_file="ledger.xlsx", source_sheet="#RX39513",
                    produced=29439, defects=369, defect_rate=369 / 29439,
                    daily=[
                        DailyDefect(date="2026-07-01", produced=9480, defects=120),
                        DailyDefect(date="2026-07-02", produced=10627, defects=157),
                        DailyDefect(date="2026-07-04", produced=9332, defects=92),
                    ],
                ),
                # 아직 설비에 있다 — 종료도 불량율도 없다.
                UsageRun(
                    mold_no="RX39513", equipment="POU WND10_Stack(1차)_01",
                    equipment_code="21004780", line=None,
                    started_at="2026-07-14T09:00:00", ended_at=None,
                    source_file="ledger.xlsx", source_sheet="#RX39513",
                ),
            ],
        ),
        MoldRecord(mold_no="RX00000", status="standby", source_file="ledger.xlsx"),
    ])
    conn.close()

    monkeypatch.setenv("MOLDS_DB_PATH", path)
    from app.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_runs_come_back_in_install_order_with_summed_quantities(with_runs):
    runs = query.get_mold("RX39513").productions

    assert [r.install_seq for r in runs] == [1, 2]
    first = runs[0]
    assert first.produced == 9480 + 10627 + 9332
    assert first.defect_count == 120 + 157 + 92
    assert first.line == "톈진 Pouch #10(S)"
    assert first.machine == "POU WND10_Stack(1차)_01"


def test_partial_run_reports_covered_vs_expected_days(with_runs):
    """4일을 덮는 구간인데 MES 실적은 3일치뿐이다. 이 차이가 안 보이면
    화면의 1.253% 를 완전한 값으로 오해한다."""
    first = query.get_mold("RX39513").productions[0]

    assert first.days_covered == 3
    assert first.days_expected == 4


def test_open_run_has_no_days_and_no_rate(with_runs):
    """가동 중인 구간은 대조할 기간이 확정되지 않았다 — 조인 실패와 다르다."""
    second = query.get_mold("RX39513").productions[1]

    assert second.ended_at is None
    assert second.days_expected == 0
    assert second.defect_rate is None
    assert second.produced is None


def test_null_line_does_not_break_the_detail_response(with_runs):
    """기준정보에 Line명이 없는 금형 하나 때문에 상세 조회 전체가 500 이
    되면 안 된다. 값 하나가 비는 것과 화면이 안 뜨는 것은 다른 사건이다."""
    second = query.get_mold("RX39513").productions[1]

    assert second.line is None


def test_install_stage_is_ok_when_runs_exist(with_runs):
    """install 은 mold_stage 에 행을 안 만든다. production_run 을 안 보면
    표에 구간이 가득한데도 탭 배지가 '없음' 이 되어 화면이 모순된다."""
    assert query.get_mold("RX39513").summary.stage_status["install"] == "ok"


def test_install_stage_stays_missing_without_runs(with_runs):
    detail = query.get_mold("RX00000")

    assert detail.productions == []
    assert detail.summary.stage_status["install"] == "missing"
