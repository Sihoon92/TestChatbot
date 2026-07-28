"""레이아웃 발견 에이전트.

test_excel_agent.py 의 FakeToolCallingModel / FakeWorkbook 패턴을 그대로 쓴다 —
정해진 tool_calls 시퀀스를 돌려주면 실제 도구가 실행되고 그래프가 끝까지 돈다.
목 호출 여부만 보는 테스트가 아니라 조립이 실제로 맞물리는지 확인한다."""
from typing import Any, Optional

import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.ingest.discover import LayoutNotSubmittedError, discover_layout


class FakeWorkbook:
    """test_excel_agent.py 의 FakeWorkbook 과 같은 최소 인터페이스."""

    def __init__(self):
        self._rows = [["No", "금형번호"], [1, "RX28312"]]

    def sheet_names(self):
        return ["Sheet1"]

    def used_values(self, sheet):
        return self._rows, "A1"

    def range_values(self, sheet, address):
        return self._rows

    def column_values(self, sheet, column, max_rows=5000):
        return [r[0] for r in self._rows]


class FakeToolCallingModel(BaseChatModel):
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
        response = self.responses[min(self.i, len(self.responses) - 1)]
        self.i += 1
        return ChatResult(generations=[ChatGeneration(message=response)])

    @property
    def _llm_type(self) -> str:
        return "fake-tool-calling-model"


LAYOUT_ARGS = {
    "sheet_name": "Sheet1",
    "anchors": [{"cell": "B1", "text": "금형번호"}],
    "tables": [{
        "name": "상세", "role": "detail", "header_rows": [1],
        "data_start_row": 2,
        "columns": [{"field": "mold_no", "column": "B"}],
    }],
}


def test_returns_layout_submitted_via_tool():
    model = FakeToolCallingModel(responses=[
        AIMessage(content="", tool_calls=[
            {"name": "list_sheets", "args": {}, "id": "c1"}
        ]),
        AIMessage(content="", tool_calls=[
            {"name": "submit_layout", "args": LAYOUT_ARGS, "id": "c2"}
        ]),
        AIMessage(content="완료", tool_calls=[]),
    ])

    layout = discover_layout(model, FakeWorkbook(), "iqc", "Sheet1")

    assert layout.sheet_name == "Sheet1"
    assert layout.anchors[0].text == "금형번호"
    assert layout.tables[0].columns[0].field == "mold_no"


def test_raises_when_agent_never_submits():
    """에이전트가 제출 없이 끝나면 그 파일은 error 다. 빈 레이아웃을 돌려주면
    '표가 하나도 없는 시트'로 오인되어 조용히 0건이 들어온다."""
    model = FakeToolCallingModel(responses=[
        AIMessage(content="잘 모르겠다", tool_calls=[]),
    ])

    with pytest.raises(LayoutNotSubmittedError):
        discover_layout(model, FakeWorkbook(), "iqc", "Sheet1")


def test_last_submission_wins():
    """에이전트가 스스로 고쳐 다시 제출하면 마지막 것이 유효하다."""
    first = {**LAYOUT_ARGS, "anchors": [{"cell": "B1", "text": "관리번호"}]}
    model = FakeToolCallingModel(responses=[
        AIMessage(content="", tool_calls=[
            {"name": "submit_layout", "args": first, "id": "c1"}
        ]),
        AIMessage(content="", tool_calls=[
            {"name": "submit_layout", "args": LAYOUT_ARGS, "id": "c2"}
        ]),
        AIMessage(content="완료", tool_calls=[]),
    ])

    layout = discover_layout(model, FakeWorkbook(), "iqc", "Sheet1")

    assert layout.anchors[0].text == "금형번호"


def test_prompt_carries_stage_specific_field_vocabulary():
    """MES 와 IQC 는 요구 필드가 다르다. 같은 프롬프트를 쓰면 에이전트가
    MES 시트에서 punch 를 찾거나 IQC 시트에서 status 를 지어낸다."""
    received: list[str] = []

    class RecordingModel(FakeToolCallingModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            received.append("\n".join(str(m.content) for m in messages))
            return super()._generate(messages, stop, run_manager, **kwargs)

    model = RecordingModel(responses=[
        AIMessage(content="", tool_calls=[
            {"name": "submit_layout", "args": LAYOUT_ARGS, "id": "c1"}
        ]),
        AIMessage(content="완료", tool_calls=[]),
    ])

    discover_layout(model, FakeWorkbook(), "mes", "Sheet1")

    assert "defect_rate" in received[0]
    assert "punch" not in received[0]


def test_raises_for_stage_without_field_guide():
    """FIELD_GUIDE 에 없는 단계는 조용히 넘어가면 안 된다. 어휘 안내 없이
    돌리면 에이전트가 필드명을 지어내고, assemble 이 그 이름을 모르니
    데이터가 파싱은 되고 쓰이지는 않는 상태가 된다."""
    model = FakeToolCallingModel(responses=[
        AIMessage(content="", tool_calls=[
            {"name": "submit_layout", "args": LAYOUT_ARGS, "id": "c1"}
        ]),
        AIMessage(content="완료", tool_calls=[]),
    ])

    with pytest.raises(ValueError, match="FIELD_GUIDE"):
        discover_layout(model, FakeWorkbook(), "pqc", "Sheet1")


def test_submit_tool_schema_explains_row_conventions():
    """에이전트는 Python 주석이 아니라 도구의 JSON 스키마만 본다.

    data_start_row/data_end_row 의 관례가 스키마에 없으면 에이전트가 헤더
    행 번호를 data_end_row 에 채우고, 파싱이 조용히 0행이 된다(파서가 이제
    막지만, 애초에 틀리게 만들 이유가 없다)."""
    from app.ingest.discover import build_discover_agent

    _agent, tools = build_discover_agent(
        FakeToolCallingModel(responses=[]), FakeWorkbook(), "iqc", {}
    )
    submit = next(t for t in tools if t.name == "submit_layout")
    table = submit.args_schema.model_json_schema()["$defs"]["TableBlock"]

    assert "헤더 다음" in table["properties"]["data_start_row"]["description"]
    assert "헤더 행 번호를 넣지 마라" in table["properties"]["data_end_row"]["description"]
    assert "비어 있으면 안 된다" in table["properties"]["columns"]["description"]
    assert "summary" in table["properties"]["role"]["description"]


def test_prompt_states_the_data_row_convention():
    """스키마 설명과 프롬프트가 같은 관례를 말해야 한다 — 한쪽만 있으면
    다른 쪽을 고칠 때 조용히 어긋난다."""
    received: list[str] = []

    class RecordingModel(FakeToolCallingModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            received.append("\n".join(str(m.content) for m in messages))
            return super()._generate(messages, stop, run_manager, **kwargs)

    model = RecordingModel(responses=[
        AIMessage(content="", tool_calls=[
            {"name": "submit_layout", "args": LAYOUT_ARGS, "id": "c1"}
        ]),
        AIMessage(content="완료", tool_calls=[]),
    ])

    discover_layout(model, FakeWorkbook(), "iqc", "Sheet1")

    assert "data_start_row 는 헤더 **다음** 행이다" in received[0]
