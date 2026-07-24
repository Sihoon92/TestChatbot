import os
from pathlib import Path

from app.config import Settings


def test_resolved_debug_log_path_anchors_relative_to_backend_root():
    s = Settings(debug_log_path="./logs/llm_calls.log")
    resolved = Path(s.resolved_debug_log_path)
    assert resolved.is_absolute()
    # backend/ 루트(= app 패키지의 부모) 기준으로 해석돼야 한다.
    backend_root = Path(__file__).resolve().parents[1]
    assert resolved == backend_root / "logs" / "llm_calls.log"


def test_resolved_debug_log_path_keeps_absolute(tmp_path):
    abs_path = str(tmp_path / "x.log")
    s = Settings(debug_log_path=abs_path)
    assert os.path.normpath(s.resolved_debug_log_path) == os.path.normpath(abs_path)


def test_cors_origin_list_splits_and_strips():
    s = Settings(cors_origins="http://a.com, http://b.com ,")
    assert s.cors_origin_list == ["http://a.com", "http://b.com"]


def test_active_model_ollama():
    s = Settings(llm_backend="ollama", ollama_model="gemma3n:e4b")
    assert s.active_model == "gemma3n:e4b"


def test_active_model_internal():
    s = Settings(llm_backend="internal", internal_llm_model="gpt-4o-mini")
    assert s.active_model == "gpt-4o-mini"
