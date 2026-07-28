"""금형 조회 — molds.db 를 읽는다.

시그니처는 샘플 데이터를 쓰던 때와 같다. 라우터도 프론트도 이 함수들만
알고 있으므로, 저장소가 바뀌어도 그 바깥은 손대지 않는다.

`sample_data.SAMPLE_MOLDS` 는 삭제하지 않고 테스트 픽스처 겸 시드로 남긴다.
"""
import json
import sqlite3

from app.config import get_settings
from app.ingest import db
from app.molds.schemas import (
    ALL_STAGES,
    ALL_STATUSES,
    CumulativeHistory,
    CurrentState,
    DesignSpec,
    FilterOptions,
    Installation,
    MoldDetail,
    MoldStatus,
    MoldSummary,
    SourceRef,
    StageItem,
    StagePanel,
)


def _conn() -> sqlite3.Connection:
    path = get_settings().resolved_molds_db_path
    db.init_db(path)  # 첫 실행에 파일이 없어도 빈 결과를 돌려주기 위해
    return db.connect(path)


def _summary(row: sqlite3.Row, stage_status: dict) -> MoldSummary:
    return MoldSummary(
        mold_no=row["mold_no"],
        status=row["status"],
        line=row["line"],
        machine=row["machine"],
        shot_count=row["shot_count"],
        latest_defect_rate=row["latest_defect_rate"],
        total_production=row["total_production"],
        stage_status=stage_status,
    )


def _stage_status_map(conn: sqlite3.Connection, mold_no: str) -> dict:
    """5단계를 전부 채운다 — 행이 없는 것이 곧 missing 이다.

    DB 에 status='missing' 행을 만들지 않는 이유: "아직 그 단계 파일이 안
    올라왔다" 와 "올라왔는데 비어 있다" 를 억지로 구분해 넣게 된다.
    """
    rows = conn.execute(
        "SELECT stage, status FROM mold_stage WHERE mold_no = ?", (mold_no,)
    ).fetchall()
    present = {r["stage"]: r["status"] for r in rows}
    return {stage: present.get(stage, "missing") for stage in ALL_STAGES}


def list_molds(
    *,
    status: MoldStatus | None = None,
    line: str | None = None,
    machine: str | None = None,
    q: str | None = None,
) -> list[MoldSummary]:
    """조건에 맞는 금형 요약 목록. 인자가 None 이면 그 조건으로 거르지 않는다."""
    sql = "SELECT * FROM mold WHERE 1=1"
    params: list = []
    if status is not None:
        sql += " AND status = ?"
        params.append(status)
    if line is not None:
        sql += " AND line = ?"
        params.append(line)
    if machine is not None:
        sql += " AND machine = ?"
        params.append(machine)
    if q is not None:
        # 사용자가 붙여넣기로 공백을 흘리는 일이 흔해 앞뒤 공백을 뗀다.
        needle = q.strip()
        if needle:
            sql += " AND mold_no LIKE ?"
            params.append(f"%{needle.upper()}%")
    sql += " ORDER BY mold_no"

    conn = _conn()
    try:
        rows = conn.execute(sql, params).fetchall()
        return [_summary(r, _stage_status_map(conn, r["mold_no"])) for r in rows]
    finally:
        conn.close()


def _stage_panels(conn: sqlite3.Connection, mold_no: str) -> list[StagePanel]:
    """유연 단계(iqc/pqc/ai_recheck)만 담는다 — design/install 은 전용 필드가 있다."""
    rows = conn.execute(
        """
        SELECT stage, status, error, items_json, source_file, updated_at
        FROM mold_stage WHERE mold_no = ? AND stage IN ('iqc','pqc','ai_recheck')
        """,
        (mold_no,),
    ).fetchall()
    panels: list[StagePanel] = []
    for r in rows:
        items = []
        for raw in json.loads(r["items_json"]):
            src = raw.get("source") or {}
            items.append(
                StageItem(
                    label=raw["label"],
                    value=raw["value"],
                    judgment=raw.get("judgment"),
                    source=SourceRef(**src) if src.get("file") else None,
                )
            )
        panels.append(
            StagePanel(
                stage=r["stage"],
                status=r["status"],
                updated_at=r["updated_at"],
                error=r["error"],
                items=items,
            )
        )
    return panels


def get_mold(mold_no: str) -> MoldDetail | None:
    """금형 번호로 상세를 찾는다. 없으면 None(라우터가 404 로 바꾼다)."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM mold WHERE mold_no = ?", (mold_no,)
        ).fetchone()
        if row is None:
            return None

        design_row = conn.execute(
            "SELECT * FROM mold_design WHERE mold_no = ?", (mold_no,)
        ).fetchone()
        design = (
            DesignSpec(
                angle_deg=design_row["angle_deg"],
                height_mm=design_row["height_mm"],
                step_mm=design_row["step_mm"],
                overall_mm=design_row["overall_mm"],
                plate_height_mm=design_row["plate_height_mm"],
                plate_width_mm=design_row["plate_width_mm"],
            )
            if design_row
            else DesignSpec()
        )

        return MoldDetail(
            summary=_summary(row, _stage_status_map(conn, mold_no)),
            design=design,
            history=CumulativeHistory(
                total_installs=row["total_installs"],
                total_production=row["total_production"],
                first_installed_at=row["first_installed_at"],
            ),
            current=CurrentState(
                status=row["status"],
                line=row["line"],
                machine=row["machine"],
                shot_count=row["shot_count"],
                installed_at=row["installed_at"],
            ),
            productions=[],  # 2단계에서 production_run 을 채운다
            stages=_stage_panels(conn, mold_no),
        )
    finally:
        conn.close()


def filter_options() -> FilterOptions:
    """필터 드롭다운이 쓸 선택지.

    - statuses: DB 에서 뽑지 않고 고정 4종. 상태는 도메인 어휘이지 데이터의
      산물이 아니다 — '폐기' 금형이 지금 없다고 '폐기'로 조회할 수단이
      사라지면 안 된다(결과 0건과 조회 불가는 다르다).
    - installations: 실제 존재하는 (라인, 호기) 쌍만. 독립 리스트로 주면 UI 가
      존재하지 않는 조합을 만들어낼 수 있다.
    """
    conn = _conn()
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT line, machine FROM mold
            WHERE line IS NOT NULL AND machine IS NOT NULL
            ORDER BY line, machine
            """
        ).fetchall()
        return FilterOptions(
            statuses=list(ALL_STATUSES),
            installations=[
                Installation(line=r["line"], machine=r["machine"]) for r in rows
            ],
        )
    finally:
        conn.close()
