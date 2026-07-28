"""query.py 가 DB 를 본다. 시그니처는 그대로여서 라우터·프론트는 안 바뀐다."""
import pytest

from app.ingest import db
from app.ingest.schemas import MoldRecord, StageItemRecord
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
