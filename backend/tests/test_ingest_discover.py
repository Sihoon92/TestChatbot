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


# ── 제출 검증 ───────────────────────────────────────────────────────────
# FakeWorkbook 의 격자는 A·B 두 열이 차 있는데 LAYOUT_ARGS 는 B 만 매핑한다 —
# 그래서 아래 테스트들은 별도 준비 없이 "덜 채운 제출" 을 재현한다.

INCOMPLETE = LAYOUT_ARGS
COMPLETE = {
    **LAYOUT_ARGS,
    "tables": [{
        **LAYOUT_ARGS["tables"][0],
        "columns": [
            {"field": "No", "column": "A"},
            {"field": "mold_no", "column": "B"},
        ],
    }],
}


def _submit_results(*submissions) -> tuple[list[str], object]:
    """제출들을 차례로 흘려보내고 (도구 응답들, 최종 레이아웃)."""
    model = FakeToolCallingModel(responses=[
        AIMessage(content="", tool_calls=[
            {"name": "submit_layout", "args": args, "id": f"c{i}"}
        ])
        for i, args in enumerate(submissions)
    ] + [AIMessage(content="완료", tool_calls=[])])

    holder: dict = {}
    from app.ingest.discover import build_discover_agent

    agent, tools = build_discover_agent(model, FakeWorkbook(), "iqc", holder)
    submit = next(t for t in tools if t.name == "submit_layout")
    replies = [submit.invoke(args) for args in submissions]
    return replies, holder.get("layout")


def test_submit_sends_back_a_layout_that_leaves_columns_unmapped():
    """프롬프트로 요구해도 모델은 표의 오른쪽 끝을 찍는다. 격자는 끝이
    어디인지 아니까, 아는 쪽이 확인해서 되돌려보낸다."""
    replies, _ = _submit_results(INCOMPLETE)

    assert "아직 접수하지 않았다" in replies[0]
    assert "A(No)" in replies[0], "어느 열이 빠졌는지 알려줘야 고칠 수 있다"


def test_submit_accepts_a_complete_layout_without_complaint():
    replies, layout = _submit_results(COMPLETE)

    assert "레이아웃을 접수했다" in replies[0]
    assert layout is not None


def test_agent_can_fix_and_resubmit():
    replies, layout = _submit_results(INCOMPLETE, COMPLETE)

    assert "아직 접수하지 않았다" in replies[0]
    assert "레이아웃을 접수했다" in replies[1]
    assert [c.column for c in layout.tables[0].columns] == ["A", "B"]


def test_repeated_incomplete_submissions_are_eventually_accepted():
    """무한 왕복이면 못 맞추는 모델이 걸렸을 때 recursion_limit 에 걸려
    그 파일의 레이아웃을 하나도 못 얻는다. 몇 열 빠진 쪽이 낫다."""
    replies, layout = _submit_results(INCOMPLETE, INCOMPLETE, INCOMPLETE)

    assert "아직 접수하지 않았다" in replies[0]
    assert "아직 접수하지 않았다" in replies[1]
    assert "레이아웃을 접수했다" in replies[2], "예산을 넘으면 받아준다"
    assert layout is not None


def test_rejected_layout_is_still_kept_as_a_fallback():
    """에이전트가 되돌려받고 재제출을 포기하면(그냥 '완료'라고 답하면)
    LayoutNotSubmittedError 로 파일 전체가 실패한다 — 불완전한 레이아웃이
    아무것도 없는 것보다 낫다."""
    model = FakeToolCallingModel(responses=[
        AIMessage(content="", tool_calls=[
            {"name": "submit_layout", "args": INCOMPLETE, "id": "c1"}
        ]),
        AIMessage(content="완료", tool_calls=[]),
    ])

    layout = discover_layout(model, FakeWorkbook(), "iqc", "Sheet1")

    assert layout.tables[0].columns[0].field == "mold_no"


def test_validation_failure_does_not_block_submission():
    """검증 자체가 못 도는 것(시트 이름 오타 등)이 제출을 막으면 레이아웃을
    하나도 못 얻어 파일 전체가 실패한다."""
    class BrokenWorkbook(FakeWorkbook):
        def used_values(self, sheet):
            raise RuntimeError("그런 시트 없음")

    model = FakeToolCallingModel(responses=[])
    holder: dict = {}
    from app.ingest.discover import build_discover_agent

    _agent, tools = build_discover_agent(model, BrokenWorkbook(), "iqc", holder)
    submit = next(t for t in tools if t.name == "submit_layout")

    assert "레이아웃을 접수했다" in submit.invoke(INCOMPLETE)


def _prompt_for(kind: str) -> str:
    """discover_layout 이 실제로 모델에 보낸 시스템 프롬프트."""
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
    discover_layout(model, FakeWorkbook(), kind, "Sheet1")
    return received[0]


def test_prompt_demands_every_column_be_mapped():
    """실물에서 에이전트가 20열짜리 표를 9열만 매핑하고 끊었다 —
    PUNCH/DIE/차이/간극이 통째로 빠졌고, 빠졌다는 사실이 어디에도 안 남았다.
    완전성 요구가 프롬프트에 없으면 '적당히' 가 기본값이 된다."""
    prompt = _prompt_for("iqc")

    assert "표의 컬럼을 하나도 빠뜨리지 마라" in prompt
    assert "중간에 끊기지 않아야 한다" in prompt
    # 고정 어휘 밖의 열을 버리지 않고 헤더 텍스트로 살리는 규칙
    assert "헤더 텍스트를 그대로" in prompt


def test_prompt_explains_the_outline_column_span():
    """근본 원인은 '매핑을 덜 했다' 가 아니라 'A33:J41 로 좁게 읽어 K열 이후를
    보지도 못했다' 였다. 윤곽이 이제 열 범위를 주므로 그걸 쓰라고 말해야 한다 —
    안 그러면 앞쪽 4칸 미리보기만 보고 표의 폭을 짐작한다."""
    prompt = _prompt_for("iqc")

    assert "B~U" in prompt, "윤곽 줄의 생김새를 예시로 보여줘야 한다"
    assert "윤곽이 알려준 열 범위를 그대로 쓴다" in prompt
    assert "읽은 표를 윤곽과 대조한다" in prompt


def test_mes_guide_separates_machine_from_model_name():
    """실물에서 machine 을 G(호기) 대신 E(기종)로 지목해 호기가 H104 로 들어왔다."""
    prompt = _prompt_for("mes")

    assert "호기" in prompt
    assert "'기종'(H104 처럼" in prompt


def test_iqc_guide_names_the_korean_headers_of_fixed_fields():
    """영문 이름만 주면 PUNCH/DIE 는 맞히면서 '차이'/'간극' 은 고정 필드로
    연결하지 못한다. 힌트는 얹되 어휘 목록의 출처는 IQC_VALUE_FIELDS 하나다."""
    from app.ingest.schemas import IQC_VALUE_FIELDS

    prompt = _prompt_for("iqc")

    for field in IQC_VALUE_FIELDS:
        assert f"  - {field}" in prompt
    assert "diff (차이, 편차)" in prompt
    assert "gap (간극, 클리어런스)" in prompt


def test_iqc_guide_says_mold_no_column_may_be_named_otherwise():
    """이력표의 '관리 번호' 를 mold_no 로 못 잡아 5행이 통째로 버려졌다."""
    prompt = _prompt_for("iqc")

    assert "관리 번호" in prompt
    assert "detail 표마다 이 열을 찾아라" in prompt


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
