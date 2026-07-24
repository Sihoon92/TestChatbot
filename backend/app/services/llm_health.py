import logging

import httpx

from app.config import Settings
from app.net import resolve_ssl_verify

logger = logging.getLogger(__name__)


async def check_llm(settings: Settings) -> dict:
    """현재 활성 백엔드(llm_backend)에 맞는 연결 상태를 반환한다.

    응답 형태는 백엔드와 무관하게 {ok, models, error} 로 통일한다(프론트 호환).
    """
    if settings.llm_backend == "internal":
        return await check_internal_llm(settings)
    return await check_ollama(settings)


async def check_ollama(settings: Settings) -> dict:
    url = f"{settings.ollama_base_url.rstrip('/')}/api/tags"
    headers = {}
    if settings.ollama_api_key:
        headers["Authorization"] = f"Bearer {settings.ollama_api_key}"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code >= 400:
            logger.warning("ollama health check failed: url=%s status=%s", url, resp.status_code)
            return {"ok": False, "models": [], "error": f"HTTP {resp.status_code}"}
        data = resp.json()
        models = [m["name"] for m in data.get("models", []) if m.get("name")]
        return {"ok": True, "models": models, "error": None}
    except Exception as exc:  # noqa: BLE001 - surface any connectivity problem to the UI
        logger.warning("ollama health check failed: url=%s error=%s", url, exc)
        return {"ok": False, "models": [], "error": str(exc)}


async def check_internal_llm(settings: Settings) -> dict:
    """사내 OpenAI 호환 API 연결 확인 (GET {base_url}/models)."""
    if not settings.internal_llm_base_url:
        logger.warning("internal LLM health check skipped: INTERNAL_LLM_BASE_URL not set")
        return {"ok": False, "models": [], "error": "INTERNAL_LLM_BASE_URL 미설정"}
    url = f"{settings.internal_llm_base_url.rstrip('/')}/models"
    headers = {}
    if settings.internal_llm_api_key:
        headers["Authorization"] = f"Bearer {settings.internal_llm_api_key}"
    try:
        async with httpx.AsyncClient(timeout=5.0, verify=resolve_ssl_verify(settings)) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code >= 400:
            logger.warning("internal LLM health check failed: url=%s status=%s", url, resp.status_code)
            return {"ok": False, "models": [], "error": f"HTTP {resp.status_code}"}
        data = resp.json()
        models = [m["id"] for m in data.get("data", []) if m.get("id")]
        return {"ok": True, "models": models, "error": None}
    except Exception as exc:  # noqa: BLE001 - surface any connectivity problem to the UI
        logger.warning("internal LLM health check failed: url=%s error=%s", url, exc)
        return {"ok": False, "models": [], "error": str(exc)}
