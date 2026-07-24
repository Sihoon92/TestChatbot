from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, LLMResult

# app.main 을 먼저 로드해야 한다: chat.py 가 app.main 을 import 하고 app.main 은
# import 시점에 create_app() 으로 chat.router 를 등록하므로, chat 을 먼저 import 하면
# 순환 import 가 된다. main 을 먼저 완전히 로드하면 순서가 확정돼 안전하다.
import app.main  # noqa: F401,E402  (import 순서 의도적)
from app.api.chat import build_run_config  # noqa: E402
from app.config import Settings  # noqa: E402
from app.observability import DebugCallbackHandler  # noqa: E402


def _settings(tmp_path, **overrides) -> Settings:
    defaults = dict(debug_log_path=str(tmp_path / "llm_calls.log"))
    defaults.update(overrides)
    return Settings(**defaults)


def _llm_result(text: str) -> LLMResult:
    gen = ChatGeneration(message=AIMessage(content=text))
    return LLMResult(generations=[[gen]])


def test_llm_block_written_with_expected_format(tmp_path):
    settings = _settings(tmp_path)
    handler = DebugCallbackHandler(settings)
    run_id = uuid4()

    handler.on_chat_model_start(
        {},
        [[SystemMessage(content="You are helpful"), HumanMessage(content="안녕하세요")]],
        run_id=run_id,
        metadata={"langgraph_node": "chat", "thread_id": "sess-123"},
    )
    handler.on_llm_end(_llm_result("반갑습니다!"), run_id=run_id)

    log_text = (tmp_path / "llm_calls.log").read_text(encoding="utf-8")
    # 세션 / 노드 / 입력 / 출력 / 소요시간(ms) 이 모두 블록에 포함
    assert "session=sess-123" in log_text
    assert "node=chat" in log_text
    assert "--- INPUT (2 messages) ---" in log_text
    assert "[system] You are helpful" in log_text
    assert "[human] 안녕하세요" in log_text
    assert "--- OUTPUT (" in log_text and "ms) ---" in log_text
    assert "[ai] 반갑습니다!" in log_text


def test_llm_error_written_as_error_block(tmp_path):
    settings = _settings(tmp_path)
    handler = DebugCallbackHandler(settings)
    run_id = uuid4()

    handler.on_chat_model_start(
        {},
        [[HumanMessage(content="hi")]],
        run_id=run_id,
        metadata={"langgraph_node": "chat", "thread_id": "sess-err"},
    )
    handler.on_llm_error(RuntimeError("boom"), run_id=run_id)

    log_text = (tmp_path / "llm_calls.log").read_text(encoding="utf-8")
    assert "--- ERROR (" in log_text and "ms) ---" in log_text
    assert "RuntimeError: boom" in log_text


def test_disabled_config_has_no_callbacks(tmp_path):
    settings = _settings(tmp_path, debug_log_enabled=False)
    cfg = build_run_config("sess-x", settings)
    assert "callbacks" not in cfg
    assert cfg["configurable"]["thread_id"] == "sess-x"


def test_enabled_config_attaches_callback(tmp_path):
    settings = _settings(tmp_path, debug_log_enabled=True)
    cfg = build_run_config("sess-y", settings)
    assert len(cfg["callbacks"]) == 1
    assert isinstance(cfg["callbacks"][0], DebugCallbackHandler)


def test_logging_failure_does_not_raise(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    handler = DebugCallbackHandler(settings)
    run_id = uuid4()

    # 블록 기록 단계에서 예외가 나도 정상 흐름을 깨뜨리지 않아야 한다.
    def _boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(
        "app.observability.debug_callback._get_logger", _boom
    )
    handler.on_chat_model_start(
        {},
        [[HumanMessage(content="hi")]],
        run_id=run_id,
        metadata={"langgraph_node": "chat", "thread_id": "sess-z"},
    )
    # 예외가 전파되지 않고 삼켜져야 함
    handler.on_llm_end(_llm_result("ok"), run_id=run_id)


def test_failure_is_reported_not_silent(tmp_path, monkeypatch, capsys):
    settings = _settings(tmp_path)
    handler = DebugCallbackHandler(settings)
    run_id = uuid4()

    def _boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr("app.observability.debug_callback._get_logger", _boom)
    handler.on_chat_model_start(
        {}, [[HumanMessage(content="hi")]], run_id=run_id,
        metadata={"langgraph_node": "chat", "thread_id": "s"},
    )
    handler.on_llm_end(_llm_result("ok"), run_id=run_id)
    # 무음이 아니라 콘솔에 실패가 드러나야 한다(이번 문제의 핵심).
    out = capsys.readouterr().out
    assert "on_llm_end 실패" in out and "disk full" in out


def test_verbose_traces_callback_firing(tmp_path, capsys):
    settings = _settings(tmp_path, debug_log_verbose=True)
    handler = DebugCallbackHandler(settings)
    run_id = uuid4()

    handler.on_chat_model_start(
        {}, [[HumanMessage(content="hi")]], run_id=run_id,
        metadata={"langgraph_node": "chat", "thread_id": "s"},
    )
    handler.on_llm_end(_llm_result("ok"), run_id=run_id)
    out = capsys.readouterr().out
    assert "on_chat_model_start 발화" in out
    assert "on_llm_end 발화" in out
    assert "블록 기록 완료" in out


def test_quiet_by_default(tmp_path, capsys):
    settings = _settings(tmp_path)  # debug_log_verbose 기본 False
    handler = DebugCallbackHandler(settings)
    run_id = uuid4()
    handler.on_chat_model_start(
        {}, [[HumanMessage(content="hi")]], run_id=run_id,
        metadata={"langgraph_node": "chat", "thread_id": "s"},
    )
    handler.on_llm_end(_llm_result("ok"), run_id=run_id)
    assert "[debug-log:trace]" not in capsys.readouterr().out
