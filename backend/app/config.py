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
    # Ollama 런타임 기본 컨텍스트는 4096 이고, 모델이 131k 를 지원해도 그 값으로
    # 서빙된다. 넘치면 **에러 없이** 앞부분이 잘리는데, 거기 도구 정의가 있으면
    # 모델이 어떤 도구도 못 부른다 — 빈 응답이 돌아오고 호출자는 이유를 모른다.
    # 수집 에이전트는 도구 6종 스키마만으로도 4천 자를 넘겨 실제로 이 방식으로
    # 조용히 실패했다. 문서가 크거나 도구가 늘면 더 올린다.
    ollama_num_ctx: int = 16384
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
    ingest_stage_dirs: str = (
        "JIG기준정보:jig_master,EES:ees,MES:mes,IQC:iqc,PQC:pqc,"
        "설계:design,설치:install,AI복검:ai_recheck"
    )
    # 채팅용 app.db 와 반드시 별도 파일. 같은 파일을 쓰면 LangGraph 체크포인터와
    # SQLite 쓰기 락을 두고 경합해 'database is locked' 가 난다.
    molds_db_path: str = "./molds.db"
    master_xlsx_path: str = "./data/관리자/금형현황.xlsx"
    # 0 = 자동 폴링 끔(수동 트리거만). 1단계는 0 으로 둔다.
    ingest_poll_seconds: int = 0

    # ── 코팅 초기조건 도출 ──────────────────────────────────────────────
    # 원본 CSV·중간 parquet·리포트가 모두 이 아래에 놓인다(상대경로는 backend/ 기준).
    coating_data_dir: str = "./data/coating"
    # 이 시간 안에 일어난 제어값 변경들을 하나의 조정 이벤트로 묶는다. 항목별로
    # 쪼개면 하나의 Wet 변화가 여러 이벤트에 중복 귀속되어 영향이 부풀려진다.
    coating_event_merge_minutes: int = 2
    # 안정화 판정: 이 길이(분)의 이동창에서 Wet 평균의 표준편차가
    # coating_settle_std_max 아래로 내려가면 정착으로 본다.
    coating_settle_window_minutes: int = 5
    coating_settle_std_max: float = 0.02
    # 이 시간 안에 정착을 못 찾으면 오염 이벤트로 버린다.
    coating_settle_max_wait_minutes: int = 30
    # 튜닝 종료의 교차검증 기준. 스펙(±0.4)은 합격 판정용이라 튜닝 종료 판정에는
    # 너무 헐겁다 — 실제 변동이 ±0.03 이면 즉시 스펙에 들어와 구간이 사라진다.
    coating_tuning_band: float = 0.1
    # 영향행렬 커널 반폭 k. 파라미터 수 = 2k+1.
    coating_kernel_half_width: int = 2
    coating_ridge_alpha: float = 1.0
    # 입력 소스. 확장자로 알 수 없을 때의 기본값이다 - '.csv'·'.xlsx'·'.parquet'
    # 는 parse.format_for 가 확장자로 판별하므로 이 값을 안 본다(사내 MES 가
    # '.dat' 로 내리는 것 같은 경우에만 쓰인다).
    #
    # xlsx 는 DRM 때문에 있다. 사내 실데이터 CSV 가 암호화돼 python 이 바이트를
    # 직접 못 읽는 동안 Excel(COM)로 우회한다. 다만 그 경로는 실행할 때마다
    # Excel 을 띄우고 xlwings 를 요구하므로, 한 번 읽을 수 있게 되면
    # `python -m app.coating.convert` 로 parquet 을 만들어 그것을 원본으로 쓰는
    # 편이 낫다 - 그 뒤로는 Excel 도 xlwings 도 필요 없다.
    coating_input_format: str = "csv"  # csv | xlsx | parquet
    # 원본 파일 경로. 상대경로는 COATING_DATA_DIR 기준이다(raw/ 아래에 둔다).
    coating_input_path: str = "raw/sample_long.csv"
    # 읽을 시트. 비우면 첫 시트. xlsx 일 때만 쓴다.
    coating_xlsx_sheet: str = ""
    # 원본 CSV 인코딩 후보(쉼표 구분). 앞에서부터 시도한다.
    # 사내 MES·엑셀 export 는 cp949 가 흔하고 우리 픽스처는 utf-8-sig 다.
    # utf-8 을 먼저 두는 순서가 중요하다(app/coating/parse.py 주석 참조).
    coating_csv_encodings: str = "utf-8-sig,cp949"

    @property
    def resolved_coating_input_path(self) -> str:
        """원본 파일의 절대경로. 상대경로는 backend/ 가 아니라 데이터 루트 기준이다

        - raw/interim/reports 가 한 루트 아래 모여 있다는 규약을 유지하려면
        기준점이 COATING_DATA_DIR 이어야 한다."""
        p = Path(self.coating_input_path)
        if p.is_absolute():
            return str(p)
        return str(Path(self.resolved_coating_data_dir) / p)

    @property
    def coating_csv_encoding_list(self) -> list[str]:
        """"utf-8-sig,cp949" -> ["utf-8-sig", "cp949"]. 빈 값이면 기본 후보."""
        out = [e.strip() for e in self.coating_csv_encodings.split(",") if e.strip()]
        return out or ["utf-8-sig", "cp949"]

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
    def resolved_coating_data_dir(self) -> str:
        return self._resolve(self.coating_data_dir)

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
