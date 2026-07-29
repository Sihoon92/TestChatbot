"""금형 조회 — molds.db 를 읽는다.

시그니처는 샘플 데이터를 쓰던 때와 같다. 라우터도 프론트도 이 함수들만
알고 있으므로, 저장소가 바뀌어도 그 바깥은 손대지 않는다.

`sample_data.SAMPLE_MOLDS` 는 삭제하지 않고 테스트 픽스처 겸 시드로 남긴다.
"""
import json
import sqlite3

from app.config import get_settings
from app.ingest import db
from app.ingest.join import covered_dates
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
    ProductionRun,
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

    # install 단계는 mold_stage 에 행을 만들지 않는다 — 그 데이터는
    # production_run 에 있다. 그것을 안 보면 표에 구간이 가득한데도 탭에는
    # '·'(없음) 배지가 붙어, 화면이 스스로와 모순된다.
    if "install" not in present and conn.execute(
        "SELECT 1 FROM production_run WHERE mold_no = ? LIMIT 1", (mold_no,)
    ).fetchone():
        present["install"] = "ok"

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


def _production_runs(conn: sqlite3.Connection, mold_no: str) -> list[ProductionRun]:
    """설비 사용구간 = 화면의 '기간별 불량율' 표 한 행씩.

    defects_json 에는 수집이 남긴 날짜별 원값([{date, produced, defects}])이
    들어 있다. 합계와 '며칠치가 반영됐는지'를 여기서 만들어 준다 — 화면이
    직접 JSON 을 헤집게 하면 같은 계산이 두 곳에 생긴다.
    """
    rows = conn.execute(
        "SELECT * FROM production_run WHERE mold_no = ? ORDER BY install_seq",
        (mold_no,),
    ).fetchall()

    runs: list[ProductionRun] = []
    for r in rows:
        daily = json.loads(r["defects_json"])
        runs.append(
            ProductionRun(
                install_seq=r["install_seq"],
                line=r["line"],
                machine=r["machine"],
                started_at=r["started_at"],
                ended_at=r["ended_at"],
                grind_result=r["grind_result"],
                defect_rate=r["defect_rate"],
                produced=sum(d["produced"] for d in daily) if daily else None,
                defect_count=sum(d["defects"] for d in daily) if daily else None,
                days_covered=len(daily),
                # 24시간 올림 규칙을 여기 다시 구현하지 않는다 — 수집과 조회가
                # 각자 계산하면 언젠가 조용히 어긋나고, 그때 화면의 '3/4일' 이
                # 무엇을 뜻하는지 아무도 확신할 수 없게 된다.
                days_expected=len(
                    covered_dates(r["started_at"], r["ended_at"])
                ) if r["started_at"] else 0,
            )
        )
    return runs


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
            productions=_production_runs(conn, mold_no),
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
