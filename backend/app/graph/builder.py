from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.graph.nodes.chat import make_chat_node
from app.graph.state import GraphState


def build_graph(model: BaseChatModel, checkpointer: BaseCheckpointSaver) -> CompiledStateGraph:
    """단일 chat 노드 그래프: START → chat → END.

    노드를 추가해 확장하려면 여기서 sg.add_node / 조건부 엣지를 붙인다(예: 라우터
    노드로 chat 과 다른 도구 노드를 분기). 체크포인터를 compile 에 넘기면 모든 노드가
    자동으로 세션별 메모리를 갖는다.
    """
    sg = StateGraph(GraphState)
    sg.add_node("chat", make_chat_node(model))
    sg.add_edge(START, "chat")
    sg.add_edge("chat", END)
    return sg.compile(checkpointer=checkpointer)
