"""엑셀 파일을 ReAct 에이전트로 분석하는 실행 진입점.

실행 (backend/ 에서, venv 파이썬으로):
    python examples/analyze_excel.py data/jobchange.xlsx "라인별 JC 소계를 요약해줘"
    python examples/analyze_excel.py data/jobchange.xlsx "질문" --backend internal

예시 워크북이 없다면 먼저 생성한다:
    python examples/make_sample_workbook.py

전제:
- 사전점검(examples/verify_tool_calling.py)이 PASS 여야 한다.
- 이 PC 에 Microsoft Excel 이 설치돼 있어야 한다(xlwings COM).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

# Windows 콘솔 코드페이지(cp949 등)에서도 print() 의 한글/화살표(→)가
# UnicodeEncodeError 로 죽지 않게 한다. examples/verify_tool_calling.py 와 같은
# 이유·같은 패턴(스트림 인코딩 자체는 바꾸지 않고 인코딩 못 하는 문자만 대체).
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name)
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")

from app.config import get_settings  # noqa: E402
from app.excel import open_workbook, run_excel_agent  # noqa: E402
from app.llm import get_chat_model  # noqa: E402

_USAGE = '사용법: python examples/analyze_excel.py <파일경로> "<질문>" [--backend internal]'


def _parse_args(argv: list[str]) -> tuple[list[str], str | None] | None:
    """`--backend <값>` 을 위치에 상관없이 뽑아내고 나머지를 위치 인자로 돌려준다.

    브리프 원안(`[a for a in sys.argv[1:] if not a.startswith("--")]`)은 플래그
    이름만 걸러내고 그 값은 그대로 남겨서, `--backend internal <path> <question>`
    처럼 플래그가 위치 인자보다 앞에 오면 "internal" 이 args[0]으로 섞여 들어가는
    버그가 있었다. 문서화된 호출(플래그가 맨 뒤)에서는 우연히 동작하지만, 순서에
    의존하는 파싱은 같은 CLI 인터페이스의 정확성 문제이므로 순서 무관하게 고쳤다
    (새 옵션을 추가하지는 않았다 — `--backend` 하나만 그대로 지원).
    """
    positional: list[str] = []
    backend: str | None = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--backend":
            if i + 1 >= len(argv):
                return None
            backend = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--"):
            i += 1
            continue
        positional.append(arg)
        i += 1
    return positional, backend


def main() -> int:
    parsed = _parse_args(sys.argv[1:])
    if parsed is None:
        print(_USAGE)
        return 2
    args, backend = parsed
    if len(args) < 2:
        print(_USAGE)
        return 2
    path, question = args[0], args[1]

    settings = get_settings()
    if backend:
        settings = settings.model_copy(update={"llm_backend": backend})
    model = get_chat_model(settings)

    print(f"[분석] 파일={path}\n[질문] {question}\n" + "=" * 60)
    with open_workbook(path) as wb:
        out = run_excel_agent(model, wb, question)
    print("사용한 도구:", " → ".join(out["tool_calls"]) or "(없음)")
    print("=" * 60)
    print(out["answer"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
