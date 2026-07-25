"""run_excel_agent 조립 검증 — 실제 LLM 대신 정해진 응답을 순서대로 돌려주는
가짜 모델을 사용해, LangGraph ReAct 루프·실제 도구 실행·tool_calls/answer 추출
로직이 실제로 맞물려 동작하는지 확인한다(내부 목 호출 여부만 보는 테스트가
아니라, 그래프를 끝까지 돌려 결과를 검증한다)."""
from typing import Any, Optional

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.excel.agent import EXCEL_SYSTEM_PROMPT, run_excel_agent
from app.excel.grid import parse_a1


def _slice_rows(rows: list[list], address: str) -> list[list]:
    """test_excel_tools.py 의 동일한 이름의 헬퍼와 같은 목적 — address 를
    무시하고 고정값을 돌려주던 예전 가짜는 사각형 범위 집계 회귀를 못 잡았다."""
    start, _, end = address.partition(":")
    end = end or start
    r1, c1 = parse_a1(start)
    r2, c2 = parse_a1(end)
    out = []
    for r in range(r1, r2 + 1):
        row_vals = []
        for c in range(c1, c2 + 1):
            ri, ci = r - 1, c - 1
            if 0 <= ri < len(rows) and 0 <= ci < len(rows[ri]):
                row_vals.append(rows[ri][ci])
            else:
                row_vals.append(None)
        out.append(row_vals)
    return out


class FakeWorkbook:
    """test_excel_tools.py 의 FakeWorkbook 과 동일한 최소 인터페이스."""

    def __init__(self):
        self._rows = [
            ["라인", "제품", "2026-01-01"],
            ["A", "제품1", 3],
            ["A", "소계", 3],
            ["B", "제품2", 5],
        ]

    def sheet_names(self):
        return ["데이터"]

    def used_values(self, sheet):
        return self._rows, "A1"

    def range_values(self, sheet, address):
        return _slice_rows(self._rows, address)

    def column_values(self, sheet, column, max_rows=5000):
        return [r[0] for r in self._rows]


class FakeToolCallingModel(BaseChatModel):
    """정해진 AIMessage 시퀀스를 순서대로 돌려주는 가짜 채팅 모델.

    `bind_tools` 는 실제 도구 스키마 검증 없이 self 를 그대로 돌려준다 —
    이 테스트는 모델이 아니라 그 뒤(LangGraph 그래프 실행, ToolNode 를 통한
    실제 도구 호출, run_excel_agent 의 결과 추출)를 검증하는 것이 목적이다.
    """

    responses: list[AIMessage]
    i: int = 0

    def bind_tools(self, tools: Any, **kwargs: Any) -> "FakeToolCallingModel":
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        response = self.responses[self.i]
        self.i += 1
        return ChatResult(generations=[ChatGeneration(message=response)])

    @property
    def _llm_type(self) -> str:
        return "fake-tool-calling-model"


def test_run_excel_agent_collects_tool_calls_and_final_answer():
    model = FakeToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "list_sheets", "args": {}, "id": "call_1"}
                ],
            ),
            AIMessage(content="시트는 데이터 하나뿐이다.", tool_calls=[]),
        ]
    )
    wb = FakeWorkbook()

    result = run_excel_agent(model, wb, "시트가 몇 개야?")

    assert result["tool_calls"] == ["list_sheets"]
    assert result["answer"] == "시트는 데이터 하나뿐이다."


def test_run_excel_agent_injects_system_prompt_as_message():
    """시스템 지시가 create_react_agent 파라미터가 아니라 SystemMessage 로
    입력 메시지 맨 앞에 주입되는지, 가짜 모델이 실제로 받은 메시지를 통해
    확인한다."""
    received: list[list[BaseMessage]] = []

    class RecordingModel(FakeToolCallingModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            received.append(list(messages))
            return super()._generate(messages, stop, run_manager, **kwargs)

    model = RecordingModel(
        responses=[AIMessage(content="답변", tool_calls=[])]
    )
    wb = FakeWorkbook()

    run_excel_agent(model, wb, "질문")

    first_call_messages = received[0]
    assert first_call_messages[0].type == "system"
    assert first_call_messages[0].content == EXCEL_SYSTEM_PROMPT
    assert first_call_messages[1].content == "질문"
