import asyncio
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage

from app.api.sse import format_sse
from app.constants import DEFAULT_SESSION_TITLE
from app.db import sessions_repo as repo
from app.main import get_app_state
from app.observability import DebugCallbackHandler
from app.schemas import ChatBody
from app.state import AppState

router = APIRouter()


def _derive_title(text: str) -> str:
    text = text.strip().replace("\n", " ")
    return (text[:40] + "…") if len(text) > 40 else (text or DEFAULT_SESSION_TITLE)


def build_run_config(session_id: str, settings: Any) -> dict:
    """요청 단위 실행 config 를 만든다.

    thread_id=session_id 로 체크포인터가 과거 대화를 자동 로드/저장한다.
    디버그 로깅이 켜져 있으면 요청 단위로 콜백을 부착한다(그래프 컴파일 시 고정
    장착하지 않음 — 요청별 on/off 등 유연성 확보). LangChain 표준 패턴.
    """
    cfg: dict = {"configurable": {"thread_id": session_id}}
    if settings.debug_log_enabled:
        cfg["callbacks"] = [DebugCallbackHandler(settings)]
        if getattr(settings, "debug_log_verbose", False):
            print(f"[debug-log:trace] 콜백 부착 session={session_id}", flush=True)
    return cfg


async def stream_graph(
    graph: Any, inputs: Any, cfg: dict, session_id: str
) -> AsyncIterator[str]:
    """그래프를 실행하고 스트림을 SSE 프레임으로 변환한다.

    프레임:
    - ``token`` : assistant 텍스트 조각 (chat 노드로 한정)
    - ``done``  : 정상 종료
    - ``error`` : 스트리밍 중 오류

    그래프 실행은 **별도 태스크**로 돌리고, 이 제너레이터는 큐에서 꺼내 클라이언트로
    보내기만 한다. 이렇게 분리하지 않으면 그래프 진행이 "클라이언트가 스트림을 끝까지
    소비하는지"에 묶여, 스트리밍 종료 타이밍(특히 OpenAI 호환 백엔드)에 따라 마지막
    단계인 ``on_llm_end``(=디버그 로그 기록)와 체크포인터 저장이 실행되지 않을 수 있다.
    별도 태스크는 이벤트 루프에서 끝까지 돌므로, 클라이언트가 중간에 끊겨도 로그/메모리
    저장이 항상 완료된다.
    """
    queue: asyncio.Queue = asyncio.Queue()

    async def _drive() -> None:
        try:
            async for chunk, meta in graph.astream(inputs, cfg, stream_mode="messages"):
                if meta.get("langgraph_node") != "chat":
                    continue
                text = getattr(chunk, "content", "")
                if text:
                    await queue.put(("token", {"delta": text}))
            await queue.put(("done", {"session_id": session_id}))
        except Exception as exc:  # noqa: BLE001 - report streaming failures to the client
            await queue.put(("error", {"message": str(exc)}))
        finally:
            await queue.put(None)  # 종료 sentinel

    task = asyncio.create_task(_drive())
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            event, data = item
            yield format_sse(event, data)
    finally:
        # 클라이언트가 끊겨 이 제너레이터가 닫혀도 _drive 태스크는 독립적으로 끝까지
        # 실행된다(취소하지 않는다). 예외가 조용히 사라지지 않도록 콜백만 달아둔다.
        if not task.done():
            task.add_done_callback(
                lambda t: t.exception() if not t.cancelled() else None
            )


@router.post("/sessions/{session_id}/chat")
async def chat(session_id: str, body: ChatBody, state: AppState = Depends(get_app_state)):
    session = await repo.get_session(state.db_path, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")

    # 첫 메시지면 제목을 유도(첫 40자), 아니면 updated_at 만 갱신해 목록 상단으로.
    if session["title"] == DEFAULT_SESSION_TITLE:
        await repo.rename_session(state.db_path, session_id, _derive_title(body.content))
    else:
        await repo.touch_session(state.db_path, session_id)

    cfg = build_run_config(session_id, state.settings)
    inputs = {
        "messages": [HumanMessage(content=body.content)],
        "session_id": session_id,
    }
    return StreamingResponse(
        stream_graph(state.graph, inputs, cfg, session_id),
        media_type="text/event-stream",
    )
