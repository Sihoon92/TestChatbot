"""시트 구조를 ReAct 에이전트로 탐색해 SheetLayout 을 얻는다.

왜 에이전트인가: 실물 IQC 시트는 한 장에 카테고리 2개 × 표 2개 = 표 4개가
있고 2단 병합 헤더까지 섞여 있다. 격자를 한 번 보여주고 매핑을 받는 단발
호출로는 이 구조를 못 잡는다 — 어디에 무엇이 있는지 찾아다녀야 한다.

왜 submit_layout 이 도구인가: 에이전트가 마지막에 JSON 을 텍스트로 뱉게 하면
파싱이 깨지기 쉽다. 도구 인자로 받으면 Pydantic 이 검증하고, 잘못 채우면
도구 에러가 나서 에이전트가 스스로 고쳐 다시 제출한다(ToolNode 가 그 왕복을
이미 처리한다).

이 에이전트는 캐시 미스에만 돈다. 양식이 안정적이면 평생 몇 번이다.
"""
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from langgraph.prebuilt import create_react_agent

from app.excel.tools import make_excel_tools
from app.ingest.schemas import (
    IQC_VALUE_FIELDS,
    MES_FIELDS,
    SheetLayout,
    SourceKind,
)

# app/excel/agent.py 와 같은 이유로 명시한다 — 약한 모델이 수렴하지 못하면
# GraphRecursionError 로 끝나야 호출자가 "이 파일은 error" 로 처리할 수 있다.
_RECURSION_LIMIT = 40


class LayoutNotSubmittedError(RuntimeError):
    """에이전트가 submit_layout 을 부르지 않고 끝났다.

    빈 레이아웃을 돌려주지 않는 이유: 표가 하나도 없는 시트로 오인되어
    조용히 0건이 들어온다. 실패는 실패로 드러나야 한다.
    """


_BASE_PROMPT = """너는 엑셀 시트의 구조를 파악하는 분석가다.
목표는 이 시트에서 데이터를 기계적으로 뽑을 수 있는 '레이아웃'을 알아내는 것이다.

반드시 도구를 호출해 조금씩 살펴보며 스스로 구조를 파악한다. 한 시트에 표가
여러 개 있을 수 있고, 헤더가 2단으로 병합돼 있을 수 있다.

권장 절차:
1) **sheet_outline 을 먼저 호출한다.** 시트 전체가 한 화면에 나오고, 표가 몇
   행에서 시작해 몇 행에서 끝나는지, 헤더가 몇 행인지 바로 보인다. 채워진
   칸 수가 달라지는 지점이 표의 경계다.
2) 윤곽에서 찾은 표마다 read_range 로 그 범위만 읽어 컬럼을 확인한다.
   read_range 는 30행까지만 보여주므로, 아래쪽 표는 그 표의 시작 행부터
   범위를 잡아 읽어야 한다 — 시트 맨 위부터 읽으면 아래쪽 표는 잘려서 안 보인다.
3) column_profile 로 어느 열이 무엇인지 검증한다. 특히 금형번호 열은
   고유값이 많고 값의 생김새가 일정하다.
4) find_value 로 '소계','합계','총계' 위치를 찾아 집계 표를 식별한다.

레이아웃 작성 규칙:
- 집계/요약 표는 role="summary" 로 표시한다. 이 표의 행은 금형이 아니다.
  실제 데이터가 있는 표만 role="detail" 이다.
- 헤더가 2단이면 header_rows 에 두 행을 모두 넣고, 컬럼의 field 는
  상단과 하단을 '/' 로 결합해 쓴다. 예: "성형부/정극 성형"
- data_start_row 는 헤더 **다음** 행이다. data_end_row 는 표 아래에 다른 블록(비고·서명란·
  다음 표)이 있을 때만 지정하고, 없으면 null 로 둬라 — 빈 행에서 자동으로 멈춘다.
- 표가 아니라 라벨 옆에 값이 있는 블록(상단 기본정보 등)은 key_values 로 쓴다.
- anchors 에는 "이 셀에 이 텍스트가 있으면 같은 양식이다" 라고 판단할 근거
  셀을 3개 이상 지목한다. 각 표의 헤더에서 하나씩 고르면 좋다. 나중에 누가
  양식을 바꾸면 이 앵커가 달라져 다시 분석하게 된다. 데이터 값이 아니라
  **헤더 텍스트**를 골라라 — 데이터는 행이 추가되면 바뀐다.
- 확신이 없는 부분은 notes 에 적어라.

분석이 끝나면 submit_layout 을 호출한다. 그 뒤에는 더 이상 도구를 부르지 말고
'완료' 라고만 답하라.
"""

FIELD_GUIDE: dict[SourceKind, str] = {
    "mes": (
        "이 시트는 MES 생산 이벤트 기록이다. 한 행이 생산 1건이며 같은 금형이\n"
        "여러 번 나올 수 있다. 아래 필드에 해당하는 열을 찾아 field 이름을\n"
        "**정확히 이 영문 이름으로** 지정하라(없으면 넣지 않는다):\n"
        + "\n".join(f"  - {f}" for f in MES_FIELDS)
        + "\n\nmold_no 와 status 는 반드시 찾아야 한다. 없으면 notes 에 적어라.\n"
    ),
    "iqc": (
        "이 시트는 IQC 입고 검사 기록이다. 금형번호 열이 있다.\n"
        "금형번호 열의 field 이름은 반드시 'mold_no' 로 지정하라.\n"
        "아래 치수 항목이 있으면 field 이름을 이 영문 이름으로 지정하라:\n"
        + "\n".join(f"  - {f}" for f in IQC_VALUE_FIELDS)
        + "\n\n그 밖의 열(측정자, 조립자, 연마자, 측정 결과 등)은 헤더 텍스트를\n"
        "그대로 field 이름으로 쓴다. 화면에 그 이름 그대로 표시된다.\n"
    ),
}


def _make_submit_tool(holder: dict) -> StructuredTool:
    def _submit(**kwargs) -> str:
        holder["layout"] = SheetLayout(**kwargs)
        return "레이아웃을 접수했다. 더 이상 도구를 호출하지 말고 '완료'라고만 답하라."

    return StructuredTool.from_function(
        func=_submit,
        name="submit_layout",
        description=(
            "시트 구조 분석이 끝났을 때 호출한다. 이 도구를 호출해야 작업이 "
            "완료된다. 인자가 스키마에 맞지 않으면 오류가 돌아오니 고쳐서 다시 호출하라."
        ),
        args_schema=SheetLayout,
    )


def build_discover_agent(model, wb, kind: SourceKind, holder: dict):
    """열린 workbook(wb)에 바인딩된 발견 에이전트와 도구 목록을 만든다.

    `holder` 에 최종 제출된 SheetLayout 이 담긴다(에이전트 그래프의 반환값이
    아니라 도구 클로저를 통해 전달된다 — submit_layout 인자가 검증된 시점의
    Pydantic 인스턴스를 그대로 잡아두기 위해서다).
    """
    tools = make_excel_tools(wb) + [_make_submit_tool(holder)]
    agent = create_react_agent(model, tools)
    return agent, tools


def discover_layout(
    model,
    wb,
    kind: SourceKind,
    sheet_name: str,
    *,
    config: dict | None = None,
) -> SheetLayout:
    """에이전트를 돌려 시트 레이아웃을 얻는다.

    `config` 로 실행 config 를 덧붙일 수 있다(디버그 로깅 콜백 등) —
    run_excel_agent 와 같은 패턴이다. 이 함수가 직접 get_settings() 를 불러
    콜백을 붙이지 않는 이유는 라이브러리 함수가 전역 설정에 묶이면 테스트와
    재사용이 어려워지기 때문이다.
    """
    guide = FIELD_GUIDE.get(kind)
    if guide is None:
        # 어휘 안내 없이 그냥 돌리면 에이전트가 필드명을 지어내고, assemble 이
        # 그 이름을 모르니 데이터가 파싱은 되고 쓰이지는 않는 상태가 된다 —
        # LayoutNotSubmittedError 가 막으려는 것과 같은 성격의 조용한 실패다.
        # 새 단계를 켤 때는 FIELD_GUIDE 항목을 함께 추가해야 한다.
        raise ValueError(
            f"'{kind}' 단계의 필드 어휘가 FIELD_GUIDE 에 없다. "
            f"현재 지원: {sorted(FIELD_GUIDE)}"
        )

    holder: dict = {}
    agent, _tools = build_discover_agent(model, wb, kind, holder)

    prompt = _BASE_PROMPT + "\n" + guide
    question = (
        f"시트 '{sheet_name}' 의 구조를 파악해 submit_layout 으로 제출하라. "
        f"sheet_name 은 정확히 '{sheet_name}' 로 적어라."
    )
    run_config = {"recursion_limit": _RECURSION_LIMIT, **(config or {})}

    agent.invoke(
        {
            "messages": [
                SystemMessage(content=prompt),
                HumanMessage(content=question),
            ]
        },
        config=run_config,
    )

    layout = holder.get("layout")
    if layout is None:
        raise LayoutNotSubmittedError(
            f"에이전트가 시트 '{sheet_name}' 의 레이아웃을 제출하지 않았다."
        )
    return layout
