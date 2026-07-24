import os

from app.config import Settings

# requests/httpx/openai 가 참조하는 프록시 환경변수 (대소문자 모두)
_PROXY_VARS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "http_proxy",
    "https_proxy",
    "ALL_PROXY",
    "all_proxy",
)


def resolve_ssl_verify(settings: Settings):
    """httpx/openai 의 `verify` 인자 값을 계산한다.

    - internal_llm_ca_bundle 가 있으면 그 CA 번들 경로 사용(검증 유지, 권장)
    - internal_llm_verify_ssl=False 면 False (검증 끔, 비보안)
    - 그 외에는 True (기본 신뢰저장소)

    반환: bool | str (httpx 의 verify 인자가 받는 형식)
    """
    if settings.internal_llm_ca_bundle:
        return settings.internal_llm_ca_bundle
    if not settings.internal_llm_verify_ssl:
        return False
    return True


def apply_proxy_bypass(settings: Settings) -> list[str]:
    """사내 프록시를 우회하도록 HTTP(S)_PROXY 환경변수를 제거한다.

    사내 내부 LLM 엔드포인트는 회사 프록시를 거치면 도달하지 못하는 경우가 많다.
    llm_backend="internal" 이면 프록시 없이는 연결이 아예 안 되므로 항상 우회하고,
    그 외 백엔드(ollama)에서는 bypass_proxy=true 일 때만 우회한다.
    우회 시 프로세스의 프록시 환경변수를 비워(= 수동으로 `set HTTPS_PROXY=` 한 것과 동일),
    httpx/openai 등 모든 HTTP 클라이언트가 프록시 없이 직접 연결하게 한다.

    HTTP 클라이언트가 만들어지기 전(startup)에 호출해야 효과가 있다.
    실제로 제거한 변수명 목록을 반환한다(로깅용).
    """
    if not (settings.llm_backend == "internal" or settings.bypass_proxy):
        return []
    cleared: list[str] = []
    for var in _PROXY_VARS:
        if os.environ.pop(var, None) is not None:
            cleared.append(var)
    return cleared
