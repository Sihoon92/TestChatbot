from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class GraphState(TypedDict, total=False):
    # add_messages reducer 는 새 응답을 기존 히스토리에 append 한다. 체크포인터가
    # thread_id(=session_id) 별로 이 messages 리스트를 영속화하므로, 같은 세션에서
    # 다시 호출하면 과거 대화가 그대로 복원돼 문맥이 이어진다.
    messages: Annotated[list[BaseMessage], add_messages]
    session_id: str
