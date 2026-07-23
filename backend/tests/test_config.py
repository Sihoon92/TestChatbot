from app.config import Settings


def test_cors_origin_list_splits_and_strips():
    s = Settings(cors_origins="http://a.com, http://b.com ,")
    assert s.cors_origin_list == ["http://a.com", "http://b.com"]


def test_active_model_ollama():
    s = Settings(llm_backend="ollama", ollama_model="gemma3n:e4b")
    assert s.active_model == "gemma3n:e4b"


def test_active_model_internal():
    s = Settings(llm_backend="internal", internal_llm_model="gpt-4o-mini")
    assert s.active_model == "gpt-4o-mini"
