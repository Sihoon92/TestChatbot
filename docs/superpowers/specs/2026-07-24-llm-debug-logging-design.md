# LLM 디버그 로깅 체계 설계

- 날짜: 2026-07-24
- 배경: 이 프로젝트(FastAPI + LangGraph 백엔드, React + Vite 프론트엔드로 구성된 사내 LLM 채팅 스캐폴드)로 초보자를 교육하는 4개 주제 중 2번째 주제. langfuse 등 외부 관측 도구 없이도, LLM 호출마다의 인풋/아웃풋을 사람이 바로 읽을 수 있는 형태로 남기는 디버그 체계를 만든다.
- 다음 단계(4번 주제: langchain 기반 Agent + tool call)에서 이 로거를 그대로 재사용할 것을 전제로 설계한다.

## 목표
- LLM 호출(및 향후 tool call)마다 시각/세션/노드/입력 메시지/출력/소요시간을 사람이 읽기 좋은 텍스트 블록으로 파일에 기록한다.
- 로깅 유무를 설정으로 켜고 끌 수 있다.
- 로깅 실패가 채팅 기능 자체에 영향을 주지 않는다.
- langfuse 없이도 "LLM이 실제로 무엇을 보내고 받았는지" 즉시 확인 가능하게 한다.

## 비목표 (이번 스코프 제외)
- 프론트엔드 디버그 패널 (백엔드 파일 로그로 확정)
- 토큰 수 / 비용 추정 로깅 (기본 상세도로 확정 — 모델이 usage를 안 줄 수도 있어 제외)
- 세션별 개별 로그 파일 분리 (단일 로테이팅 파일로 확정)

## 아키텍처

### 콜백 핸들러
`backend/app/observability/debug_callback.py`에 `DebugCallbackHandler(BaseCallbackHandler)`를 신설한다.

- `on_chat_model_start(serialized, messages, *, run_id, parent_run_id, tags, metadata, **kwargs)`
  - 호출 시작 시각을 `run_id` 키로 내부 dict에 저장
  - 입력 메시지 목록, `metadata.get("langgraph_node")`, 세션 id(`metadata`/`configurable`에서 유도)를 함께 보관
- `on_llm_end(response: LLMResult, *, run_id, parent_run_id, **kwargs)`
  - 시작 시각과의 차이(ms)를 계산
  - `response.generations[0][0].message`를 출력으로 사용해 한 블록을 완성, 파일에 기록
- `on_llm_error(error, *, run_id, **kwargs)`
  - 실패 사례도 `--- ERROR ---` 블록으로 기록
- `on_tool_start` / `on_tool_end`
  - 지금은 그래프에 tool이 없어 호출되지 않지만, Agent(주제 4) 추가 시 코드 수정 없이 자동으로 tool 호출 로그가 남도록 미리 구현해둔다

### 부착 지점
`backend/app/api/chat.py`의 `cfg = {"configurable": {"thread_id": session_id}}`에 요청마다 다음을 추가한다:

```python
cfg = {"configurable": {"thread_id": session_id}}
if settings.debug_log_enabled:
    cfg["callbacks"] = [DebugCallbackHandler(settings)]
```

요청 단위로 콜백을 붙이는 LangChain 표준 패턴을 따른다(그래프 컴파일 시 고정 장착하지 않음 — 요청별 on/off, 향후 요청별 다른 콜백 부착 등 유연성 확보).

## 설정 (`app/config.py` Settings 확장)

```python
debug_log_enabled: bool = True
debug_log_path: str = "./logs/llm_calls.log"
debug_log_max_bytes: int = 5_000_000   # 5MB
debug_log_backup_count: int = 5
```

`.env.example`에 대응 항목 추가. `backend/logs/`는 `.gitignore`에 추가한다.

## 로그 포맷

Python 표준 `logging.handlers.RotatingFileHandler`를 사용하는 전용 로거(`llm_calls`)에, 아래와 같은 사람이 읽기 좋은 텍스트 블록을 한 번의 `logger.info(block)` 호출로 기록한다(콘솔에는 출력하지 않음).

```
================================================================================
[2026-07-24 10:15:32.118] session=b3f1... node=chat run=8f3a9c1e
--- INPUT (2 messages) ---
[system] You are a helpful assistant...
[human] 안녕하세요, 오늘 날씨 알려줘

--- OUTPUT (842ms) ---
[ai] 안녕하세요! 저는 실시간 날씨 정보에 접근할 수 없어요...
================================================================================
```

Agent(주제 4) 추가 후 tool call이 있는 턴은 INPUT/OUTPUT 사이에 아래가 끼워진다:

```
--- TOOL CALL: get_weather({"city": "Seoul"}) ---
--- TOOL RESULT (118ms) ---
{"temp": 25, "condition": "sunny"}
```

## 에러 처리
- 로그 파일 쓰기 실패(디스크 문제 등)가 채팅 요청 자체를 실패시키지 않는다 — 핸들러 내부에서 로깅 관련 예외는 잡아 `logging.getLogger(__name__).warning(...)`으로만 남기고 삼킨다.
- LLM 호출 자체의 실패는 `on_llm_error`에서 `--- ERROR ---` 블록으로 기록한다.

## 테스트

`backend/tests/test_debug_callback.py` 신규 추가 (기존 `test_config.py`, `test_sessions_repo.py` 패턴을 따름):
- `tmp_path`에 로그 파일 경로를 지정하고 `on_chat_model_start` → `on_llm_end`를 직접 호출해 로그 블록이 예상 포맷(세션/노드/입력/출력/소요시간 포함)으로 기록되는지 검증
- `debug_log_enabled=False`일 때 `chat.py`가 `cfg`에 `callbacks`를 추가하지 않는지 검증
- 로깅 중 예외가 발생해도 정상 흐름(응답 반환)에 영향이 없는지 검증

## 이후 단계와의 연결
- 주제 4(Agent + tool call) 구현 시, 이 핸들러의 `on_tool_start`/`on_tool_end`가 자동으로 동작하므로 로깅 관련 추가 작업이 필요 없다.
- 별도로 작성될 교육 문서/슬라이드에서 "langfuse도 결국 LangChain 콜백 인터페이스 위에서 동작한다"는 점을 이 구현을 예시로 설명한다.
