"""사내 LLM에 가장 단순하게 연결해보는 예제 (한 파일).

교육자료 "사내 LLM 연결 & LangChain 입문" 의 실습용 예제다. 이 프로젝트의
app/llm.py(get_chat_model)가 하는 일을, 프레임워크 없이 압축해 보여준다.

실행:  python backend/examples/connect_llm.py
설치:  pip install langchain-openai httpx   (백엔드 의존성에 이미 포함)

설정: backend/.env 에 아래 값을 채우거나 환경변수로 지정한다.
  INTERNAL_LLM_BASE_URL   # 예: https://llm.company.com/v1
  INTERNAL_LLM_MODEL      # 예: gpt-4o-mini
  INTERNAL_LLM_API_KEY    # 게이트웨이가 요구할 때만

사내 엔드포인트가 없어도 로컬 Ollama 의 OpenAI 호환 API 로 바로 실습할 수 있다:
  INTERNAL_LLM_BASE_URL=http://localhost:11434/v1
  INTERNAL_LLM_MODEL=gemma3n:e4b
"""
import os
from pathlib import Path

import httpx
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage


# 0) backend/.env 를 직접 읽어 환경변수로 채운다. 이 예제는 앱과 달리 프레임워크
#    (pydantic-settings) 없이 동작하므로, 같은 .env 를 손수 로드해 앱과 동일한
#    설정을 쓰게 한다. 이미 셸에 지정된 환경변수는 덮어쓰지 않는다.
def load_env_file() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"  # backend/.env
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


load_env_file()

# 필수 설정 확인 — 없으면 원인 모를 KeyError 대신 무엇을 채워야 하는지 안내한다.
missing = [k for k in ("INTERNAL_LLM_BASE_URL", "INTERNAL_LLM_MODEL") if not os.environ.get(k)]
if missing:
    raise SystemExit(
        "사내 LLM 설정이 비어 있습니다: " + ", ".join(missing) + "\n"
        "backend/.env 에 값을 채우거나 환경변수로 지정하세요. 예시:\n"
        "  INTERNAL_LLM_BASE_URL=https://llm.company.com/v1\n"
        "  INTERNAL_LLM_MODEL=gpt-4o-mini\n"
        "사내 엔드포인트가 없으면 로컬 Ollama 로 실습할 수 있습니다:\n"
        "  INTERNAL_LLM_BASE_URL=http://localhost:11434/v1\n"
        "  INTERNAL_LLM_MODEL=gemma3n:e4b"
    )

# 1) 사내망 대응: 회사 프록시 우회 (내부 엔드포인트는 프록시를 거치면 못 닿는 경우가 많다)
for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(var, None)

# 2) LLM 클라이언트 생성 — OpenAI 호환 게이트웨이를 base_url 로 가리킨다
llm = ChatOpenAI(
    base_url=os.environ["INTERNAL_LLM_BASE_URL"],
    api_key=os.environ.get("INTERNAL_LLM_API_KEY", "not-needed"),
    model=os.environ["INTERNAL_LLM_MODEL"],
    http_client=httpx.Client(verify=False),  # 사설 CA로 검증이 막힐 때 임시 우회(비보안)
)

# 3) 인풋: 역할(role)이 있는 메시지 리스트
messages = [
    SystemMessage(content="너는 친절한 한국어 비서야."),
    HumanMessage(content="사내 LLM 연결을 한 문장으로 설명해줘."),
]

# 4) 실행 & 아웃풋
reply = llm.invoke(messages)
print(reply.content)
