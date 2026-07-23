from fastapi import APIRouter, Depends, HTTPException, Response

from app.api.messages_util import load_history
from app.constants import DEFAULT_SESSION_TITLE
from app.db import sessions_repo as repo
from app.main import get_app_state
from app.schemas import CreateSessionBody, RenameBody
from app.state import AppState

router = APIRouter()


@router.post("/sessions", status_code=201)
async def create_session(body: CreateSessionBody, state: AppState = Depends(get_app_state)):
    title = body.title or DEFAULT_SESSION_TITLE
    return await repo.create_session(state.db_path, title=title)


@router.get("/sessions")
async def list_sessions(state: AppState = Depends(get_app_state)):
    return await repo.list_sessions(state.db_path)


@router.patch("/sessions/{session_id}")
async def rename_session(session_id: str, body: RenameBody, state: AppState = Depends(get_app_state)):
    updated = await repo.rename_session(state.db_path, session_id, body.title)
    if updated is None:
        raise HTTPException(status_code=404, detail="session not found")
    return updated


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str, state: AppState = Depends(get_app_state)):
    deleted = await repo.delete_session(state.db_path, session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="session not found")
    # 세션의 LangGraph 체크포인트 스레드도 삭제해 고아 상태가 남지 않게 한다.
    # 아직 메시지가 없어 checkpoints 테이블이 없을 수 있으므로 방어적으로 감싼다.
    try:
        await state.graph.checkpointer.adelete_thread(session_id)
    except Exception:  # noqa: BLE001
        pass
    return Response(status_code=204)


@router.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str, state: AppState = Depends(get_app_state)):
    session = await repo.get_session(state.db_path, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return {"messages": await load_history(state.graph, session_id)}
