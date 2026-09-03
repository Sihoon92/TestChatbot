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


def test_resolved_master_xlsx_path_relative_to_backend_root():
    from pathlib import Path
    from app.config import Settings

    s = Settings(master_xlsx_path="./data/관리자/금형현황.xlsx")
    resolved = Path(s.resolved_master_xlsx_path)
    assert resolved.is_absolute()
    backend_root = Path(__file__).resolve().parents[1]
    assert resolved == backend_root / "data" / "관리자" / "금형현황.xlsx"


def test_resolved_master_xlsx_path_keeps_absolute(tmp_path):
    from app.config import Settings

    abs_path = str(tmp_path / "master.xlsx")
    s = Settings(master_xlsx_path=abs_path)
    assert s.resolved_master_xlsx_path == abs_path


def test_stage_dir_map_normal_case():
    from app.config import Settings

    s = Settings(ingest_stage_dirs="MES:mes,IQC:iqc")
    assert s.stage_dir_map == {"MES": "mes", "IQC": "iqc"}


def test_stage_dir_map_strips_whitespace():
    from app.config import Settings

    s = Settings(ingest_stage_dirs=" MES : mes , IQC:iqc ")
    assert s.stage_dir_map == {"MES": "mes", "IQC": "iqc"}


def test_stage_dir_map_ignores_missing_colon():
    from app.config import Settings

    s = Settings(ingest_stage_dirs="MES:mes,쓰레기")
    assert s.stage_dir_map == {"MES": "mes"}


def test_stage_dir_map_ignores_empty_names_or_kinds():
    from app.config import Settings

    s = Settings(ingest_stage_dirs=":mes,IQC:")
    assert s.stage_dir_map == {}


def test_stage_dir_map_empty_string_returns_empty_dict():
    from app.config import Settings

    s = Settings(ingest_stage_dirs="")
    assert s.stage_dir_map == {}


def test_stage_dir_map_ignores_extra_colons():
    """콜론이 2개 이상인 항목은 무시 (유효 종류는 닫힌 어휘이므로 mes:extra는 유효하지 않음)."""
    from app.config import Settings

    s = Settings(ingest_stage_dirs="MES:mes:extra,IQC:iqc")
    assert s.stage_dir_map == {"IQC": "iqc"}


def test_coating_line_speed_default_is_35():
    """라인 속도는 설비 고정값(전 제품 공통)이다. 기본값이 코드에 있어야
    .env 에 그 줄이 없는 환경에서도 같은 값을 본다 — 설정의 단일 출처 규칙."""
    assert Settings(_env_file=None).coating_line_speed_mpm == 35.0
