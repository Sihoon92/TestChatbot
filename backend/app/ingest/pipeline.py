"""배치 하나를 실행한다 — 이 모듈이 유일한 조립 지점이다.

여기서만 파일·COM·LLM·DB 가 함께 등장한다. 나머지 모듈은 각자 한 가지만
알고 있어서 순수 함수로 테스트된다.

처리 순서에 의존성이 있다: MES 를 먼저 읽어야 금형 목록이 생긴다. MES 가
없거나 실패하면 배치를 중단하고 이전 DB 상태를 그대로 둔다 — 부분 갱신은
옛 금형과 새 부속정보가 섞인, 어느 쪽도 믿을 수 없는 상태를 만든다.
"""
import os
from datetime import datetime, timezone

from app.config import Settings
from app.excel.workbook import open_workbook
from app.ingest import db, registry
from app.ingest.assemble import assemble
from app.ingest.discover import discover_layout
from app.ingest.layout import pick_layout
from app.ingest.parser import parse_rows
from app.ingest.scanner import scan
from app.ingest.schemas import FoundFile, Row, RunSummary
from app.ingest.store import replace_all


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_file(conn, model, found: FoundFile, open_wb, config) -> list[Row]:
    """파일 하나를 열어 행을 뽑는다. Excel 기동은 여기서 한 번뿐이다."""
    rows: list[Row] = []
    with open_wb(found.path) as wb:
        for sheet_name in wb.sheet_names():
            grid, top_left = wb.used_values(sheet_name)
            if not grid:
                continue
            layout = pick_layout(
                grid, top_left, registry.load_layouts(conn, found.kind, sheet_name)
            )
            if layout is None:
                layout = discover_layout(
                    model, wb, found.kind, sheet_name, config=config
                )
                registry.save_layout(conn, found.kind, layout, found.path)
            rows.extend(parse_rows(grid, top_left, layout, found.path))
    return rows


def run_ingest(
    settings: Settings,
    model=None,
    *,
    open_wb=None,
    config: dict | None = None,
) -> RunSummary:
    """업로드 폴더를 읽어 molds.db 를 갱신한다.

    `open_wb` 는 테스트가 open_workbook 을 대체하기 위한 주입점이다 — 실제
    Excel 없이 조립 전체를 검증할 수 있어야 한다.
    """
    open_wb = open_wb or open_workbook
    started = _now()
    db_path = settings.resolved_molds_db_path
    db.init_db(db_path)
    conn = db.connect(db_path)

    try:
        found = scan(settings.resolved_ingest_root, settings.stage_dir_map)
        known = registry.known_hashes(conn)
        found_paths = {f.path for f in found}

        # 스캐너가 건너뛴(읽지 못한) 파일과 진짜 삭제된 파일을 구분한다. 파일이
        # 여전히 디스크에 있다면 삭제된 게 아니라 이번 회차에 못 읽은 것이다.
        # 이 경우 그냥 진행하면 배치가 DB 를 전체 교체하므로 그 파일의 데이터가
        # 화면에서 조용히 사라진다 — 그래서 배치 자체를 건너뛰고 이력도 건드리지
        # 않는다(다음 회차에 다시 시도한다).
        unreadable = sorted(
            p for p in known if p not in found_paths and os.path.exists(p)
        )
        if unreadable:
            summary = RunSummary(
                status="skipped", started_at=started, finished_at=_now(),
                unreadable_files=unreadable,
                files=[f.path for f in found],
            )
            registry.record_run(conn, summary)
            return summary

        # 어느 파일이든 바뀌면 배치 전체를 다시 돈다. 증분 로직은 "부분 갱신
        # 때문에 옛 데이터가 남는" 버그를 만드는데, 수천 행 규모에서 그 위험을
        # 살 만한 이득이 없다.
        changed = [f for f in found if known.get(f.path) != f.content_hash]
        removed = set(known) - found_paths
        if not changed and not removed:
            summary = RunSummary(
                status="skipped", started_at=started, finished_at=_now(),
                files=[f.path for f in found],
            )
            registry.record_run(conn, summary)
            return summary

        mes_files = [f for f in found if f.kind == "mes"]
        if not mes_files:
            summary = RunSummary(
                status="error", started_at=started, finished_at=_now(),
                error="MES 파일이 없다. 금형 목록의 유일한 출처이므로 배치를 중단한다.",
                files=[f.path for f in found],
            )
            registry.record_run(conn, summary)
            return summary

        mes_rows: list[Row] = []
        iqc_rows: list[Row] = []
        for f in mes_files:
            mes_rows.extend(_read_file(conn, model, f, open_wb, config))
        for f in [x for x in found if x.kind == "iqc"]:
            iqc_rows.extend(_read_file(conn, model, f, open_wb, config))

        result = assemble(mes_rows, iqc_rows)
        # mold 전체 교체 + 파일 이력 갱신 + 삭제 이력 제거를 한 트랜잭션으로
        # 묶는다. 각자 따로 커밋하면 예를 들어 replace_all 이 커밋된 뒤
        # record_files 가 실패했을 때 mold 는 새 상태, ingested_file 은 옛
        # 상태로 갈라져 다음 회차에 전부 재처리된다.
        replace_all(conn, result.records, commit=False)
        registry.record_files(conn, found, commit=False)
        # 사라진 파일의 이력은 지운다 — 남겨두면 "바뀐 게 없다" 판정이
        # 영원히 틀린다(removed 가 매번 참이 된다).
        for path in removed:
            conn.execute("DELETE FROM ingested_file WHERE path = ?", (path,))
        conn.commit()

        summary = RunSummary(
            status="ok", started_at=started, finished_at=_now(),
            mold_count=len(result.records),
            iqc_matched=result.iqc_matched,
            orphan_mold_nos=result.orphan_mold_nos,
            unknown_statuses=result.unknown_statuses,
            skipped_rows=result.skipped_rows,
            files=[f.path for f in found],
        )
        registry.record_run(conn, summary)
        return summary

    except Exception as exc:  # noqa: BLE001
        # 미완료 트랜잭션을 먼저 되돌린다. 이걸 빼면 아래 record_run 의
        # commit() 이 실패 도중의 부분 변경(예: replace_all 커밋 후
        # record_files 실패)까지 함께 확정해, 화면은 error 인데 DB 는 반쯤
        # 갱신된 상태로 남는다. store 의 "실패하면 롤백되어 이전 상태가
        # 유지된다"는 전제는 이 바깥 트랜잭션에도 그대로 적용돼야 한다.
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        summary = RunSummary(
            status="error", started_at=started, finished_at=_now(),
            error=f"{type(exc).__name__}: {exc}",
        )
        registry.record_run(conn, summary)
        return summary
    finally:
        conn.close()
