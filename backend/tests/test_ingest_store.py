"""금형 데이터 저장. 배치마다 트랜잭션 안에서 전체 교체된다."""
import pytest

from app.ingest import db
from app.ingest.schemas import MoldRecord, StageItemRecord
from app.ingest.store import replace_all


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / "molds.db")
    db.init_db(path)
    c = db.connect(path)
    yield c
    c.close()


def _rec(mold_no, **kw):
    return MoldRecord(mold_no=mold_no, status="in_use", source_file="mes.xlsx", **kw)


def test_inserts_molds(conn):
    replace_all(conn, [_rec("RX28312", machine="2", shot_count=8412)])
    row = conn.execute("SELECT * FROM mold WHERE mold_no='RX28312'").fetchone()
    assert row["machine"] == "2"
    assert row["shot_count"] == 8412


def test_none_quantities_are_stored_as_null_not_zero(conn):
    """0 으로 저장하면 '신품'이라는 거짓말이 DB 에 남는다."""
    replace_all(conn, [_rec("RX28312")])
    row = conn.execute("SELECT * FROM mold").fetchone()
    assert row["shot_count"] is None
    assert row["total_production"] is None


def test_replace_removes_molds_absent_from_new_batch(conn):
    """MES 에서 사라진 금형은 대시보드에서도 사라져야 한다. upsert 만 하면
    유령 금형이 영원히 남는다."""
    replace_all(conn, [_rec("RX28312"), _rec("RX28315")])
    replace_all(conn, [_rec("RX28312")])

    nos = [r["mold_no"] for r in conn.execute("SELECT mold_no FROM mold")]
    assert nos == ["RX28312"]


def test_iqc_items_are_stored_as_stage_row(conn):
    replace_all(conn, [_rec("RX28312", iqc_source_file="iqc.xlsx", iqc_items=[
        StageItemRecord(label="punch", value="12.5", source_file="iqc.xlsx",
                        source_sheet="Sheet1"),
    ])])
    row = conn.execute("SELECT * FROM mold_stage WHERE stage='iqc'").fetchone()
    assert row["mold_no"] == "RX28312"
    assert row["status"] == "ok"
    assert "punch" in row["items_json"]
    assert row["source_file"] == "iqc.xlsx"


def test_mold_without_iqc_has_no_stage_row(conn):
    """행이 없는 것이 곧 missing 이다. status='missing' 행을 만들면 '아직
    안 올라옴'과 '올라왔는데 비었음'을 DB 에 억지로 구분해 넣게 된다."""
    replace_all(conn, [_rec("RX28312")])
    assert conn.execute("SELECT COUNT(*) c FROM mold_stage").fetchone()["c"] == 0


def test_stage_rows_are_also_replaced(conn):
    replace_all(conn, [_rec("RX28312", iqc_items=[
        StageItemRecord(label="punch", value="12.5", source_file="iqc.xlsx")
    ])])
    replace_all(conn, [_rec("RX28312")])
    assert conn.execute("SELECT COUNT(*) c FROM mold_stage").fetchone()["c"] == 0


def test_failed_batch_rolls_back_and_keeps_previous_data(conn):
    """반쯤 교체된 상태를 만들지 않는다 — 옛 금형과 새 부속정보가 섞이면
    어느 쪽도 믿을 수 없다."""
    replace_all(conn, [_rec("RX28312")])

    bad = _rec("RX28315")
    bad.status = None  # type: ignore[assignment]  # NOT NULL 제약 위반 유도
    with pytest.raises(Exception):
        replace_all(conn, [bad])

    nos = [r["mold_no"] for r in conn.execute("SELECT mold_no FROM mold")]
    assert nos == ["RX28312"]
