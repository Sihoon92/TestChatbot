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
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult

from app.config import Settings

# 로깅 자체의 실패(디스크 문제 등)를 알릴 때 쓰는 내부 로거. 채팅 흐름에는 영향 없음.
_internal_log = logging.getLogger(__name__)

_SEP = "=" * 80


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
        os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
        handler = RotatingFileHandler(
            abs_path,
            maxBytes=settings.debug_log_max_bytes,
            backupCount=settings.debug_log_backup_count,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
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
            self._starts[run_id] = {
                "t0": time.perf_counter(),
                "messages": messages[0] if messages else [],
                "node": meta.get("langgraph_node", "?"),
                "session": meta.get("thread_id", "?"),
            }
        except Exception:  # noqa: BLE001 - 로깅이 채팅을 깨뜨리지 않도록 삼킨다
            _internal_log.warning("debug logging: on_chat_model_start failed", exc_info=True)

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        try:
            start = self._starts.pop(run_id, None)
            elapsed_ms = self._elapsed_ms(start)
            output = self._extract_output(response)
            self._write_llm_block(start, run_id, elapsed_ms, output_lines=[output])
        except Exception:  # noqa: BLE001
            _internal_log.warning("debug logging: on_llm_end failed", exc_info=True)

    def on_llm_error(
        self, error: BaseException, *, run_id: UUID, **kwargs: Any
    ) -> None:
        try:
            start = self._starts.pop(run_id, None)
            elapsed_ms = self._elapsed_ms(start)
            self._write_llm_block(
                start,
                run_id,
                elapsed_ms,
                output_lines=[f"{type(error).__name__}: {error}"],
                errored=True,
            )
        except Exception:  # noqa: BLE001
            _internal_log.warning("debug logging: on_llm_error failed", exc_info=True)

    # --- Tool (주제 4 대비: 지금은 호출되지 않지만 미리 구현) --------------------

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        try:
            name = (serialized or {}).get("name", "?")
            self._starts[run_id] = {"t0": time.perf_counter(), "tool": name}
            self._write_block(
                [f"--- TOOL CALL: {name}({input_str}) ---"]
            )
        except Exception:  # noqa: BLE001
            _internal_log.warning("debug logging: on_tool_start failed", exc_info=True)

    def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        try:
            start = self._starts.pop(run_id, None)
            elapsed_ms = self._elapsed_ms(start)
            self._write_block(
                [f"--- TOOL RESULT ({elapsed_ms}ms) ---", str(output)]
            )
        except Exception:  # noqa: BLE001
            _internal_log.warning("debug logging: on_tool_end failed", exc_info=True)

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

    def _write_llm_block(
        self,
        start: dict[str, Any] | None,
        run_id: UUID,
        elapsed_ms: int,
        *,
        output_lines: list[str],
        errored: bool = False,
    ) -> None:
        node = (start or {}).get("node", "?")
        session = (start or {}).get("session", "?")
        messages: list[BaseMessage] = (start or {}).get("messages", [])
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

        lines = [
            f"[{ts}] session={session} node={node} run={str(run_id)[:8]}",
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
