"""tool-calling 을 원리부터 단계별로 뜯어보는 실습 (한 파일).

교육자료 "Tool calling" 의 실습용 예제다. LLM 이 큰 수 곱셈을 틀리는 것에서
시작해, 도구 정의 -> bind_tools 로 tool_calls 확인 -> 수동 왕복 루프 ->
create_react_agent 한 줄 -> 엑셀 셀 write/read 도구까지 6단계로 tool calling 의
원리를 보여준다.

실행 (backend/ 에서, venv 파이썬으로):
    python examples/tool_calling_step_by_step.py            # 전체 6단계
    python examples/tool_calling_step_by_step.py --step 3    # 특정 단계만
    python examples/tool_calling_step_by_step.py --backend internal

설치: langchain-openai, langchain-ollama, langchain-core, langgraph, httpx
     (백엔드 의존성에 이미 포함) / Step 6 만 선택적으로 xlwings 필요
     (pip install xlwings, 이 PC 에 Microsoft Excel 설치 필요 - 없으면 SKIP)
"""
import argparse
import os
import sys
from pathlib import Path

# Windows UTF-8 output encoding
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

import httpx
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

# 디버그 로깅(선택). 이 예제는 app 패키지에 의존하지 않는 단일 파일이 원칙이지만,
# 이 저장소 안에서 실행할 때는 LLM 입출력과 도구 호출을 backend/logs/llm_calls.log 에
# 남겨두면 실습에 도움이 된다(특히 step 3~5 에서 "언제 실제로 도구가 실행되는지"를
# 로그로 확인할 수 있다). import 가 실패하면 빈 config 를 돌려주고 예제는 그대로 동작한다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
try:
    from app.observability import debug_run_config
except Exception:  # noqa: BLE001
    def debug_run_config(script: str) -> dict:  # type: ignore[misc]
        return {}

A, B = 4823, 6791  # 암산으로는 못 맞히는 크기 - 도구가 필요한 이유를 보여준다


def load_env_file() -> None:
    """backend/.env 를 읽어 환경변수로 채운다(이미 설정된 값은 덮어쓰지 않음)."""
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


load_env_file()

for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(var, None)


def build_model(backend: str | None = None):
    """LLM_BACKEND(또는 --backend 인자)에 따라 ChatOpenAI/ChatOllama 를 만든다."""
    backend = backend or os.environ.get("LLM_BACKEND", "ollama")
    if backend == "internal":
        missing = [
            k for k in ("INTERNAL_LLM_BASE_URL", "INTERNAL_LLM_MODEL")
            if not os.environ.get(k)
        ]
        if missing:
            raise SystemExit(
                "사내 LLM 설정이 비어 있습니다: " + ", ".join(missing)
                + "\nbackend/.env 를 채우세요."
            )
        return ChatOpenAI(
            base_url=os.environ["INTERNAL_LLM_BASE_URL"],
            api_key=os.environ.get("INTERNAL_LLM_API_KEY", "not-needed"),
            model=os.environ["INTERNAL_LLM_MODEL"],
            http_client=httpx.Client(verify=False),
        )
    return ChatOllama(
        model=os.environ.get("OLLAMA_MODEL", "gemma3n:e4b"),
        base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
    )


@tool
def multiply(a: int, b: int) -> int:
    """두 정수 a, b 를 곱한 값을 돌려준다. 곱셈이 필요하면 반드시 이 도구를 써라."""
    return a * b


def step_1(model) -> None:
    """Step 1: 도구 없이 큰 수 곱셈을 직접 시키기"""
    reply = model.invoke(
        f"{A} 곱하기 {B} 는 얼마야? 숫자만 답해.",
        config=debug_run_config("tool_calling/step_1"),
    )
    correct = A * B
    print(f"모델 답변: {reply.content!r}")
    print(f"정답({correct})이 답변에 포함됐는가: {str(correct) in reply.content}")


def step_2(model) -> None:
    """Step 2: @tool 로 도구를 정의만 해보기 (아직 LLM 에 연결 안 함)"""
    print("도구 이름:", multiply.name)
    print("도구 설명(LLM 이 읽는 부분):", multiply.description)
    print("도구 스키마:", multiply.args)
    # 이 직접 호출도 콜백을 타므로 로그에 TOOL CALL 블록이 남는다 — LLM 없이
    # 우리 코드가 도구를 실행한 것이라는 점이 로그에서도 그대로 드러난다.
    print(
        "직접 호출:",
        multiply.invoke({"a": A, "b": B}, config=debug_run_config("tool_calling/step_2")),
    )


def step_3(model) -> None:
    """Step 3: bind_tools - LLM 이 '부르고 싶다'고 말하는 것을 확인"""
    bound = model.bind_tools([multiply])
    reply = bound.invoke(
        [HumanMessage(content=f"{A} 곱하기 {B} 를 계산해줘. 반드시 도구를 써라.")],
        config=debug_run_config("tool_calling/step_3"),
    )
    print("tool_calls:", reply.tool_calls)
    print("content(비어있을 수 있다 - 아직 계산 안 함):", repr(reply.content))
    print(
        "\n주의: 이 시점까지 실제 곱셈은 한 번도 실행되지 않았다 - "
        "LLM 은 '불러달라'는 요청만 만들었다."
    )


def step_4(model) -> None:
    """Step 4: 수동 왕복 루프 - 도구를 실제로 실행하고 결과를 되돌려준다"""
    cfg = debug_run_config("tool_calling/step_4")
    tools_by_name = {multiply.name: multiply}
    bound = model.bind_tools([multiply])

    messages = [
        HumanMessage(content=f"{A} 곱하기 {B} 를 계산해줘. 반드시 도구를 써라.")
    ]
    ai_msg = bound.invoke(messages, config=cfg)
    messages.append(ai_msg)

    for call in ai_msg.tool_calls:
        result = tools_by_name[call["name"]].invoke(call["args"], config=cfg)
        print(f"[실행] {call['name']}({call['args']}) = {result}")
        messages.append(ToolMessage(content=str(result), tool_call_id=call["id"]))

    final = bound.invoke(messages, config=cfg)
    print("최종 답변:", final.content)


def step_5(model) -> None:
    """Step 5: create_react_agent 한 줄로 Step 4 의 루프를 대체"""
    agent = create_react_agent(model, [multiply])
    result = agent.invoke(
        {
            "messages": [
                HumanMessage(content=f"{A} 곱하기 {B} 를 계산해줘. 반드시 도구를 써라.")
            ]
        },
        config=debug_run_config("tool_calling/step_5"),
    )
    used = [
        c["name"]
        for m in result["messages"]
        for c in (getattr(m, "tool_calls", None) or [])
    ]
    print("호출된 도구:", used)
    print("최종 답변:", result["messages"][-1].content)


def step_6(model) -> None:
    """Step 6: 엑셀 셀 write→read 왕복 - 실무 데이터에도 그대로 적용"""
    try:
        import xlwings as xw
    except ImportError:
        print("SKIP: xlwings 미설치 (pip install xlwings)")
        return

    try:
        app = xw.App(visible=False, add_book=False)
    except Exception as exc:  # noqa: BLE001
        print(f"SKIP: Excel COM 사용 불가 ({exc})")
        return

    app.display_alerts = False
    book = None
    try:
        book = app.books.add()
        sheet = book.sheets[0]

        @tool
        def excel_write_cell(address: str, value: str) -> str:
            """엑셀 시트의 특정 셀(예 'B2')에 값을 기록한다."""
            sheet.range(address).value = value
            return f"{address} 에 '{value}' 를 기록했다."

        @tool
        def excel_read_cell(address: str) -> str:
            """엑셀 시트의 특정 셀(예 'B2') 값을 읽어 돌려준다."""
            return f"{address} = {sheet.range(address).value}"

        agent = create_react_agent(model, [excel_write_cell, excel_read_cell])
        token = "엑셀툴검증OK"
        prompt = (
            f"먼저 excel_write_cell 로 셀 B2 에 '{token}' 를 기록해라. "
            "그다음 excel_read_cell 로 B2 를 읽어 그 값을 그대로 알려줘. "
            "반드시 두 도구를 순서대로 사용해라."
        )
        result = agent.invoke(
            {"messages": [HumanMessage(content=prompt)]},
            config=debug_run_config("tool_calling/step_6"),
        )
        used = [
            c["name"]
            for m in result["messages"]
            for c in (getattr(m, "tool_calls", None) or [])
        ]
        print("호출된 도구:", used)
        print("실제 B2 셀 값:", sheet.range("B2").value)
        print("최종 답변:", result["messages"][-1].content)
    finally:
        try:
            if book is not None:
                book.close()
        finally:
            app.quit()


STEPS = {1: step_1, 2: step_2, 3: step_3, 4: step_4, 5: step_5, 6: step_6}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", type=int, choices=sorted(STEPS), default=None)
    parser.add_argument("--backend", choices=("internal", "ollama"), default=None)
    args = parser.parse_args()

    model = build_model(args.backend)
    steps = [STEPS[args.step]] if args.step else list(STEPS.values())
    for fn in steps:
        print(f"\n{'=' * 60}\n[{fn.__doc__}]\n{'=' * 60}")
        fn(model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
