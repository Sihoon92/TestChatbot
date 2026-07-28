from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 모든 기본 설정값의 단일 출처. cwd(실행 위치)와 무관하게 항상 backend/.env 를
# 읽도록 절대경로로 고정한다 — 어디서 스크립트/테스트/uvicorn 을 띄우든 동일한
# 설정을 참조하게 하기 위함(상대경로 ".env" 는 cwd 기준이라 위치에 따라 빗나감).
_BACKEND_ROOT = Path(__file__).resolve().parents[1]  # backend/


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_BACKEND_ROOT / ".env", extra="ignore"
    )

    # LLM backend switch: "ollama" | "internal"
    llm_backend: str = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    ollama_api_key: str = ""
    ollama_model: str = "gemma3n:e4b"
    # Internal OpenAI-compatible API (used when llm_backend == "internal")
    internal_llm_base_url: str = ""
    internal_llm_api_key: str = ""
    internal_llm_model: str = ""
    # 사내 TLS: CA 번들(.pem) 경로를 주면 검증 유지(권장). verify_ssl=false 면 검증 끔(비보안)
    internal_llm_ca_bundle: str = ""
    # 기본 false: 사내 CA 검증 불가 환경이 많아 우선 연결이 되도록 함(MITM 위험 감수).
    # CA 번들을 구할 수 있으면 internal_llm_ca_bundle 지정 + true 로 바꾸는 걸 권장.
    internal_llm_verify_ssl: bool = False
    # 사내 프록시 우회: true 면 startup 에서 HTTP(S)_PROXY 환경변수를 비워 직접 연결한다
    bypass_proxy: bool = False
    app_db_path: str = "./app.db"
    cors_origins: str = "http://localhost:5173"
    # LLM 디버그 로깅: 호출마다 입력/출력을 사람이 읽기 좋은 블록으로 파일에 남긴다.
    debug_log_enabled: bool = True
    debug_log_path: str = "./logs/llm_calls.log"
    debug_log_max_bytes: int = 5_000_000  # 5MB, 초과 시 로테이션
    debug_log_backup_count: int = 5
    # 진단용: 로거 내부 동작(콜백 발화·파일핸들러 생성·쓰기 성공)을 콘솔에 추적
    # 출력한다. 로그가 "왜 안 남는지" 파악할 때만 켠다. 기본 off.
    debug_log_verbose: bool = False

    # ── 금형 데이터 수집 ────────────────────────────────────────────────
    # 부서가 엑셀을 올리는 루트. 하위에 폴더별로 나뉜다(ingest_stage_dirs).
    ingest_root: str = "./data/uploads"
    # "폴더명:소스종류" 쌍을 쉼표로. 폴더 이름이 바뀌거나 공유드라이브로
    # 옮겨도 코드를 고치지 않기 위해 설정으로 뺀다.
    ingest_stage_dirs: str = "MES:mes,IQC:iqc,PQC:pqc,설계:design,설치:install,AI복검:ai_recheck"
    # 채팅용 app.db 와 반드시 별도 파일. 같은 파일을 쓰면 LangGraph 체크포인터와
    # SQLite 쓰기 락을 두고 경합해 'database is locked' 가 난다.
    molds_db_path: str = "./molds.db"
    master_xlsx_path: str = "./data/관리자/금형현황.xlsx"
    # 0 = 자동 폴링 끔(수동 트리거만). 1단계는 0 으로 둔다.
    ingest_poll_seconds: int = 0

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def _resolve(self, raw: str) -> str:
        """상대경로를 backend 루트 기준으로 푼다(resolved_debug_log_path 와 동일 규칙)."""
        p = Path(raw)
        if not p.is_absolute():
            p = _BACKEND_ROOT / p
        return str(p)

    @property
    def resolved_ingest_root(self) -> str:
        return self._resolve(self.ingest_root)

    @property
    def resolved_molds_db_path(self) -> str:
        return self._resolve(self.molds_db_path)

    @property
    def resolved_master_xlsx_path(self) -> str:
        return self._resolve(self.master_xlsx_path)

    @property
    def stage_dir_map(self) -> dict[str, str]:
        """'MES:mes,IQC:iqc' → {'MES': 'mes', 'IQC': 'iqc'}.

        잘못된 항목은 조용히 무시:
        - 콜론 없음: "쓰레기" → 제외
        - 빈 이름/종류: ":mes" 또는 "MES:" → 제외
        - 콜론 2개 이상: "MES:mes:extra" → kind 에 ':' 가 남으면 제외 (유효 종류는 6종의 닫힌 어휘)

        전체가 비면 빈 dict 를 돌려준다 — 호출자가 '설정이 비었다'로 처리한다.
        """
        out: dict[str, str] = {}
        for part in self.ingest_stage_dirs.split(","):
            name, sep, kind = part.partition(":")
            name, kind = name.strip(), kind.strip()
            # 유효한 항목만 매핑: 콜론 있고, 이름과 종류가 비지 않았고, 종류에 콜론 없음
            if sep and name and kind and ":" not in kind:
                out[name] = kind
        return out

    @property
    def resolved_debug_log_path(self) -> str:
        """디버그 로그의 실제 기록 경로(절대경로).

        상대경로면 실행 위치(cwd)가 아니라 backend 루트를 기준으로 해석한다.
        이렇게 하면 어디서 uvicorn 을 띄우든 항상 backend/logs/llm_calls.log 로
        일관되게 기록돼, "로그 파일이 어디 갔는지" 헷갈리지 않는다.
        """
        return self._resolve(self.debug_log_path)

    @property
    def active_model(self) -> str:
        """현재 선택된 백엔드의 모델명 (트레이스/로깅용)."""
        if self.llm_backend == "internal":
            return self.internal_llm_model or "internal"
        return self.ollama_model


@lru_cache
def get_settings() -> Settings:
    return Settings()
