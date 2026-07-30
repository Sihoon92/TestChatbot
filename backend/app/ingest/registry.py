"""처리 이력·레이아웃 캐시·배치 요약 접근.

시간은 전부 호출자가 넘긴 값을 쓰거나 여기서 UTC ISO 로 찍는다 — 파일에
기록되는 시각이 로컬/UTC 로 섞이면 이력을 읽을 수 없다.
"""
import json
import sqlite3
from datetime import datetime, timezone

from app.ingest.schemas import FoundFile, RunSummary, SheetLayout


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def known_hashes(conn: sqlite3.Connection) -> dict[str, str]:
    """{파일 경로: 마지막으로 본 내용 해시}."""
    rows = conn.execute("SELECT path, content_hash FROM ingested_file").fetchall()
    return {r["path"]: r["content_hash"] for r in rows}


def record_files(
    conn: sqlite3.Connection, files: list[FoundFile], *, commit: bool = True
) -> None:
    """`commit=False` 면 커밋을 생략한다 — 호출자가 다른 쓰기와 한 트랜잭션으로
    묶어 마지막에 한 번만 커밋하려 할 때 쓴다(파이프라인이 replace_all 과
    함께 묶는다)."""
    conn.executemany(
        """
        INSERT INTO ingested_file (path, kind, content_hash, seen_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            kind = excluded.kind,
            content_hash = excluded.content_hash,
            seen_at = excluded.seen_at
        """,
        [(f.path, f.kind, f.content_hash, _now()) for f in files],
    )
    if commit:
        conn.commit()


def save_layout(
    conn: sqlite3.Connection, kind: str, layout: SheetLayout, source_path: str
) -> None:
    conn.execute(
        """
        INSERT INTO sheet_mapping (kind, sheet_name, layout_json, source_path, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (kind, layout.sheet_name, layout.model_dump_json(), source_path, _now()),
    )
    conn.commit()


def load_layouts(conn: sqlite3.Connection, kind: str) -> list[SheetLayout]:
    """이 단계에서 지금까지 학습한 레이아웃 전부. 최신순.

    시트 이름으로 거르지 않는다. 관리대장은 시트 이름이 곧 금형번호라 시트마다
    이름이 달라, 시트명으로 거르면 양식이 똑같은 시트들이 각각 LLM 발견을
    돈다. 어느 레이아웃이 이 시트에 맞는지는 pick_layout 의 앵커 대조가
    판정한다 — 원래 그러라고 만든 장치다.

    최신순인 이유는 그대로다: 바뀐 양식이 먼저 맞아야 옛 규칙으로 파싱하는
    일이 없다.
    """
    rows = conn.execute(
        """
        SELECT layout_json FROM sheet_mapping
        WHERE kind = ?
        ORDER BY id DESC
        """,
        (kind,),
    ).fetchall()
    return [SheetLayout.model_validate_json(r["layout_json"]) for r in rows]


def record_run(conn: sqlite3.Connection, summary: RunSummary) -> None:
    conn.execute(
        """
        INSERT INTO ingest_run
            (status, started_at, finished_at, mold_count, iqc_matched,
             orphan_json, unknown_json, skipped_rows, files_json, error,
             unreadable_json, failed_json, unknown_status_rows,
             unknown_jig_id_json, unknown_equipment_json, missing_mes_days_json,
             unmatched_runs, open_runs)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            summary.status,
            summary.started_at,
            summary.finished_at,
            summary.mold_count,
            summary.iqc_matched,
            json.dumps(summary.orphan_mold_nos, ensure_ascii=False),
            json.dumps(summary.unknown_statuses, ensure_ascii=False),
            summary.skipped_rows,
            json.dumps(summary.files, ensure_ascii=False),
            summary.error,
            json.dumps(summary.unreadable_files, ensure_ascii=False),
            json.dumps(summary.failed_files, ensure_ascii=False),
            summary.unknown_status_rows,
            json.dumps(summary.unknown_jig_id, ensure_ascii=False),
            json.dumps(summary.unknown_equipment, ensure_ascii=False),
            json.dumps(summary.missing_mes_days, ensure_ascii=False),
            summary.unmatched_runs,
            summary.open_runs,
        ),
    )
    conn.commit()


def latest_run(conn: sqlite3.Connection) -> RunSummary | None:
    row = conn.execute(
        "SELECT * FROM ingest_run ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return RunSummary(
        status=row["status"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        mold_count=row["mold_count"],
        iqc_matched=row["iqc_matched"],
        orphan_mold_nos=json.loads(row["orphan_json"]),
        unknown_statuses=json.loads(row["unknown_json"]),
        unknown_status_rows=row["unknown_status_rows"],
        skipped_rows=row["skipped_rows"],
        files=json.loads(row["files_json"]),
        error=row["error"],
        unreadable_files=json.loads(row["unreadable_json"]),
        failed_files=json.loads(row["failed_json"]),
        unknown_jig_id=json.loads(row["unknown_jig_id_json"]),
        unknown_equipment=json.loads(row["unknown_equipment_json"]),
        missing_mes_days=json.loads(row["missing_mes_days_json"]),
        unmatched_runs=row["unmatched_runs"],
        open_runs=row["open_runs"],
    )
