"""처리 이력·레이아웃 캐시·배치 요약의 영속화. 인메모리 SQLite 로 검증한다."""
import pytest

from app.ingest import db, registry
from app.ingest.schemas import AnchorCheck, FoundFile, RunSummary, SheetLayout


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / "molds.db")
    db.init_db(path)
    c = db.connect(path)
    yield c
    c.close()


def _layout(text="금형번호", sheet="Sheet1"):
    return SheetLayout(sheet_name=sheet, anchors=[AnchorCheck(cell="B4", text=text)])


def test_known_hashes_empty_at_first(conn):
    assert registry.known_hashes(conn) == {}


def test_record_files_then_read_back(conn):
    registry.record_files(conn, [
        FoundFile(path="a.xlsx", kind="mes", content_hash="h1"),
        FoundFile(path="b.xlsx", kind="iqc", content_hash="h2"),
    ])
    assert registry.known_hashes(conn) == {"a.xlsx": "h1", "b.xlsx": "h2"}


def test_record_files_updates_hash_on_same_path(conn):
    """같은 파일이 갱신되면 해시만 바뀌고 행이 늘어나면 안 된다."""
    registry.record_files(conn, [FoundFile(path="a.xlsx", kind="mes", content_hash="h1")])
    registry.record_files(conn, [FoundFile(path="a.xlsx", kind="mes", content_hash="h2")])
    assert registry.known_hashes(conn) == {"a.xlsx": "h2"}


def test_load_layouts_returns_newest_first(conn):
    """양식이 바뀌면 새 레이아웃이 쌓인다. 최신부터 대조해야 바뀐 양식이
    먼저 맞는다 — 옛 레이아웃을 먼저 시도하면 앵커가 우연히 겹칠 때
    옛 규칙으로 파싱한다."""
    registry.save_layout(conn, "iqc", _layout("관리번호"), "old.xlsx")
    registry.save_layout(conn, "iqc", _layout("금형번호"), "new.xlsx")

    layouts = registry.load_layouts(conn, "iqc", "Sheet1")

    assert [l.anchors[0].text for l in layouts] == ["금형번호", "관리번호"]


def test_load_layouts_filters_by_kind_and_sheet(conn):
    registry.save_layout(conn, "iqc", _layout(sheet="Sheet1"), "a.xlsx")
    registry.save_layout(conn, "mes", _layout(sheet="Sheet1"), "b.xlsx")

    assert len(registry.load_layouts(conn, "iqc", "Sheet1")) == 1
    assert registry.load_layouts(conn, "iqc", "다른시트") == []


def test_old_layouts_are_kept_for_traceability(conn):
    """옛 레이아웃을 지우지 않는다 — '언제 양식이 바뀌었고 그때 어떻게
    해석했는가' 가 추출값을 의심할 때 유일한 근거다."""
    registry.save_layout(conn, "iqc", _layout("관리번호"), "old.xlsx")
    registry.save_layout(conn, "iqc", _layout("금형번호"), "new.xlsx")
    assert len(registry.load_layouts(conn, "iqc", "Sheet1")) == 2


def test_record_and_read_latest_run(conn):
    registry.record_run(conn, RunSummary(
        status="ok", started_at="2026-07-28T09:00:00",
        finished_at="2026-07-28T09:00:12",
        mold_count=4, iqc_matched=3,
        orphan_mold_nos=["RX99999"], unknown_statuses=["가동"],
        skipped_rows=2, files=["mes.xlsx", "iqc.xlsx"],
    ))

    latest = registry.latest_run(conn)

    assert latest is not None
    assert latest.mold_count == 4
    assert latest.orphan_mold_nos == ["RX99999"]
    assert latest.unknown_statuses == ["가동"]
    assert latest.files == ["mes.xlsx", "iqc.xlsx"]


def test_latest_run_returns_most_recent(conn):
    registry.record_run(conn, RunSummary(status="ok", started_at="2026-07-28T09:00:00", mold_count=1))
    registry.record_run(conn, RunSummary(status="error", started_at="2026-07-28T10:00:00", error="MES 없음"))

    latest = registry.latest_run(conn)

    assert latest.status == "error"
    assert latest.error == "MES 없음"


def test_latest_run_none_when_never_run(conn):
    assert registry.latest_run(conn) is None


def test_init_db_adds_columns_to_an_older_database(tmp_path):
    """CREATE TABLE IF NOT EXISTS 는 이미 있는 테이블에 컬럼을 붙여주지 않는다.

    예전 molds.db 를 쓰던 환경에서 record_run 이 'no such column' 으로 터지면
    배치가 통째로 실패하고, 화면에는 원인을 알 수 없는 error 만 남는다."""
    path = str(tmp_path / "molds.db")
    old = db.connect(path)
    old.execute(
        """
        CREATE TABLE ingest_run (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            status       TEXT NOT NULL,
            started_at   TEXT NOT NULL,
            finished_at  TEXT,
            mold_count   INTEGER NOT NULL DEFAULT 0,
            iqc_matched  INTEGER NOT NULL DEFAULT 0,
            orphan_json  TEXT NOT NULL DEFAULT '[]',
            unknown_json TEXT NOT NULL DEFAULT '[]',
            skipped_rows INTEGER NOT NULL DEFAULT 0,
            files_json   TEXT NOT NULL DEFAULT '[]',
            error        TEXT
        )
        """
    )
    old.commit()
    old.close()

    db.init_db(path)

    c = db.connect(path)
    registry.record_run(c, RunSummary(
        status="ok", started_at="s", finished_at="f",
        failed_files=["iqc.xlsx: ValueError: 컬럼 매핑이 없다"],
    ))
    latest = registry.latest_run(c)
    c.close()

    assert latest is not None
    assert latest.failed_files == ["iqc.xlsx: ValueError: 컬럼 매핑이 없다"]
