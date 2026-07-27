"""프롬프트 구성과 LangChain vs LangGraph 를 코드로 비교해보는 실습 (한 파일).

교육자료 "프롬프트와 LangGraph" 의 실습용 예제다. 문자열 하나로 묻는 것에서 시작해,
프롬프트 템플릿 -> 멀티턴 대화를 LangChain 만으로 손수 관리 -> 같은 걸 LangGraph 로
넘겨 상태 관리를 위임하는 순서로 4단계를 거친다.

실행 (backend/ 에서, venv 파이썬으로):
    python examples/prompt_and_graph.py            # 전체 4단계
    python examples/prompt_and_graph.py --step 2    # 특정 단계만
    python examples/prompt_and_graph.py --backend ollama

설치: langchain-openai, langchain-ollama, langchain-core, langgraph, httpx
     (백엔드 의존성에 이미 포함되어 있어 추가 설치 불필요)

설정: backend/.env 에 LLM_BACKEND 와 해당 백엔드 값을 채운다.
"""
import argparse
import os
import sys
from pathlib import Path
from typing import Annotated, TypedDict

# Windows UTF-8 output encoding
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

import httpx
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

# 디버그 로깅(선택). 이 예제는 app 패키지에 의존하지 않는 단일 파일이 원칙이지만,
# 이 저장소 안에서 실행할 때는 LLM 입출력을 backend/logs/llm_calls.log 에 남겨두면
# 실습에 도움이 된다(각 step 이 무엇을 모델에 보냈는지 그대로 볼 수 있다). import 가
# 실패하면 빈 config 를 돌려주고 예제는 그대로 동작한다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
try:
    from app.observability import debug_run_config
except Exception:  # noqa: BLE001
    def debug_run_config(script: str) -> dict:  # type: ignore[misc]
        return {}


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

# 사내망 대응: 회사 프록시 우회 (내부 엔드포인트는 프록시를 거치면 못 닿는 경우가 많다)
for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(var, None)


def build_model(backend: str | None = None):
    """LLM_BACKEND(또는 --backend 인자)에 따라 ChatOpenAI/ChatOllama 를 만든다.

    app.llm.get_chat_model 과 같은 분기를 프레임워크 없이 손수 보여준다
    (이 예제는 app 패키지에 의존하지 않는 단일 파일이므로).
    """
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


def step_1(model) -> None:
    """Step 1: 문자열 하나만 던지기 - 역할 지정 없이"""
    reply = model.invoke("자기소개해줘", config=debug_run_config("prompt_and_graph/step_1"))
    print(reply.content)


def step_2(model) -> None:
    """Step 2: ChatPromptTemplate 로 역할 · 변수를 구조화"""
    template = ChatPromptTemplate.from_messages(
        [
            ("system", "너는 {role} 전문가야. 항상 한국어로 간결하게 답해."),
            ("human", "{topic} 을 한 문장으로 설명해줘."),
        ]
    )
    chain = template | model
    reply = chain.invoke(
        {"role": "데이터 분석", "topic": "이상치 탐지"},
        config=debug_run_config("prompt_and_graph/step_2"),
    )
    print(reply.content)


def step_3(model) -> None:
    """Step 3: LangChain만으로 멀티턴 - 히스토리를 손으로 append"""
    cfg = debug_run_config("prompt_and_graph/step_3")
    messages = [SystemMessage(content="너는 친절한 한국어 비서야.")]

    messages.append(HumanMessage(content="LangGraph가 뭐야? 한 문장으로."))
    reply = model.invoke(messages, config=cfg)
    messages.append(AIMessage(content=reply.content))
    print("[1턴]", reply.content)

    messages.append(HumanMessage(content="방금 답을 더 짧게 줄여줘."))
    reply = model.invoke(messages, config=cfg)
    messages.append(AIMessage(content=reply.content))
    print("[2턴]", reply.content)

    print(
        f"\n(대화가 길어질수록 messages 리스트를 매번 손으로 관리해야 한다: "
        f"현재 {len(messages)}개)"
    )


class ChatState(TypedDict):
    messages: Annotated[list, add_messages]


def step_4(model) -> None:
    """Step 4: 같은 대화를 LangGraph 로 - 상태(히스토리)를 대신 관리"""

    def chat_node(state: ChatState) -> dict:
        response = model.invoke(state["messages"])
        return {"messages": [response]}

    graph = StateGraph(ChatState)
    graph.add_node("chat", chat_node)
    graph.add_edge(START, "chat")
    graph.add_edge("chat", END)
    app = graph.compile(checkpointer=MemorySaver())

    # 디버그 로깅 콜백을 같은 config 에 합친다(thread_id 와 키가 겹치지 않는다).
    config = {
        "configurable": {"thread_id": "demo"},
        **debug_run_config("prompt_and_graph/step_4"),
    }
    out = app.invoke(
        {"messages": [HumanMessage(content="LangGraph가 뭐야? 한 문장으로.")]}, config
    )
    print("[1턴]", out["messages"][-1].content)

    out = app.invoke(
        {"messages": [HumanMessage(content="방금 답을 더 짧게 줄여줘.")]}, config
    )
    print("[2턴]", out["messages"][-1].content)

    print(
        f"\n(messages 를 손으로 append 하지 않았다 - thread_id 로 이전 턴이 자동 "
        f"복원됐다: 현재 {len(out['messages'])}개)"
    )


STEPS = {1: step_1, 2: step_2, 3: step_3, 4: step_4}


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
