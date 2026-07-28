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


def test_ingest_paths_resolve_against_backend_root(tmp_path):
    """상대경로는 cwd 가 아니라 backend/ 기준으로 풀려야 한다.
    실행 위치에 따라 다른 폴더를 보면 '왜 파일을 못 찾는지' 디버깅이 폭발한다."""
    from pathlib import Path
    from app.config import Settings

    s = Settings(ingest_root="./data/uploads", molds_db_path="./molds.db")
    backend_root = Path(__file__).resolve().parents[1]

    assert Path(s.resolved_ingest_root) == backend_root / "data" / "uploads"
    assert Path(s.resolved_molds_db_path) == backend_root / "molds.db"


def test_ingest_paths_keep_absolute_as_is(tmp_path):
    from app.config import Settings

    s = Settings(ingest_root=str(tmp_path), molds_db_path=str(tmp_path / "x.db"))
    assert s.resolved_ingest_root == str(tmp_path)
    assert s.resolved_molds_db_path == str(tmp_path / "x.db")
