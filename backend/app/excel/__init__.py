"""엑셀 분석 패키지 진입점.

`app.excel.grid` 는 xlwings/COM 에 의존하지 않는 순수 계층으로 설계됐다(모듈
docstring 참고) — Excel 이 없는 환경에서도 단위 테스트가 돌아야 한다는 게 그
분리의 존재 이유다. 그런데 이 `__init__.py` 가 `workbook`/`tools`/`agent` 를
top-level 에서 즉시 import 하면, `import app.excel.grid` 한 줄만으로도 패키지
`__init__.py` 가 먼저 실행되며 xlwings/pythoncom(Windows 전용, pyproject.toml
에 별도 선언돼 있지 않다)/langgraph 까지 전부 로드돼 버려 그 분리가 무의미해진다.
그래서 이 재수출들은 실제로 접근될 때만(module-level `__getattr__`, PEP 562)
지연 로드한다 — `import app.excel.grid` 만으로는 아무것도 추가로 로드되지
않는다.
"""
import importlib
from typing import Any

__all__ = [
    "open_workbook",
    "Workbook",
    "make_excel_tools",
    "build_excel_agent",
    "run_excel_agent",
    "EXCEL_SYSTEM_PROMPT",
]

# 이름 → 실제로 정의된 서브모듈. 값 하나만 필요해도 그 모듈 전체를 import 하게
# 되는 건 감수한다(workbook/tools/agent 는 어차피 서로 얽혀 있어 부분 로드로는
# xlwings/langgraph 의존을 피할 수 없다) — 여기서 막으려는 건 "grid 만 쓰는
# 호출자까지 덩달아 무거워지는 것"이지, workbook/agent 를 실제로 쓰는 호출자의
# import 비용이 아니다.
_LAZY_MODULES = {
    "open_workbook": "app.excel.workbook",
    "Workbook": "app.excel.workbook",
    "make_excel_tools": "app.excel.tools",
    "build_excel_agent": "app.excel.agent",
    "run_excel_agent": "app.excel.agent",
    "EXCEL_SYSTEM_PROMPT": "app.excel.agent",
}


def __getattr__(name: str) -> Any:
    module_name = _LAZY_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(module_name)
    value = getattr(module, name)
    globals()[name] = value  # 다음 접근부터는 재조회 없이 바로 쓰도록 캐시
    return value
