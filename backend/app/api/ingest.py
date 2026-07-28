"""금형 데이터 수집 API.

`Depends(get_app_state)` 를 쓰지 않는다 — 수집은 채팅 그래프와 무관하고,
app_state 에 의존하면 lifespan 없이 테스트할 수 없다.

수집은 Excel COM 을 쓰므로 이벤트 루프에서 직접 돌리면 안 된다. 파일당 수 초가
걸려 그동안 서버 전체가 멈춘다. asyncio.to_thread 로 워커 스레드에 넘긴다 —
open_workbook 이 COM 을 자기 전용 스레드에 고정하므로 어느 스레드에서 불러도
안전하다.
"""
import asyncio

from fastapi import APIRouter

from app.config import get_settings
from app.ingest import db, registry
from app.ingest.pipeline import run_ingest
from app.ingest.schemas import RunSummary
from app.llm import get_chat_model
from app.observability import debug_run_config

router = APIRouter()


@router.post("/ingest/run", response_model=RunSummary)
async def trigger_ingest() -> RunSummary:
    settings = get_settings()
    model = get_chat_model(settings)
    # 에이전트가 도는 전 과정(도구 호출 순서·판단)을 파일 로그로 남긴다.
    # 프롬프트를 다듬을 때 이게 유일한 근거다.
    config = debug_run_config("ingest", settings)
    return await asyncio.to_thread(run_ingest, settings, model, config=config)


@router.get("/ingest/status", response_model=RunSummary | None)
async def ingest_status() -> RunSummary | None:
    settings = get_settings()
    path = settings.resolved_molds_db_path
    db.init_db(path)
    conn = db.connect(path)
    try:
        return registry.latest_run(conn)
    finally:
        conn.close()
