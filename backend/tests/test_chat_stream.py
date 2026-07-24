import asyncio

# app.main 을 먼저 로드해야 한다(chat.py ↔ app.main 순환 import 회피).
import app.main  # noqa: F401,E402
from app.api.chat import stream_graph  # noqa: E402


class _Chunk:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeGraph:
    """astream 이 몇 개의 토큰을 낸 뒤 '마지막 단계'에서 완료 플래그를 세운다.

    실제 그래프에서 on_llm_end(=디버그 로그 기록)와 체크포인터 저장이 일어나는 지점을
    이 완료 플래그로 흉내낸다.
    """

    def __init__(self) -> None:
        self.completed = False

    async def astream(self, inputs, cfg, stream_mode):  # noqa: ANN001
        for t in ["a", "b", "c"]:
            yield _Chunk(t), {"langgraph_node": "chat"}
        self.completed = True


async def test_graph_runs_to_completion_even_if_client_disconnects_early():
    graph = _FakeGraph()
    gen = stream_graph(graph, {}, {}, "sess")

    # 클라이언트가 첫 토큰만 받고 연결을 끊은 상황을 흉내낸다.
    first = await gen.__anext__()
    assert "a" in first
    await gen.aclose()

    # 그래프 실행은 별도 태스크라, 클라이언트가 끊겨도 끝까지 돌아 완료돼야 한다.
    for _ in range(100):
        if graph.completed:
            break
        await asyncio.sleep(0.01)
    assert graph.completed is True
