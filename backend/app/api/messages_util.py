from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.messages import AIMessage, HumanMessage

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph


def serialize_messages(messages: list) -> list[dict]:
    out = []
    for m in messages:
        if isinstance(m, HumanMessage):
            role = "user"
        elif isinstance(m, AIMessage):
            role = "assistant"
        else:
            role = "system"
        content = m.content if isinstance(m.content, str) else str(m.content)
        out.append({"role": role, "content": content})
    return out


async def load_history(graph: "CompiledStateGraph", session_id: str) -> list[dict]:
    # 체크포인터에서 세션(thread)의 현재 상태 스냅샷을 읽어 과거 메시지를 복원한다.
    cfg = {"configurable": {"thread_id": session_id}}
    snap = await graph.aget_state(cfg)
    messages = (snap.values or {}).get("messages", []) if snap else []
    return serialize_messages(messages)
