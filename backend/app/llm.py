from typing import Any

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from app.config import Settings
from app.net import resolve_ssl_verify


def get_chat_model(settings: Settings) -> BaseChatModel:
    """설정의 llm_backend 값에 따라 LLM 클라이언트를 생성한다.

    - "ollama"   : 로컬/원격 Ollama (OLLAMA_* 설정)
    - "internal" : 사내 OpenAI 호환 API (INTERNAL_LLM_* 설정)
    """
    if settings.llm_backend == "internal":
        return _internal_model(settings)
    return _ollama_model(settings)


def _ollama_model(settings: Settings) -> BaseChatModel:
    client_kwargs: dict[str, Any] = {}
    if settings.ollama_api_key:
        client_kwargs["headers"] = {"Authorization": f"Bearer {settings.ollama_api_key}"}
    return ChatOllama(
        model=settings.ollama_model,
        base_url=settings.ollama_base_url,
        client_kwargs=client_kwargs or None,
    )


def _internal_model(settings: Settings) -> BaseChatModel:
    if not settings.internal_llm_base_url or not settings.internal_llm_model:
        raise RuntimeError(
            "LLM_BACKEND=internal 에는 INTERNAL_LLM_BASE_URL 과 INTERNAL_LLM_MODEL 이 필요합니다."
        )
    verify = resolve_ssl_verify(settings)
    extra: dict[str, Any] = {}
    if verify is not True:
        # 사내 CA 번들 사용 또는 검증 끄기 — openai SDK 에 커스텀 httpx 클라이언트를 주입
        if verify is False:
            print("[ssl] INTERNAL_LLM_VERIFY_SSL=false → SSL 인증서 검증을 끕니다 (비보안: MITM 위험)")
        else:
            print(f"[ssl] 사내 CA 번들 사용: {verify}")
        extra["http_client"] = httpx.Client(verify=verify)
        extra["http_async_client"] = httpx.AsyncClient(verify=verify)
    return ChatOpenAI(
        model=settings.internal_llm_model,
        base_url=settings.internal_llm_base_url,
        # 키가 필요 없는 게이트웨이도 있으므로 빈 값이면 placeholder 사용
        api_key=settings.internal_llm_api_key or "not-needed",
        **extra,
    )
