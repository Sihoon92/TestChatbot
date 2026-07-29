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
from app.ingest.layout import find_layout_gaps
from app.ingest.schemas import (
    EES_FIELDS,
    IQC_VALUE_FIELDS,
    JIG_MASTER_FIELDS,
    MES_FIELDS,
    SheetLayout,
    SourceKind,
)

# app/excel/agent.py 와 같은 이유로 명시한다 — 약한 모델이 수렴하지 못하면
# GraphRecursionError 로 끝나야 호출자가 "이 파일은 error" 로 처리할 수 있다.
_RECURSION_LIMIT = 40

# submit_layout 이 불완전한 레이아웃을 되돌려보낼 최대 횟수. 프롬프트로 요구해도
# 모델은 표의 오른쪽 끝을 찍으므로 격자와 대조해 되돌려보내지만, 무제한이면
# 못 맞추는 모델이 걸렸을 때 왕복만 하다 recursion_limit 에 걸려 그 파일의
# 레이아웃을 하나도 못 얻는다. 몇 열 빠진 레이아웃이 그것보다 낫다.
_MAX_SUBMIT_REJECTIONS = 2


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
   칸 수가 달라지는 지점이 표의 경계다. 각 줄은 이렇게 생겼다:

       33  16칸  B~U  B=No · C=입고 시간 · D=금형 번호 · E=성형부

   "16칸" 은 그 행에 값이 있는 칸 수, "B~U" 는 **그 값들이 걸쳐 있는 열 범위**,
   그 뒤는 앞쪽 4칸의 미리보기일 뿐이다. 미리보기에 안 나온다고 열이 없는 게
   아니다 — 이 표는 U 열까지 있다.
2) 윤곽에서 찾은 표마다 read_range 로 그 범위만 읽어 컬럼을 확인한다.
   read_range 는 30행·30열까지만 보여주므로, 아래쪽 표는 그 표의 시작 행부터
   범위를 잡아 읽어야 한다 — 시트 맨 위부터 읽으면 아래쪽 표는 잘려서 안 보인다.
   **오른쪽 끝은 윤곽이 알려준 열 범위를 그대로 쓴다.** 위 예라면 'B33:U41'
   이다. 찍어서 좁게 자르면 잘려 나간 열은 있는 줄도 모르고 지나간다.
3) **읽은 표를 윤곽과 대조한다.** 윤곽이 "16칸 B~U" 라고 한 행에서 채워진
   칸이 4개밖에 안 보인다면 나머지는 읽은 범위 바깥에 있다. 범위를 넓혀
   다시 읽어라.
4) column_profile 로 어느 열이 무엇인지 검증한다. 특히 금형번호 열은
   고유값이 많고 값의 생김새가 일정하다.
5) find_value 로 '소계','합계','총계' 위치를 찾아 집계 표를 식별한다.

레이아웃 작성 규칙:
- 집계/요약 표는 role="summary" 로 표시한다. 이 표의 행은 금형이 아니다.
  실제 데이터가 있는 표만 role="detail" 이다.
- 헤더가 2단이면 header_rows 에 두 행을 모두 넣고, 컬럼의 field 는
  상단과 하단을 '/' 로 결합해 쓴다. 예: "성형부/정극 성형"
- **표의 컬럼을 하나도 빠뜨리지 마라.** columns 에는 표의 첫 열부터 마지막
  열까지 전부 넣는다 — 열 문자가 B,C,D,…,U 처럼 중간에 끊기지 않아야 한다.
  고정 어휘(mold_no 등)에 해당하지 않는 열은 **헤더 텍스트를 그대로** field
  이름으로 쓴다. 쓸모없어 보이거나 값이 비어 보인다고 빼지 마라 — 빠뜨린 열의
  값은 그 뒤 어디에도 나타나지 않는다. 몇 개 쓰다가 중간에서 멈추지 말고
  표의 오른쪽 끝까지 간다.
- data_start_row 는 헤더 **다음** 행이다. data_end_row 는 표 아래에 다른 블록(비고·서명란·
  다음 표)이 있을 때만 지정하고, 없으면 null 로 둬라 — 빈 행에서 자동으로 멈춘다.
- 표가 아니라 라벨 옆에 값이 있는 블록(상단 기본정보 등)은 key_values 로 쓴다.
- anchors 에는 "이 셀에 이 텍스트가 있으면 같은 양식이다" 라고 판단할 근거
  셀을 3개 이상 지목한다. 각 표의 헤더에서 하나씩 고르면 좋다. 나중에 누가
  양식을 바꾸면 이 앵커가 달라져 다시 분석하게 된다. 데이터 값이 아니라
  **헤더 텍스트**를 골라라 — 데이터는 행이 추가되면 바뀐다.
- 확신이 없는 부분은 notes 에 적어라.

제출 직전에 표마다 스스로 검산하라: columns 의 열 문자가 윤곽이 알려준 열
범위(예 B~U)의 끝까지 이어지는가? 시트의 모든 표를 tables 에 넣었는가?
빠진 곳이 있으면 submit_layout 이 어디가 빠졌는지 알려주며 되돌려보낸다 —
그때는 그 부분을 read_range 로 읽어 채운 뒤 다시 호출하라.

분석이 끝나면 submit_layout 을 호출한다. 그 뒤에는 더 이상 도구를 부르지 말고
'완료' 라고만 답하라.
"""

# IQC 고정 필드가 실물 시트에서 어떤 헤더 문구로 나타나는지. 영문 이름만
# 나열하면 에이전트가 'PUNCH'/'DIE' 는 알아보면서 '차이'/'간극' 은 고정 필드로
# 연결하지 못하고 한글 헤더 그대로 남긴다 — 파싱은 되는데 diff/gap 자리는
# 비는, 화면만 봐서는 모르는 손실이다.
#
# 어휘 목록 자체는 IQC_VALUE_FIELDS 가 단일 출처다. 여기는 힌트만 얹으므로
# 새 필드를 추가해도 힌트 없이 그대로 안내된다(누락되지 않는다).
_IQC_HEADER_HINTS = {
    "punch": "PUNCH, 펀치",
    "die": "DIE, 다이",
    "diff": "차이, 편차",
    "gap": "간극, 클리어런스",
}

FIELD_GUIDE: dict[SourceKind, str] = {
    "ees": (
        "이 시트는 JIG 관리대장이다. **시트 하나가 금형 하나**이고, 한 행이\n"
        "그 금형에 일어난 사건 하나(어디로 옮겨졌는지)를 시간순으로 적은 것이다.\n"
        "아래 필드에 해당하는 열을 찾아 field 이름을 **정확히 이 영문 이름으로**\n"
        "지정하라(없으면 넣지 않는다):\n"
        + "\n".join(f"  - {f}" for f in EES_FIELDS)
        + "\n\n가장 중요한 것은 location(위치) 열이다. 이 값이 '설비' 인 행이\n"
        "금형이 설비에 투입된 시점이고, 그 다음 행의 시각이 빠져나온 시점이다.\n"
        "이 열을 놓치면 금형이 언제 가동됐는지 알 방법이 사라진다.\n"
        "\n이 시트에 **금형번호 열은 없다.** 찾지 못했다고 헤매지 마라 —\n"
        "equipment(설비명)으로 다른 문서에서 찾는다. 그래서 equipment 는\n"
        "반드시 잡아야 한다.\n"
    ),
    "jig_master": (
        "이 시트는 JIG 기준정보다. 설비명·금형번호·설비코드·라인을 잇는\n"
        "매핑표이며, 한 행이 금형 하나다. 아래 필드에 해당하는 열을 찾아\n"
        "field 이름을 **정확히 이 영문 이름으로** 지정하라:\n"
        + "\n".join(f"  - {f}" for f in JIG_MASTER_FIELDS)
        + "\n\n짝지어 보면 이렇다:\n"
        "  'JIG ID'(#RX39513 처럼 생긴 값) → mold_no\n"
        "  'JIG명'                         → jig_name\n"
        "  '설비명'(POU WND10_Stack(1차)_01 꼴) → equipment\n"
        "  '설비코드'(21004780 같은 숫자)   → equipment_code\n"
        "  'Line명'                        → line\n"
        "\nequipment 와 equipment_code 는 반드시 찾아야 한다. 이 둘이 없으면\n"
        "다른 두 문서를 이어붙일 수 없다. 없으면 notes 에 적어라.\n"
    ),
    "mes": (
        "이 시트는 MES 일자별 생산·불량 실적이다. 한 행이 (라인, 설비코드)\n"
        "하나의 그날 실적이며, **금형번호는 없다.**\n"
        "아래 필드에 해당하는 열을 찾아 field 이름을 **정확히 이 영문 이름으로**\n"
        "지정하라(없으면 넣지 않는다):\n"
        + "\n".join(f"  - {f}" for f in MES_FIELDS)
        + "\n\n짝지어 보면 이렇다:\n"
        "  '투입수량' → produced,  '양품수량' → good,  '불량수량' → defects\n"
        "  '종합/불량율(PPM)' → defect_ppm  (조립라인·조립별화성 것이 아니라\n"
        "                                   **종합** 것을 골라라)\n"
        "\nrun_date(날짜)는 표 안이 아니라 시트 위쪽 라벨에 있을 수 있다.\n"
        "그럴 때는 표의 컬럼이 아니라 key_values 로 잡아라 — 그래야 모든 행에\n"
        "그 날짜가 붙는다.\n"
        "\n마지막의 'TOTAL' 행은 라인이 아니라 합계다. 그 행에는 설비코드가\n"
        "없으므로 자연히 걸러지지만, 표의 범위를 잡을 때 참고하라.\n"
    ),
    "iqc": (
        "이 시트는 IQC 입고 검사 기록이다. 금형번호 열이 있다.\n"
        "금형번호 열의 field 이름은 반드시 'mold_no' 로 지정하라.\n"
        "그 열의 헤더가 '금형 번호' 가 아닐 수 있다 — '관리 번호', '관리번호',\n"
        "'금형No' 처럼 표마다 다르게 부른다. 헤더 문구가 아니라 **값의 생김새**로\n"
        "판단하라: RX28312, #RX41194 처럼 영문+숫자가 일정한 형태로 반복되고\n"
        "고유값이 많은 열이 금형번호다(column_profile 로 확인할 수 있다).\n"
        "detail 표마다 이 열을 찾아라 — 한 표라도 놓치면 그 표의 행이 통째로\n"
        "버려진다.\n"
        "아래 치수 항목이 있으면 field 이름을 이 영문 이름으로 지정하라\n"
        "(괄호 안은 실물에서 쓰이는 헤더 문구다):\n"
        + "\n".join(
            f"  - {f}" + (f" ({_IQC_HEADER_HINTS[f]})" if f in _IQC_HEADER_HINTS else "")
            for f in IQC_VALUE_FIELDS
        )
        + "\n\n그 밖의 열(측정자, 조립자, 연마자, 측정 결과 등)은 헤더 텍스트를\n"
        "그대로 field 이름으로 쓴다. 화면에 그 이름 그대로 표시된다.\n"
    ),
}


def _gaps(wb, layout: SheetLayout) -> list[str]:
    """제출된 레이아웃이 시트에서 덮지 못한 곳. 검증이 못 돌면 빈 목록이다.

    검증 자체의 실패(시트 이름 오타 등)가 제출을 막을 이유는 없다 — 그러면
    레이아웃을 하나도 못 얻어 파일 전체가 실패한다.
    """
    try:
        grid, top_left = wb.used_values(layout.sheet_name)
    except Exception:  # noqa: BLE001
        return []
    return find_layout_gaps(grid, top_left, layout)


def _make_submit_tool(holder: dict, wb) -> StructuredTool:
    def _submit(**kwargs) -> str:
        layout = SheetLayout(**kwargs)
        # 되돌려보낼 때도 붙잡아 둔다. 에이전트가 재제출을 포기하면
        # LayoutNotSubmittedError 로 파일 전체가 실패하는데, 불완전한
        # 레이아웃이 아무것도 없는 것보다 낫다(다음 제출이 덮어쓴다).
        holder["layout"] = layout

        gaps = _gaps(wb, layout)
        rejections = holder.get("rejections", 0)
        if gaps and rejections < _MAX_SUBMIT_REJECTIONS:
            # 무한 왕복을 막는다. 못 맞추는 모델이 걸렸을 때 표를 하나도 못
            # 얻는 것보다, 몇 열 빠진 레이아웃이라도 받는 편이 낫다.
            holder["rejections"] = rejections + 1
            # 예외가 아니라 문자열로 돌려준다. ToolException 은 ToolNode 를
            # 그대로 통과해 그래프 밖으로 나가버려(실측), 되돌려보내려던 제출이
            # 파일 전체의 실패가 된다 — 검증이 막으려던 것보다 나쁜 결과다.
            return (
                "아직 접수하지 않았다. 레이아웃에 덮이지 않은 곳이 있다.\n"
                + "\n".join(f"- {g}" for g in gaps)
                + "\n고쳐서 submit_layout 을 다시 호출하라."
            )
        return "레이아웃을 접수했다. 더 이상 도구를 호출하지 말고 '완료'라고만 답하라."

    return StructuredTool.from_function(
        func=_submit,
        name="submit_layout",
        description=(
            "시트 구조 분석이 끝났을 때 호출한다. 이 도구를 호출해야 작업이 "
            "완료된다. 인자가 스키마에 맞지 않거나 레이아웃이 시트를 다 덮지 "
            "못하면(값이 있는데 매핑 안 된 열, 어느 표에도 안 들어간 행) "
            "어디가 빠졌는지 알려주며 되돌아오니, 채워서 다시 호출하라."
        ),
        args_schema=SheetLayout,
    )


def build_discover_agent(model, wb, kind: SourceKind, holder: dict):
    """열린 workbook(wb)에 바인딩된 발견 에이전트와 도구 목록을 만든다.

    `holder` 에 최종 제출된 SheetLayout 이 담긴다(에이전트 그래프의 반환값이
    아니라 도구 클로저를 통해 전달된다 — submit_layout 인자가 검증된 시점의
    Pydantic 인스턴스를 그대로 잡아두기 위해서다).
    """
    tools = make_excel_tools(wb) + [_make_submit_tool(holder, wb)]
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
