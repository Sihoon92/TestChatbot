# 프로젝트 작업 규칙 (TestProject)

## 설정값은 항상 `.env` 를 단일 출처로 참조한다 (필수)

**모든 기본 세팅값(LLM 백엔드/URL/모델/키, 포트, 로그 경로, SSL 등)은 `backend/.env` 에서 읽는다.**
코드·스크립트·예제·테스트 어디서도 이 값들을 하드코딩하지 않는다.

### 규칙
- **설정 접근은 `app.config.get_settings()` 로 한다.** `Settings` 는 cwd 와 무관하게 항상
  `backend/.env` 를 읽도록 절대경로로 고정돼 있다(`backend/app/config.py`). 새 스크립트도
  이 경로를 재사용하면 어디서 실행하든 같은 설정을 본다.
- **`get_settings()` 를 못 쓰는 순수 예제(app 미의존)** 는 `backend/examples/connect_llm.py`
  의 `load_env_file()` 처럼 `backend/.env` 를 명시적으로 로드한다. cwd 상대경로(`".env"`)에
  의존하지 않는다.
- **하드코딩 금지 대상**: `INTERNAL_LLM_BASE_URL`, `INTERNAL_LLM_MODEL`, `INTERNAL_LLM_API_KEY`,
  `OLLAMA_*`, `BACKEND_PORT`, `DEBUG_LOG_*`, SSL/프록시 관련 값. 기본값이 필요하면
  `.env` 또는 `.env.example` 에 정의하고 거기서 읽는다.
- **런타임 오버라이드가 필요하면** `settings.model_copy(update={...})` 로 복제해 쓴다
  (예: 특정 스크립트에서 `llm_backend` 강제). 원본 설정/`.env` 는 건드리지 않는다.
- **새 설정 항목을 추가할 때** `Settings` 필드 + `backend/.env.example` 에 기본값을 함께 넣어,
  `.env` 가 항상 "무엇을 설정할 수 있는지"의 문서 역할을 하게 한다.

### 이유
실행 위치(cwd)나 환경마다 설정이 달라지면 "왜 안 되는지" 디버깅이 폭발한다(과거 포트/백엔드
불일치로 오래 헤맴). 설정의 단일 출처를 `.env` 로 고정하면 재현성과 이식성이 보장된다.
