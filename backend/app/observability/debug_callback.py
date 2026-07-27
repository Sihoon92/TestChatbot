"""LLM 호출 디버그 로깅 콜백.

langfuse 같은 외부 관측 도구 없이도, LangChain 콜백 인터페이스만으로 LLM 호출의
입력/출력/소요시간을 사람이 바로 읽을 수 있는 텍스트 블록으로 파일에 남긴다.

주제 4(Agent + tool call)에서 이 핸들러를 그대로 재사용한다 — 그래프에 tool 이
추가되면 ``on_tool_start``/``on_tool_end`` 가 자동으로 동작해 tool 호출 로그가
코드 수정 없이 남는다.
"""

from __future__ import annotations

import logging
import os
import time
import traceback
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult

from app.config import Settings, get_settings

# 로깅 자체의 실패(디스크 문제 등)를 알릴 때 쓰는 내부 로거. 채팅 흐름에는 영향 없음.
_internal_log = logging.getLogger(__name__)

_SEP = "=" * 80


def _trace(settings: Settings, msg: str) -> None:
    """진단 모드일 때만 콘솔에 추적 한 줄을 남긴다(startup 로그처럼 flush)."""
    if getattr(settings, "debug_log_verbose", False):
        print(f"[debug-log:trace] {msg}", flush=True)


def _report_error(settings: Settings, where: str, exc: BaseException) -> None:
    """로깅 실패는 채팅을 깨뜨리지 않도록 삼키되, '조용히 사라지지'는 않게 한다.

    - 항상 콘솔에 한 줄(원인 요약)을 남긴다(이게 이번 문제의 핵심 — 무음 실패 방지).
    - 진단 모드면 전체 traceback 까지 출력한다.
    """
    print(f"[debug-log] WARNING: {where} 실패: {type(exc).__name__}: {exc}", flush=True)
    _internal_log.warning("debug logging: %s failed", where, exc_info=True)
    if getattr(settings, "debug_log_verbose", False):
        traceback.print_exc()


def _get_logger(settings: Settings) -> logging.Logger:
    """설정된 경로로 기록하는 전용 로거를 (경로별로 1회) 준비해 돌려준다.

    - 콘솔에는 출력하지 않는다(``propagate=False``, 파일 핸들러만 부착).
    - 경로를 로거 이름에 포함해, 서로 다른 경로(예: 테스트의 tmp_path)가
      독립된 핸들러를 갖도록 한다.
    """
    abs_path = settings.resolved_debug_log_path
    logger = logging.getLogger(f"llm_calls:{abs_path}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        _trace(settings, f"파일 핸들러 생성 시도 → {abs_path}")
        os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
        handler = RotatingFileHandler(
            abs_path,
            maxBytes=settings.debug_log_max_bytes,
            backupCount=settings.debug_log_backup_count,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        _trace(settings, f"파일 핸들러 생성 완료 → {abs_path}")
    return logger


def _render_message(msg: BaseMessage) -> str:
    """메시지 한 개를 ``[role] content`` 한 줄(+필요 시 tool_call)로 렌더링."""
    content = msg.content
    if not isinstance(content, str):
        content = str(content)
    line = f"[{msg.type}] {content}"
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls:
        for tc in tool_calls:
            name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "?")
            args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {})
            line += f"\n  ↳ tool_call: {name}({args})"
    return line


class DebugCallbackHandler(BaseCallbackHandler):
    """LLM/tool 호출을 사람이 읽기 좋은 블록으로 파일에 남기는 콜백 핸들러.

    요청 단위로 생성해 ``config["callbacks"]`` 에 부착하는 표준 패턴으로 쓴다.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # run_id -> 시작 정보(시작 시각, 입력 메시지, 노드, 세션)
        self._starts: dict[UUID, dict[str, Any]] = {}

    # --- LLM (chat model) ---------------------------------------------------

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        try:
            meta = metadata or {}
            _trace(
                self.settings,
                f"on_chat_model_start 발화 run={str(run_id)[:8]} "
                f"node={meta.get('langgraph_node', '?')} session={meta.get('thread_id', '?')}",
            )
            self._starts[run_id] = {
                "t0": time.perf_counter(),
                "messages": messages[0] if messages else [],
                "node": meta.get("langgraph_node", "?"),
                "session": meta.get("thread_id", "?"),
                # 호출자가 config metadata 로 넘긴 실행 주체 이름. 웹 채팅과
                # examples/ 의 여러 스크립트가 같은 로그 파일을 공유하므로 필요하다.
                "script": meta.get("script"),
            }
        except Exception as exc:  # noqa: BLE001 - 로깅이 채팅을 깨뜨리지 않도록 삼킨다
            _report_error(self.settings, "on_chat_model_start", exc)

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        try:
            _trace(self.settings, f"on_llm_end 발화 run={str(run_id)[:8]}")
            start = self._starts.pop(run_id, None)
            elapsed_ms = self._elapsed_ms(start)
            output = self._extract_output(response)
            self._write_llm_block(start, run_id, elapsed_ms, output_lines=[output])
        except Exception as exc:  # noqa: BLE001
            _report_error(self.settings, "on_llm_end", exc)

    def on_llm_error(
        self, error: BaseException, *, run_id: UUID, **kwargs: Any
    ) -> None:
        try:
            _trace(self.settings, f"on_llm_error 발화 run={str(run_id)[:8]}: {type(error).__name__}")
            start = self._starts.pop(run_id, None)
            elapsed_ms = self._elapsed_ms(start)
            self._write_llm_block(
                start,
                run_id,
                elapsed_ms,
                output_lines=[f"{type(error).__name__}: {error}"],
                errored=True,
            )
        except Exception as exc:  # noqa: BLE001
            _report_error(self.settings, "on_llm_error", exc)

    # --- Tool ---------------------------------------------------------------

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        try:
            name = (serialized or {}).get("name", "?")
            meta = metadata or {}
            _trace(self.settings, f"on_tool_start 발화 run={str(run_id)[:8]} tool={name}")
            start = {
                "t0": time.perf_counter(),
                "tool": name,
                "node": meta.get("langgraph_node"),
                "session": meta.get("thread_id"),
                "script": meta.get("script"),
            }
            self._starts[run_id] = start
            self._write_block(
                [self._header(start, run_id), f"--- TOOL CALL: {name}({input_str}) ---"]
            )
        except Exception as exc:  # noqa: BLE001
            _report_error(self.settings, "on_tool_start", exc)

    def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        try:
            _trace(self.settings, f"on_tool_end 발화 run={str(run_id)[:8]}")
            start = self._starts.pop(run_id, None)
            self._write_tool_result(start, run_id, [str(output)])
        except Exception as exc:  # noqa: BLE001
            _report_error(self.settings, "on_tool_end", exc)

    def on_tool_error(
        self, error: BaseException, *, run_id: UUID, **kwargs: Any
    ) -> None:
        """도구가 예외로 끝난 경우도 블록으로 남긴다.

        on_tool_end 는 이때 발화하지 않으므로, 이 핸들러가 없으면 로그에는
        TOOL CALL 만 남고 결과가 통째로 사라져 "도구가 멈춘 것인지 실패한
        것인지" 구분할 수 없다(_starts 항목도 회수되지 않는다). ReAct 에이전트는
        도구 실패를 ToolMessage 로 바꿔 계속 진행하므로, 최종 답변만 봐서는
        중간 실패가 드러나지 않는다 — 그래서 더더욱 남겨야 한다.
        """
        try:
            _trace(
                self.settings,
                f"on_tool_error 발화 run={str(run_id)[:8]}: {type(error).__name__}",
            )
            start = self._starts.pop(run_id, None)
            self._write_tool_result(
                start, run_id, [f"{type(error).__name__}: {error}"], errored=True
            )
        except Exception as exc:  # noqa: BLE001
            _report_error(self.settings, "on_tool_error", exc)

    # --- 내부 헬퍼 ----------------------------------------------------------

    @staticmethod
    def _elapsed_ms(start: dict[str, Any] | None) -> int:
        if not start or "t0" not in start:
            return 0
        return int((time.perf_counter() - start["t0"]) * 1000)

    @staticmethod
    def _extract_output(response: LLMResult) -> str:
        try:
            gen = response.generations[0][0]
            msg = getattr(gen, "message", None)
            if msg is not None:
                return _render_message(msg)
            return f"[ai] {gen.text}"
        except Exception:  # noqa: BLE001
            return "[ai] <no output>"

    @staticmethod
    def _header(start: dict[str, Any] | None, run_id: UUID) -> str:
        """모든 블록의 첫 줄 — 언제·어느 실행·어느 노드에서 나왔는지.

        웹 채팅(app/api/chat.py)과 examples/ 의 여러 스크립트가 같은 로그 파일을
        공유하므로, 호출자가 config metadata 로 넘긴 ``script`` 가 있으면 함께
        남겨 어느 실행의 블록인지 구분할 수 있게 한다. 값이 없는 항목은 ``?`` 로
        채우지 않고 생략해 한 줄을 짧게 유지한다(그래프를 거치지 않는 예제는
        session/node 가 애초에 없다).
        """
        info = start or {}
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        parts = [f"[{ts}]"]
        for key in ("script", "session", "node"):
            value = info.get(key)
            if value and value != "?":
                parts.append(f"{key}={value}")
        parts.append(f"run={str(run_id)[:8]}")
        return " ".join(parts)

    def _write_tool_result(
        self,
        start: dict[str, Any] | None,
        run_id: UUID,
        output_lines: list[str],
        *,
        errored: bool = False,
    ) -> None:
        name = (start or {}).get("tool", "?")
        elapsed_ms = self._elapsed_ms(start)
        label = "TOOL ERROR" if errored else "TOOL RESULT"
        self._write_block(
            [
                self._header(start, run_id),
                f"--- {label}: {name} ({elapsed_ms}ms) ---",
                *output_lines,
            ]
        )

    def _write_llm_block(
        self,
        start: dict[str, Any] | None,
        run_id: UUID,
        elapsed_ms: int,
        *,
        output_lines: list[str],
        errored: bool = False,
    ) -> None:
        messages: list[BaseMessage] = (start or {}).get("messages", [])
        lines = [
            self._header(start, run_id),
            f"--- INPUT ({len(messages)} messages) ---",
            *[_render_message(m) for m in messages],
            "",
            f"--- {'ERROR' if errored else 'OUTPUT'} ({elapsed_ms}ms) ---",
            *output_lines,
        ]
        self._write_block(lines)

    def _write_block(self, lines: list[str]) -> None:
        """구분선으로 감싼 한 블록을 파일에 한 번의 info() 로 기록한다."""
        block = "\n".join([_SEP, *lines, _SEP])
        _get_logger(self.settings).info(block)
        _trace(
            self.settings,
            f"블록 기록 완료 ({len(block)} chars) → {self.settings.resolved_debug_log_path}",
        )


def build_debug_callbacks(
    settings: Settings | None = None,
) -> list[BaseCallbackHandler] | None:
    """디버그 로깅이 켜져 있으면 콜백 리스트를, 꺼져 있으면 None 을 돌려준다.

    ``settings.debug_log_enabled`` 판단을 진입점마다 되풀이하지 않게 한 곳에
    모은다(웹 채팅의 build_run_config 와 examples/ 스크립트들이 공유).
    """
    settings = settings or get_settings()
    if not settings.debug_log_enabled:
        return None
    return [DebugCallbackHandler(settings)]


def debug_run_config(script: str, settings: Settings | None = None) -> dict:
    """``invoke(..., config=...)`` 에 그대로 넘길 수 있는 디버그 로깅용 실행 config.

    로깅이 꺼져 있으면 빈 dict 를 돌려주므로 호출부에 분기가 필요 없다
    (빈 config 는 LangChain 이 무시한다). ``script`` 는 로그 블록 헤더에
    남아, 웹 채팅과 여러 예제 스크립트가 같은 파일에 쓸 때 어느 실행에서 나온
    블록인지 구분하게 해준다 — 예: ``"tool_calling/step_4"``.

    이미 config 를 만들어 쓰는 호출부에서는 펼쳐서 합치면 된다:
        ``{"configurable": {...}, **debug_run_config("...")}``
    """
    callbacks = build_debug_callbacks(settings)
    if not callbacks:
        return {}
    return {"callbacks": callbacks, "metadata": {"script": script}}
