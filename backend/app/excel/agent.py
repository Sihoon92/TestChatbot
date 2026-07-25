"""엑셀 분석 ReAct 에이전트.

LLM 이 도구를 호출하며 스스로 구조를 파악하도록 시스템 프롬프트로 절차를 안내한다.
시스템 지시는 create_react_agent 의 버전별 파라미터 차이를 피하려고 입력 메시지
앞에 SystemMessage 로 붙인다.
"""
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent

from app.excel.tools import make_excel_tools

EXCEL_SYSTEM_PROMPT = """너는 엑셀 데이터 분석가다. 엑셀 내용을 한 번에 다 볼 수 없으므로,
반드시 도구를 호출해 조금씩 살펴보며 스스로 구조를 파악한 뒤 분석한다.

권장 절차:
1) list_sheets 로 시트를 확인한다.
2) read_range 로 좌상단(예 A1:J6)부터 읽어 헤더 위치와 각 열의 의미를 파악한다.
   (예: 앞쪽 열은 라인/제품 같은 메타데이터, 뒤쪽 열 헤더는 날짜일 수 있다.)
3) column_profile 로 각 열이 라인명/제품명/날짜/숫자지표 중 무엇인지 확인한다.
4) find_value 로 '소계','합계' 같은 키워드 위치를 찾아 '집계행'을 식별한다.
   집계행은 원시 데이터 합산에서 제외해야 이중 계산을 피한다.
5) 숫자 계산은 직접 하지 말고 반드시 aggregate 도구로 한다.

규칙:
- 셀 값을 눈으로 더하지 마라. 합/평균/개수는 항상 aggregate 를 사용한다.
- 근거가 된 셀 주소(예 C2:C50, 소계행 위치)를 답변에 함께 제시한다.
- 확신이 없으면 범위를 더 읽어 확인한 뒤 결론을 낸다.
"""


def build_excel_agent(model, wb):
    """열린 workbook(wb)에 바인딩된 ReAct 에이전트를 만든다."""
    return create_react_agent(model, make_excel_tools(wb))


def run_excel_agent(model, wb, question: str) -> dict:
    """에이전트를 돌려 최종 답과 사용한 도구 목록을 돌려준다."""
    agent = build_excel_agent(model, wb)
    result = agent.invoke(
        {
            "messages": [
                SystemMessage(content=EXCEL_SYSTEM_PROMPT),
                HumanMessage(content=question),
            ]
        }
    )
    msgs = result["messages"]
    tool_calls: list[str] = []
    for m in msgs:
        for call in getattr(m, "tool_calls", None) or []:
            tool_calls.append(call.get("name", "?"))
    return {"answer": getattr(msgs[-1], "content", ""), "tool_calls": tool_calls}
