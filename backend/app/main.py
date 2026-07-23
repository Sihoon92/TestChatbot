import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.config import get_settings
from app.db.database import init_db
from app.graph.builder import build_graph
from app.llm import get_chat_model
from app.net import apply_proxy_bypass
from app.state import AppState


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # 사내 프록시 우회: HTTP 클라이언트(LLM/health)가 만들어지기 전에 처리해야 한다.
    cleared = apply_proxy_bypass(settings)
    if cleared:
        print(f"[proxy] bypass_proxy=on → cleared {', '.join(cleared)}")
    await init_db(settings.app_db_path)

    # LangGraph 의 SQLite 체크포인터가 세션별(=thread_id) 대화 메모리를 영속화한다.
    cm = AsyncSqliteSaver.from_conn_string(settings.app_db_path)
    async with cm as checkpointer:
        model = get_chat_model(settings)
        graph = build_graph(model, checkpointer)
        app.state.app_state = AppState(
            graph=graph,
            db_path=settings.app_db_path,
            settings=settings,
        )
        yield


def get_app_state(request: Request) -> AppState:
    return request.app.state.app_state


def create_app() -> FastAPI:
    settings = get_settings()
    # 진단 로그가 콘솔에 보이도록 보장한다. uvicorn 등이 이미 루트 핸들러를 설치했다면
    # 건드리지 않아 중복 출력을 피한다.
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
    app = FastAPI(title="Chat Backend", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/")
    async def root():
        return {"status": "ok"}

    from app.api import chat, health, sessions
    app.include_router(health.router, prefix="/api")
    app.include_router(sessions.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")

    return app


app = create_app()
