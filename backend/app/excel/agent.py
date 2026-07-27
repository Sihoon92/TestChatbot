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
   집계 대상이 여러 열(예: 월별 날짜 열 여러 개)에 걸쳐 있다면 aggregate 를
   한 열씩 여러 번 부르지 말고, 그 열들을 모두 포함하는 사각형 범위 하나로
   (예 E2:J20 처럼 여러 열·행에 걸친 범위) 한 번에 집계한다.
6) 집계행(소계/합계)을 이미 찾았다면, aggregate 로 구한 원시 데이터 합계를
   그 집계행의 값과 대조한다. 서로 다르면 집계 범위를 잘못 잡은 것이므로
   범위를 다시 확인하고 aggregate 를 다시 호출한다.

규칙:
- 셀 값을 눈으로 더하지 마라. 합/평균/개수는 항상 aggregate 를 사용한다.
- 근거가 된 셀 주소(예 E2:J20 처럼 여러 열·행에 걸친 사각형 범위도 가능, 소계행
  위치)를 답변에 함께 제시한다.
- 확신이 없으면 범위를 더 읽어 확인한 뒤 결론을 낸다.
"""


# create_react_agent 그래프의 기본 재귀 한도(~25 스텝)에 기대지 않고 명시한다.
# 약한 모델이 도구 호출을 반복하며 수렴하지 못하면 langgraph.errors.
# GraphRecursionError 로 끝나야, 무한정 스텝을 쓰다 조용히 잘리는 대신 호출자가
# 명확히 처리할 수 있다(analyze_excel.py 가 이 예외를 잡아 안내 메시지를 낸다).
_RECURSION_LIMIT = 40


def build_excel_agent(model, wb):
    """열린 workbook(wb)에 바인딩된 ReAct 에이전트를 만든다."""
    return create_react_agent(model, make_excel_tools(wb))


def run_excel_agent(model, wb, question: str, *, config: dict | None = None) -> dict:
    """에이전트를 돌려 최종 답과 사용한 도구 목록을 돌려준다.

    `config` 로 실행 config 를 덧붙일 수 있다(예: 디버그 로깅 콜백). 이 함수가
    직접 get_settings() 를 불러 콜백을 붙이지 않는 이유는, 라이브러리 함수가
    전역 설정에 묶이면 테스트/재사용이 어려워지기 때문이다 — 호출자가 config 에
    콜백을 넣어주는 LangChain 표준 패턴을 따른다(app/api/chat.py 와 동일).
    `recursion_limit` 은 호출자가 명시하지 않는 한 이 모듈의 값을 유지한다.
    """
    agent = build_excel_agent(model, wb)
    run_config = {"recursion_limit": _RECURSION_LIMIT, **(config or {})}
    result = agent.invoke(
        {
            "messages": [
                SystemMessage(content=EXCEL_SYSTEM_PROMPT),
                HumanMessage(content=question),
            ]
        },
        config=run_config,
    )
    msgs = result["messages"]
    tool_calls: list[str] = []
    for m in msgs:
        for call in getattr(m, "tool_calls", None) or []:
            tool_calls.append(call.get("name", "?"))
    return {"answer": getattr(msgs[-1], "content", ""), "tool_calls": tool_calls}
