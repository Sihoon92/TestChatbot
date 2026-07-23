from fastapi import APIRouter, Depends

from app.main import get_app_state
from app.services.llm_health import check_llm
from app.state import AppState

router = APIRouter()


@router.get("/health/llm")
async def health_llm(state: AppState = Depends(get_app_state)):
    # 활성 백엔드(ollama|internal)로 디스패치해 연결/모델 목록을 확인한다.
    return await check_llm(state.settings)
