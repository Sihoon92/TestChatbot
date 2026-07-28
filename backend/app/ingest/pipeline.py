"""배치 하나를 실행한다 — 이 모듈이 유일한 조립 지점이다.

여기서만 파일·COM·LLM·DB 가 함께 등장한다. 나머지 모듈은 각자 한 가지만
알고 있어서 순수 함수로 테스트된다.

처리 순서에 의존성이 있다: MES 를 먼저 읽어야 금형 목록이 생긴다. MES 가
없거나 실패하면 배치를 중단하고 이전 DB 상태를 그대로 둔다 — 부분 갱신은
옛 금형과 새 부속정보가 섞인, 어느 쪽도 믿을 수 없는 상태를 만든다.
"""
from datetime import datetime, timezone
from pathlib import Path

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


# 1단계는 MES + IQC 만 읽는다. 나머지 폴더의 파일을 found 에 남기면 이력에는
# "읽었다"고 기록되면서 데이터는 안 들어와, 화면이 성공으로 보인다. 단계를
# 늘릴 때 여기와 FIELD_GUIDE 를 함께 늘린다.
_PROCESSED_KINDS = ("mes", "iqc")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _processed_dirs(stage_dirs: dict[str, str]) -> set[str]:
    """이번 단계가 처리하는 종류로 매핑된 폴더 이름들.

    unreadable 은 경로 문자열이라 kind 가 없다(해시를 못 떠서 FoundFile 을
    만들 수 없다). 스캐너가 root/폴더명/파일 로만 훑으므로 부모 폴더 이름이
    곧 매핑 키다 — 그것으로 종류를 되짚는다.
    """
    return {d for d, kind in stage_dirs.items() if kind in _PROCESSED_KINDS}


def _read_file(conn, model, found: FoundFile, open_wb, config) -> list[Row]:
    """파일 하나를 열어 행을 뽑는다. Excel 기동은 여기서 한 번뿐이다."""
    rows: list[Row] = []
    with open_wb(found.path) as wb:
        for sheet_name in wb.sheet_names():
            grid, top_left = wb.used_values(sheet_name)
            if not grid:
                continue
            cached = pick_layout(
                grid, top_left, registry.load_layouts(conn, found.kind, sheet_name)
            )
            layout = cached
            if layout is None:
                layout = discover_layout(
                    model, wb, found.kind, sheet_name, config=config
                )
            try:
                parsed = parse_rows(grid, top_left, layout, found.path)
            except Exception:  # noqa: BLE001
                # 캐시된 레이아웃으로 실패했다면 양식이 앵커 밖에서 바뀐 것이다
                # (헤더 텍스트는 그대로인데 컬럼 구성만 달라진 경우 등).
                # 한 번만 다시 해석해본다 — 이게 없으면 나쁜 캐시에 걸린 파일이
                # 영구히 실패하고 UI 로 빠져나올 방법이 없다.
                if cached is None:
                    raise
                layout = discover_layout(
                    model, wb, found.kind, sheet_name, config=config
                )
                parsed = parse_rows(grid, top_left, layout, found.path)
            if layout is not cached:
                # 파싱에 성공한 레이아웃만 캐시에 남긴다. 저장을 파싱보다 앞에
                # 두면 파싱을 못 하는 레이아웃이 캐시에 박혀 영구 실패한다
                # (save_layout 은 즉시 커밋하므로 배치 롤백으로도 못 지운다).
                # load_layouts 가 최신순이므로 새 레이아웃이 옛것보다 먼저 뽑힌다.
                registry.save_layout(conn, found.kind, layout, found.path)
            rows.extend(parsed)
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
        stage_dirs = settings.stage_dir_map
        scan_result = scan(settings.resolved_ingest_root, stage_dirs)
        # 이번 단계가 읽지 않는 종류는 아예 없는 셈 친다. 이력에 남기면
        # "읽었다"고 기록되면서 데이터는 안 들어와 화면이 성공으로 보인다.
        # 이렇게 걸러내면 설정에서 폴더 매핑을 뺀 파일도 found/unreadable
        # 어디에도 없어 자연히 removed 로 정리된다.
        found = [f for f in scan_result.files if f.kind in _PROCESSED_KINDS]
        processed_dirs = _processed_dirs(stage_dirs)
        unreadable = [
            p for p in scan_result.unreadable
            if Path(p).parent.name in processed_dirs
        ]

        known = registry.known_hashes(conn)
        found_paths = {f.path for f in found}

        # 스캐너가 직접 돌려준 목록이라 "이번에 못 읽은 파일"과 "삭제된 파일"이
        # 섞이지 않는다. 못 읽은 파일이 하나라도 있으면 배치 자체를 건너뛰고
        # 이력도 건드리지 않는다 — 배치는 DB 를 전체 교체하므로, 그냥 진행하면
        # 그 파일의 데이터가 화면에서 조용히 사라진다(다음 회차에 다시 시도한다).
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

        # MES 실패는 배치를 중단시킨다(마스터가 없으면 금형 목록을 만들 수
        # 없다). IQC 는 파일 단위로 격리한다 — 한 장이 깨졌다고 금형 목록까지
        # 버리면 MES 는 멀쩡히 읽혔는데 화면이 통째로 옛 상태로 남는다.
        mes_rows: list[Row] = []
        iqc_rows: list[Row] = []
        failed: list[str] = []
        for f in mes_files:
            mes_rows.extend(_read_file(conn, model, f, open_wb, config))
        for f in [x for x in found if x.kind == "iqc"]:
            try:
                iqc_rows.extend(_read_file(conn, model, f, open_wb, config))
            except Exception as exc:  # noqa: BLE001
                # 부속 정보 하나가 없다고 금형 목록을 버리지 않는다. 다만
                # 조용히 넘기지도 않는다 — 그 파일의 IQC 항목이 화면에서
                # 사라지는데 사유가 없으면 아무도 원인을 못 찾는다.
                failed.append(f"{f.path}: {type(exc).__name__}: {exc}")

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
            unknown_status_rows=result.unknown_status_rows,
            skipped_rows=result.skipped_rows,
            files=[f.path for f in found],
            failed_files=failed,
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
