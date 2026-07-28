"""금형 데이터를 DB 에 쓴다 — 배치마다 트랜잭션 안에서 전체 교체.

upsert 가 아니라 전체 교체인 이유: MES 가 마스터이고 매번 전체를 다시 읽으므로,
MES 에서 사라진 금형은 대시보드에서도 사라져야 한다. upsert 만 하면 유령 금형이
영원히 남는다.

실패하면 롤백되어 이전 상태가 그대로 유지된다. 반쯤 교체된 상태 — 옛 금형과
새 부속정보가 섞인 DB — 는 어느 쪽도 믿을 수 없어 가장 나쁘다.
"""
import json
import sqlite3
from datetime import datetime, timezone

from app.ingest.schemas import MoldRecord

_TABLES = ("mold", "mold_design", "mold_stage", "production_run")


def _items_json(record: MoldRecord) -> str:
    return json.dumps(
        [
            {
                "label": i.label,
                "value": i.value,
                "judgment": None,
                "source": {
                    "file": i.source_file,
                    "sheet": i.source_sheet,
                    "cell": i.source_cell,
                },
            }
            for i in record.iqc_items
        ],
        ensure_ascii=False,
    )


def replace_all(conn: sqlite3.Connection, records: list[MoldRecord]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    try:
        # 명시적 트랜잭션. sqlite3 는 DDL/DML 자동커밋 규칙이 미묘해서
        # BEGIN 을 직접 열어 실패 시 확실히 되돌린다.
        conn.execute("BEGIN")
        for table in _TABLES:
            conn.execute(f"DELETE FROM {table}")

        conn.executemany(
            """
            INSERT INTO mold
                (mold_no, status, line, machine, shot_count, total_installs,
                 total_production, latest_defect_rate, first_installed_at,
                 installed_at, source_file, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
            """,
            [
                (
                    r.mold_no, r.status, r.line, r.machine, r.shot_count,
                    r.total_installs, r.total_production, r.latest_defect_rate,
                    r.source_file, now,
                )
                for r in records
            ],
        )

        # IQC 항목이 있는 금형만 stage 행을 만든다. 행이 없는 것이 곧
        # missing 이므로 status='missing' 행을 따로 넣지 않는다.
        staged = [r for r in records if r.iqc_items]
        conn.executemany(
            """
            INSERT INTO mold_stage
                (mold_no, stage, status, error, items_json, source_file, updated_at)
            VALUES (?, 'iqc', 'ok', NULL, ?, ?, ?)
            """,
            [(r.mold_no, _items_json(r), r.iqc_source_file, now) for r in staged],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
