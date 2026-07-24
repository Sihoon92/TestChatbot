import logging

from app.config import Settings
from app.services.llm_health import check_internal_llm, check_ollama

# 아무도 듣고 있지 않은 로컬 포트 - 연결이 즉시 거부되어 빠르게 실패한다.
_UNREACHABLE = "http://127.0.0.1:59999"


async def test_check_ollama_logs_warning_on_connection_failure(caplog):
    settings = Settings(llm_backend="ollama", ollama_base_url=_UNREACHABLE)
    with caplog.at_level(logging.WARNING):
        result = await check_ollama(settings)
    assert result["ok"] is False
    assert any("ollama" in rec.message.lower() for rec in caplog.records)


async def test_check_internal_llm_logs_warning_on_connection_failure(caplog):
    settings = Settings(llm_backend="internal", internal_llm_base_url=_UNREACHABLE, internal_llm_model="gpt")
    with caplog.at_level(logging.WARNING):
        result = await check_internal_llm(settings)
    assert result["ok"] is False
    assert any("internal" in rec.message.lower() for rec in caplog.records)


async def test_check_internal_llm_logs_warning_when_base_url_missing(caplog):
    settings = Settings(llm_backend="internal", internal_llm_base_url="")
    with caplog.at_level(logging.WARNING):
        result = await check_internal_llm(settings)
    assert result["ok"] is False
    assert len(caplog.records) >= 1
