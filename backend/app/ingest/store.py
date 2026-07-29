"""금형 데이터를 DB 에 쓴다 — 배치마다 트랜잭션 안에서 전체 교체.

upsert 가 아니라 전체 교체인 이유: JIG 관리대장이 마스터이고 매번 전체를 다시
읽으므로, 관리대장에서 사라진 금형은 대시보드에서도 사라져야 한다. upsert 만
하면 유령 금형이 영원히 남는다.

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


def replace_all(
    conn: sqlite3.Connection, records: list[MoldRecord], *, commit: bool = True
) -> None:
    """`commit=False` 면 커밋을 생략하고 호출자에게 트랜잭션을 넘긴다 — 파이프라인이
    이 교체와 `record_files`/이력 삭제를 한 트랜잭션으로 묶어 마지막에 한 번만
    커밋하려 할 때 쓴다. 실패 시 되돌리는 책임은 `commit` 값과 무관하게 항상
    여기(`except` 블록의 `rollback()`)에 있다."""
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

        # 설비 사용구간. 금형 하나가 여러 번 설치될 수 있으므로 install_seq 로
        # 순서를 매긴다. defects_json 에 날짜별 원값을 남기는 이유는 합산
        # 불량율이 어느 날들에서 나왔는지 되짚을 수 있어야 하기 때문이다 —
        # 값 하나만 남기면 "이 수치 어디서 나왔냐"에 답할 수 없다.
        conn.executemany(
            """
            INSERT INTO production_run
                (mold_no, install_seq, line, machine, started_at, ended_at,
                 grind_result, defect_rate, defects_json, source_file, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
            """,
            [
                (
                    r.mold_no, seq, run.line, run.equipment,
                    run.started_at, run.ended_at, run.defect_rate,
                    json.dumps([d.model_dump() for d in run.daily],
                               ensure_ascii=False),
                    run.source_file, now,
                )
                for r in records
                for seq, run in enumerate(r.runs, start=1)
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
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
