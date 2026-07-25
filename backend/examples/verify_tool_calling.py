"""사내 gemma 모델의 tool calling 사전점검 스크립트 (일회용).

엑셀 분석 에이전트를 만들기 전에, 사내 LLM(gemma)이 우리 스택
(ChatOpenAI 호환 게이트웨이 + LangChain/LangGraph)에서 실제로 tool calling 을
하는지 확인한다. 여기서 실패하면 ReAct 에이전트 설계 전체를 다시 잡아야 하므로,
가장 먼저 검증한다.

세 단계로 확인한다:
  [1] 저수준   : model.bind_tools() 후 응답에 tool_calls 가 실제로 담기는지
  [2] 대표     : langgraph create_react_agent 가 도구를 호출해 최종 답을 내는지
  [3] 엑셀왕복 : `app.excel.workbook.open_workbook` 으로 연 워크북에 바인딩된
                `app.excel.tools.make_excel_tools` 의 실제 읽기 전용 도구
                (read_range)를 프롬프트로 지시했을 때, 에이전트가 실제로 그
                도구를 호출해 미리 셀에 심어둔 값을 정확히 읽어오는지 (open한
                스레드와 LangGraph 가 도구를 실행하는 스레드가 다른 실사용
                경로를 그대로 재현하는, 실사용과 가장 가까운 검증)

실행 (backend/ 에서, venv 파이썬으로):
    python examples/verify_tool_calling.py
    python examples/verify_tool_calling.py --backend internal   # .env 무시하고 강제
    python examples/verify_tool_calling.py --backend ollama

주의: [3] 은 xlwings 설치 + 이 PC 에 Microsoft Excel 이 필요하다. 없으면 SKIP 한다.
    pip install xlwings
"""
import shutil
import sys
import tempfile
from pathlib import Path

# examples/ 에서 실행해도 app 패키지를 import 할 수 있게 backend/ 를 경로에 추가한다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

# Windows 콘솔 코드페이지(cp949 등)에서도 print() 의 이모지(✅/❌/⏭️)가
# UnicodeEncodeError 로 죽지 않도록, stdout/stderr 를 UTF-8 로 강제한다.
# (원인: 콘솔 코드페이지가 cp949 면 기본 stdout 인코딩도 cp949 라 그 밖의
# 문자를 인코딩하지 못한다. UTF-8 로 재설정하면 문자 자체는 그대로 인코딩되고
# 실제 화면 렌더링만 터미널/폰트 설정에 달렸을 뿐, 정보 손실은 없다.)
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name)
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from langchain_core.messages import HumanMessage  # noqa: E402
from langchain_core.tools import tool  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.excel.tools import make_excel_tools  # noqa: E402
from app.excel.workbook import open_workbook  # noqa: E402
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
    print(f"[3] 엑셀 도구(open_workbook 경유) 왕복 : {mark(ok_excel)}")
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


def _seed_workbook_with_token(path: str, token: str) -> None:
    """검증용 임시 워크북을 만들어 시트 '데이터'의 B2 에 token 을 써 넣는다.

    이 App/Book 은 여기서 만들고 여기서 끝낸다 — `open_workbook` 이 열 파일을
    준비하는 별도의 (그리고 open_workbook 과는 무관한) 단계일 뿐, 아래
    `_check_excel_tools` 가 검증하는 실제 스택(open_workbook + 실제 도구)과는
    섞이지 않는다.
    """
    import xlwings as xw

    app = xw.App(visible=False, add_book=False)
    app.display_alerts = False
    app.screen_updating = False
    try:
        book = app.books.add()
        sheet = book.sheets[0]
        sheet.name = "데이터"
        sheet.range("B2").value = token
        book.save(path)
        book.close()
    finally:
        app.quit()


def _check_excel_tools(model):
    """`open_workbook` + `app.excel.tools` 의 실제 읽기 전용 도구가 프롬프트로
    지시했을 때 실제로 호출되어, 미리 심어둔 셀 값을 정확히 읽어오는지 확인한다.

    과거 버전은 이 사전점검이 직접 xw.App/book/sheet 를 메인 스레드에서 만들어
    도구 클로저에 그대로 물려주고, 에이전트가 그 도구를 (LangGraph 내부)
    워커 스레드에서 호출하게 했다 — 이는 `open_workbook` 이 고치는 바로 그
    "COM 포인터를 만든 스레드와 쓰는 스레드가 다르면 실패한다" 는 문제를
    사전점검 스크립트 스스로 재현하는 코드였고, 그래서 이 단계는 실제로는
    -2147221008 로 실패했어야 했다(하지만 이 스크립트가 그 사실을 드러내지
    못했다). 여기서는 실제 운영 경로와 동일하게 `open_workbook` 으로 워크북을
    열고, `make_excel_tools` 가 만드는 실제 읽기 전용 도구를 그대로 에이전트에
    바인딩한다 — write 도구는 실제 운영 도구 세트에 없으므로(읽기 전용 계약),
    "왕복"은 파일 준비 단계에서 미리 셀에 값을 심어두고, 에이전트가 실제
    read_range 도구로 그 값을 읽어내는지로 검증한다.

    반환: True(성공) / False(실패) / None(Excel·xlwings 없어 SKIP).
    """
    print("\n[3] 엑셀 왕복 검증: open_workbook + 실제 read_range 도구를 프롬프트로 지시")
    try:
        import xlwings as xw  # noqa: F401  (설치 여부만 확인)
    except Exception:  # noqa: BLE001
        print("    → SKIP: xlwings 미설치 (pip install xlwings)")
        return None
    try:
        from langgraph.prebuilt import create_react_agent
    except Exception as exc:  # noqa: BLE001
        print(f"    → create_react_agent import 실패: {exc}")
        return False

    token = "엑셀툴검증OK"
    tmp_dir = tempfile.mkdtemp(prefix="verify_tool_calling_")
    path = str(Path(tmp_dir) / "roundtrip.xlsx")
    try:
        _seed_workbook_with_token(path, token)
    except Exception as exc:  # noqa: BLE001
        print(f"    → SKIP: Excel COM 사용 불가 ({exc})")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return None

    try:
        with open_workbook(path) as wb:
            tools = make_excel_tools(wb)
            agent = create_react_agent(model, tools)

            prompt = (
                "엑셀 시트 '데이터'의 B2:B2 범위를 read_range 도구로 읽어서 "
                "그 값을 그대로 알려줘. 반드시 read_range 도구를 사용해라."
            )
            result = agent.invoke({"messages": [HumanMessage(content=prompt)]})
            msgs = result["messages"]
            used = _tool_names(msgs)
            final = getattr(msgs[-1], "content", "")

            print("    → 호출된 도구:", used or "(없음)")
            print("    → 최종 답변:", (final or "")[:200])

            read = "read_range" in used
            answered = token in str(final)
            if read and answered:
                return True
            # 부분 성공 진단 힌트
            print(f"    → 진단: read_range호출={read} 답변반영={answered}")
            return False
    except Exception as exc:  # noqa: BLE001
        print(f"    → 실행 중 예외: {exc}")
        return False
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
