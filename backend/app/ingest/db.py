"""molds.db 의 스키마와 연결.

채팅용 app.db 와 반드시 다른 파일이다 — app.db 는 LangGraph 체크포인터가
채팅 턴마다 쓰기 때문에, 같은 파일에 수집 배치가 쓰면 SQLite 쓰기 락을 두고
경합해 'database is locked' 가 나거나 양쪽이 느려진다.
"""
import os
import sqlite3

SCHEMA = """
-- 처리 이력. 배치를 다시 돌지 판정한다.
CREATE TABLE IF NOT EXISTS ingested_file (
    path         TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    seen_at      TEXT NOT NULL
);

-- 양식 해석 결과. 옛 행을 지우지 않는다 — 언제 양식이 바뀌었고 그때 어떻게
-- 해석했는가가 추출값을 의심할 때 유일한 근거다.
CREATE TABLE IF NOT EXISTS sheet_mapping (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    sheet_name  TEXT NOT NULL,
    layout_json TEXT NOT NULL,
    source_path TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sheet_mapping_lookup
    ON sheet_mapping (kind, sheet_name, id DESC);

-- 배치 실행 요약. 조용히 빠진 데이터가 없음을 증명하는 자리.
CREATE TABLE IF NOT EXISTS ingest_run (
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
    error        TEXT,
    unreadable_json TEXT NOT NULL DEFAULT '[]',
    failed_json  TEXT NOT NULL DEFAULT '[]',
    unknown_status_rows INTEGER NOT NULL DEFAULT 0
);

-- 아래 네 테이블은 대시보드 스키마와 대응하며, 배치마다 전체 교체된다.
CREATE TABLE IF NOT EXISTS mold (
    mold_no            TEXT PRIMARY KEY,
    status             TEXT NOT NULL,
    line               TEXT,
    machine            TEXT,
    shot_count         INTEGER,
    total_installs     INTEGER,
    total_production   INTEGER,
    latest_defect_rate REAL,
    first_installed_at TEXT,
    installed_at       TEXT,
    source_file        TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mold_design (
    mold_no         TEXT PRIMARY KEY,
    angle_deg       REAL,
    height_mm       REAL,
    step_mm         REAL,
    overall_mm      REAL,
    plate_height_mm REAL,
    plate_width_mm  REAL,
    source_file     TEXT,
    updated_at      TEXT NOT NULL
);

-- 행이 없는 것이 곧 missing 이다. status='missing' 행을 따로 만들지 않는다.
CREATE TABLE IF NOT EXISTS mold_stage (
    mold_no     TEXT NOT NULL,
    stage       TEXT NOT NULL,
    status      TEXT NOT NULL,
    error       TEXT,
    items_json  TEXT NOT NULL DEFAULT '[]',
    source_file TEXT,
    updated_at  TEXT,
    PRIMARY KEY (mold_no, stage)
);

CREATE TABLE IF NOT EXISTS production_run (
    mold_no      TEXT NOT NULL,
    install_seq  INTEGER NOT NULL,
    line         TEXT,
    machine      TEXT,
    started_at   TEXT,
    ended_at     TEXT,
    grind_result TEXT,
    defect_rate  REAL,
    defects_json TEXT NOT NULL DEFAULT '[]',
    source_file  TEXT,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (mold_no, install_seq)
);
"""


# CREATE TABLE IF NOT EXISTS 는 이미 있는 테이블에 새 컬럼을 붙여주지 않는다.
# 그래서 스키마에 컬럼을 추가할 때는 여기에도 같이 적는다 — 안 그러면 예전
# molds.db 를 쓰던 환경에서 record_run 이 "no such column" 으로 터져 배치가
# 통째로 실패한다(그 실패는 화면에 error 로만 뜨고 원인을 알 수 없다).
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "ingest_run": {
        "unreadable_json": "TEXT NOT NULL DEFAULT '[]'",
        "failed_json": "TEXT NOT NULL DEFAULT '[]'",
        "unknown_status_rows": "INTEGER NOT NULL DEFAULT 0",
    },
}


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    for table, columns in _ADDED_COLUMNS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns.items():
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(path: str) -> None:
    """스키마를 만든다(이미 있으면 그대로 둔다)."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        _add_missing_columns(conn)
        conn.commit()
    finally:
        conn.close()
