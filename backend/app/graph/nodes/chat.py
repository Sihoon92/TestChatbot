from typing import Awaitable, Callable

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage

from app.graph.state import GraphState
from app.prompts import CHAT_SYSTEM_PROMPT

ChatNode = Callable[[GraphState], Awaitable[dict]]


def make_chat_node(model: BaseChatModel) -> ChatNode:
    async def chat_node(state: GraphState) -> dict:
        # system 프롬프트는 모델 호출 시에만 앞에 붙이고, 응답만 반환해 체크포인터에
        # 영속화하지 않는다(매 턴 system 메시지가 히스토리에 쌓이는 것을 방지).
        messages = [SystemMessage(content=CHAT_SYSTEM_PROMPT), *state["messages"]]
        response = await model.ainvoke(messages)
        return {"messages": [response]}

    return chat_node
