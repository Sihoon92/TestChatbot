"""사내 gemma 모델의 tool calling 사전점검 스크립트 (일회용).

엑셀 분석 에이전트를 만들기 전에, 사내 LLM(gemma)이 우리 스택
(ChatOpenAI 호환 게이트웨이 + LangChain/LangGraph)에서 실제로 tool calling 을
하는지 확인한다. 여기서 실패하면 ReAct 에이전트 설계 전체를 다시 잡아야 하므로,
가장 먼저 검증한다.

세 단계로 확인한다:
  [1] 저수준   : model.bind_tools() 후 응답에 tool_calls 가 실제로 담기는지
  [2] 대표     : langgraph create_react_agent 가 도구를 호출해 최종 답을 내는지
  [3] 엑셀왕복 : 프롬프트로 특정 도구를 지시했을 때, xlwings 엑셀 도구(write→read)를
                실제로 호출해 셀에 값이 기록/조회되는지 (실사용과 가장 가까운 검증)

실행 (backend/ 에서, venv 파이썬으로):
    python examples/verify_tool_calling.py
    python examples/verify_tool_calling.py --backend internal   # .env 무시하고 강제
    python examples/verify_tool_calling.py --backend ollama

주의: [3] 은 xlwings 설치 + 이 PC 에 Microsoft Excel 이 필요하다. 없으면 SKIP 한다.
    pip install xlwings
"""
import sys
from pathlib import Path

# examples/ 에서 실행해도 app 패키지를 import 할 수 있게 backend/ 를 경로에 추가한다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from langchain_core.messages import HumanMessage  # noqa: E402
from langchain_core.tools import tool  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.llm import get_chat_model  # noqa: E402


@tool
def multiply(a: int, b: int) -> int:
    """두 정수 a, b 를 곱한 값을 돌려준다. 곱셈이 필요하면 반드시 이 도구를 써라."""
    return a * b


def _parse_backend(argv: list[str]) -> str | None:
    for i, a in enumerate(argv):
        if a == "--backend" and i + 1 < len(argv):
            return argv[i + 1]
    return None


def _tool_names(messages) -> list[str]:
    """대화 메시지에서 실제로 호출된 도구 이름들을 뽑는다."""
    names: list[str] = []
    for m in messages:
        for call in getattr(m, "tool_calls", None) or []:
            names.append(call.get("name", "?"))
    return names


def main() -> int:
    backend = _parse_backend(sys.argv[1:])
    settings = get_settings()
    if backend:
        settings = settings.model_copy(update={"llm_backend": backend})

    print("=" * 64)
    print("llm_backend =", settings.llm_backend)
    print("model       =", settings.active_model)
    print("=" * 64)

    try:
        model = get_chat_model(settings)
    except Exception as exc:  # noqa: BLE001
        print(f"[중단] 모델 생성 실패: {exc}")
        return 2

    ok_bind = _check_bind_tools(model)
    ok_agent = _check_react_agent(model)
    ok_excel = _check_excel_tools(model)  # True / False / None(SKIP)

    def mark(v):
        if v is None:
            return "SKIP ⏭️  (Excel/xlwings 없음)"
        return "PASS ✅" if v else "FAIL ❌"

    print("=" * 64)
    print(f"[1] bind_tools tool_calls 생성   : {mark(ok_bind)}")
    print(f"[2] ReAct 에이전트 도구 사용     : {mark(ok_agent)}")
    print(f"[3] 엑셀 도구(write→read) 왕복   : {mark(ok_excel)}")
    print("=" * 64)

    core_ok = bool(ok_bind and ok_agent)
    excel_ok = ok_excel is True or ok_excel is None  # 실행됐으면 통과여야, 스킵은 보류
    if core_ok and excel_ok:
        if ok_excel is None:
            print("결론: tool calling 사용 가능. 단, 엑셀 왕복[3]은 SKIP 됐으니 "
                  "Excel + xlwings 있는 PC 에서 한 번 더 확인하라.")
        else:
            print("결론: tool calling + 엑셀 도구 구동 확인 — 엑셀 분석 에이전트를 진행해도 된다.")
        return 0
    print("결론: 확인 실패 — 계획의 '폴백'(덤프+프롬프트) 경로를 검토하라.")
    return 1


def _check_bind_tools(model) -> bool:
    print("\n[1] 저수준 검증: model.bind_tools([multiply]).invoke(...)")
    bound = model.bind_tools([multiply])
    resp = bound.invoke(
        [HumanMessage(content="23 곱하기 7은 얼마야? 반드시 도구를 써서 계산해줘.")]
    )
    tool_calls = getattr(resp, "tool_calls", None) or []
    if tool_calls:
        print("    → tool_calls 감지:", tool_calls)
        return True
    print("    → tool_calls 없음. 모델이 도구 대신 직접 답함:")
    print("      content:", (getattr(resp, "content", "") or "")[:200])
    return False


def _check_react_agent(model) -> bool:
    print("\n[2] 대표 검증: langgraph create_react_agent")
    try:
        from langgraph.prebuilt import create_react_agent
    except Exception as exc:  # noqa: BLE001
        print(f"    → create_react_agent import 실패: {exc}")
        return False

    agent = create_react_agent(model, [multiply])
    result = agent.invoke(
        {"messages": [HumanMessage(content="23 곱하기 7을 도구로 계산해서 숫자로 알려줘.")]}
    )
    msgs = result["messages"]
    used = _tool_names(msgs)
    final = getattr(msgs[-1], "content", "")
    print("    → 호출된 도구:", used or "(없음)")
    print("    → 최종 답변:", (final or "")[:200])
    # 도구를 실제로 거쳤고 정답(161)이 답변에 있으면 성공으로 본다.
    return bool(used) and "161" in str(final)


def _make_excel_roundtrip_tools(sheet) -> list:
    """열린 xlwings 시트에 바인딩된 read/write 도구 한 쌍."""

    @tool
    def excel_write_cell(address: str, value: str) -> str:
        """엑셀 시트의 특정 셀(예 'B2')에 값을 기록한다."""
        sheet.range(address).value = value
        return f"{address} 에 '{value}' 를 기록했다."

    @tool
    def excel_read_cell(address: str) -> str:
        """엑셀 시트의 특정 셀(예 'B2') 값을 읽어 돌려준다."""
        return f"{address} = {sheet.range(address).value}"

    return [excel_write_cell, excel_read_cell]


def _check_excel_tools(model):
    """프롬프트로 특정 엑셀 도구를 지시해, 실제 Excel 셀에 write→read 가 되는지 확인.

    반환: True(성공) / False(실패) / None(Excel·xlwings 없어 SKIP).
    """
    print("\n[3] 엑셀 왕복 검증: xlwings write→read 도구를 프롬프트로 지시")
    try:
        import xlwings as xw
    except Exception:  # noqa: BLE001
        print("    → SKIP: xlwings 미설치 (pip install xlwings)")
        return None
    try:
        from langgraph.prebuilt import create_react_agent
    except Exception as exc:  # noqa: BLE001
        print(f"    → create_react_agent import 실패: {exc}")
        return False

    try:
        app = xw.App(visible=False, add_book=False)
    except Exception as exc:  # noqa: BLE001
        print(f"    → SKIP: Excel COM 사용 불가 ({exc})")
        return None

    app.display_alerts = False
    app.screen_updating = False
    book = None
    try:
        book = app.books.add()
        sheet = book.sheets[0]
        tools = _make_excel_roundtrip_tools(sheet)
        agent = create_react_agent(model, tools)

        token = "엑셀툴검증OK"
        prompt = (
            f"먼저 excel_write_cell 도구로 셀 B2 에 '{token}' 를 기록해라. "
            "그다음 excel_read_cell 도구로 B2 를 읽어 그 값을 그대로 알려줘. "
            "반드시 두 도구를 순서대로 사용해라."
        )
        result = agent.invoke({"messages": [HumanMessage(content=prompt)]})
        msgs = result["messages"]
        used = _tool_names(msgs)
        cell_value = sheet.range("B2").value  # 실제 Excel 셀에 기록됐는지 직접 확인
        final = getattr(msgs[-1], "content", "")

        print("    → 호출된 도구:", used or "(없음)")
        print("    → 실제 B2 셀 값:", cell_value)
        print("    → 최종 답변:", (final or "")[:200])

        wrote = "excel_write_cell" in used
        read = "excel_read_cell" in used
        persisted = cell_value == token
        answered = token in str(final)
        if wrote and read and persisted and answered:
            return True
        # 부분 성공 진단 힌트
        print("    → 진단: write호출={} read호출={} 셀기록={} 답변반영={}".format(
            wrote, read, persisted, answered))
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"    → 실행 중 예외: {exc}")
        return False
    finally:
        try:
            if book is not None:
                book.close()
        finally:
            app.quit()


if __name__ == "__main__":
    raise SystemExit(main())
