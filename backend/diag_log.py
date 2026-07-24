"""디버그 로그가 안 남는 원인 진단 스크립트 (일회용).

앱의 실제 채팅 경로(그래프 + astream(stream_mode="messages"))를 그대로 재현하면서,
콜백이 어디까지 발화하고 파일이 실제로 생기는지 콘솔에 출력한다. 프론트/서버 콘솔
가시성 문제와 무관하게, 이 스크립트를 직접 실행하면 결과가 바로 보인다.

사용법 (backend/ 에서, venv 파이썬으로):
    python diag_log.py
"""
import asyncio
import os
import traceback

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from app.config import get_settings
from app.graph.builder import build_graph
from app.llm import get_chat_model
from app.observability import DebugCallbackHandler


async def main() -> None:
    # 실제 .env 설정을 쓰되, 추적 출력만 강제로 켠다.
    settings = get_settings().model_copy(update={"debug_log_verbose": True})
    log_path = settings.resolved_debug_log_path

    print("=" * 60)
    print("llm_backend        =", settings.llm_backend)
    print("debug_log_enabled  =", settings.debug_log_enabled)
    print("resolved log path  =", log_path)
    print("파일 존재 (실행 전) =", os.path.exists(log_path))
    print("=" * 60)

    try:
        model = get_chat_model(settings)
    except Exception:
        print("!!! 모델 생성 실패 (get_chat_model):")
        traceback.print_exc()
        return

    graph = build_graph(model, MemorySaver())
    cb = DebugCallbackHandler(settings)
    cfg = {"configurable": {"thread_id": "diag"}, "callbacks": [cb]}
    inputs = {"messages": [HumanMessage(content="한 단어로만 답해줘: 안녕?")], "session_id": "diag"}

    print(">>> astream 시작 (앱과 동일한 stream_mode='messages')")
    got_token = False
    try:
        async for chunk, meta in graph.astream(inputs, cfg, stream_mode="messages"):
            if getattr(chunk, "content", ""):
                got_token = True
    except Exception:
        print("!!! astream 중 예외 발생:")
        traceback.print_exc()
    print(f">>> astream 종료 (토큰 수신: {got_token})")

    print("=" * 60)
    exists = os.path.exists(log_path)
    print("파일 존재 (실행 후) =", exists)
    if exists:
        print("파일 크기 =", os.path.getsize(log_path), "bytes")
    print("=" * 60)
    if exists:
        print("[결과] 로깅 정상 — 파일이 생성됨. 문제는 스크립트 밖(서버 실행 방식/경로)일 수 있음.")
    else:
        print("[결과] 파일이 안 생김 — 위 추적 로그에서 어느 콜백까지 떴는지 확인하세요.")


if __name__ == "__main__":
    asyncio.run(main())
