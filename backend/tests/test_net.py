import os

import pytest

from app.config import Settings
from app.net import apply_proxy_bypass

_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy")


@pytest.fixture
def proxy_env_set():
    """모든 프록시 환경변수를 세팅해두고, 테스트 후 원상 복구한다."""
    saved = {var: os.environ.get(var) for var in _PROXY_VARS}
    for var in _PROXY_VARS:
        os.environ[var] = "http://proxy.example.com:8080"
    yield
    for var, value in saved.items():
        if value is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = value


def test_internal_backend_clears_proxy_even_without_bypass_flag(proxy_env_set):
    settings = Settings(llm_backend="internal", bypass_proxy=False)
    cleared = apply_proxy_bypass(settings)
    assert cleared  # 최소 하나 이상 제거됨(대소문자 구분 없는 OS에서는 일부만 별개 항목일 수 있음)
    for var in _PROXY_VARS:
        assert var not in os.environ


def test_ollama_backend_leaves_proxy_untouched_without_bypass_flag(proxy_env_set):
    settings = Settings(llm_backend="ollama", bypass_proxy=False)
    cleared = apply_proxy_bypass(settings)
    assert cleared == []
    for var in _PROXY_VARS:
        assert os.environ[var] == "http://proxy.example.com:8080"


def test_bypass_proxy_flag_clears_regardless_of_backend(proxy_env_set):
    settings = Settings(llm_backend="ollama", bypass_proxy=True)
    cleared = apply_proxy_bypass(settings)
    assert cleared
    for var in _PROXY_VARS:
        assert var not in os.environ
