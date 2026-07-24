"""사내 LLM에 가장 단순하게 연결해보는 예제 (한 파일).

교육자료 "사내 LLM 연결 & LangChain 입문" 의 실습용 예제다. 이 프로젝트의
app/llm.py(get_chat_model)가 하는 일을, 프레임워크 없이 30줄로 압축해 보여준다.

실행:  python backend/examples/connect_llm.py
설치:  pip install langchain-openai httpx   (백엔드 의존성에 이미 포함)
설정(환경변수):
  INTERNAL_LLM_BASE_URL   # 예: https://llm.company.com/v1
  INTERNAL_LLM_API_KEY    # 게이트웨이가 요구할 때만
  INTERNAL_LLM_MODEL      # 예: gpt-4o-mini
"""
import os

import httpx
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

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
