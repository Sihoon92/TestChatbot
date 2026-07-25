"""examples/analyze_excel.py 엔드투엔드 실행용 예시 워크북 생성기.

`app.excel.open_workbook` 은 읽기 전용 계약이라 쓰기 메서드가 없다(의도된 설계).
그래서 예시 데이터를 "쓰는" 이 스크립트는 raw xlwings App 을 직접 연다 —
examples/verify_tool_calling.py 의 `_seed_workbook_with_token` 과 같은 패턴이며,
읽기 전용 분석 경로(open_workbook)와는 무관한 별도의 준비 단계다.

만들어지는 시트 '데이터' 구조 (계획서의 예시 시나리오를 그대로 반영):
    A~D 열: 메타데이터 (라인, 제품, 구분, 비고)
    E 열~  : 날짜(월별) 헤더, 각 셀은 그 (라인, 제품, 날짜)의 JC(잡체인지) 건수
    '제품'/'구분' 열 값이 '소계'인 행은 같은 라인의 월별 합계(집계행) — 원시
    데이터 이중 계산을 피하려면 분석 시 이 행을 따로 식별해야 한다.

실행 (backend/ 에서, venv 파이썬으로):
    python examples/make_sample_workbook.py
    python examples/make_sample_workbook.py data/custom.xlsx

전제: 이 PC 에 Microsoft Excel 이 설치돼 있어야 한다(xlwings COM).
"""
import sys
from datetime import date
from pathlib import Path

import xlwings as xw

_HEADERS = ["라인", "제품", "구분", "비고"]
_DATES = [date(2026, m, 1) for m in range(1, 7)]  # 2026-01 ~ 2026-06

# (라인, 제품, 구분, 비고, [월별 JC 건수 6개])
# '소계' 행 값은 같은 라인의 다른 행들을 월(열)별로 더한 값 — 원시 데이터 합계와
# 소계행 합계가 항상 일치하도록 손으로 맞춰뒀다(엔드투엔드 검증 시 결과 대조용).
_ROWS = [
    ("A", "제품A1", "실적", "", [5, 3, 4, 2, 6, 1]),
    ("A", "제품A2", "실적", "", [2, 4, 3, 5, 1, 2]),
    ("A", "소계", "소계", "", [7, 7, 7, 7, 7, 3]),
    ("B", "제품B1", "실적", "", [3, 3, 3, 3, 3, 3]),
    ("B", "제품B2", "실적", "", [1, 2, 1, 2, 1, 2]),
    ("B", "소계", "소계", "", [4, 5, 4, 5, 4, 5]),
]


def build(path: str) -> None:
    app = xw.App(visible=False, add_book=False)
    app.display_alerts = False
    app.screen_updating = False
    try:
        book = app.books.add()
        sheet = book.sheets[0]
        sheet.name = "데이터"
        sheet.range("A1").value = [_HEADERS + list(_DATES)]
        for i, (line, product, kind, note, values) in enumerate(_ROWS, start=2):
            sheet.range(f"A{i}").value = [[line, product, kind, note] + values]
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        book.save(str(out_path.resolve()))
        book.close()
    finally:
        app.quit()


_DEFAULT_OUT = Path(__file__).resolve().parents[1] / "data" / "jobchange.xlsx"


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    # 기본 출력 경로는 실행 위치(cwd)와 무관하게 항상 backend/data/ 를 가리켜야
    # 한다 — cwd 상대경로("data/jobchange.xlsx")로 두면 저장소 루트 등 다른
    # 위치에서 실행할 때 엉뚱한 곳에 조용히 data/ 를 새로 만든다(CLAUDE.md 의
    # "설정/경로는 cwd 와 무관해야 한다" 규칙과 같은 이유 — Settings 가
    # backend/.env 를 절대경로로 고정하는 것과 동일한 패턴). 사용자가 직접
    # 경로를 지정하면(args[0]) 그 값은 그대로 쓴다.
    out = args[0] if args else str(_DEFAULT_OUT)
    build(out)
    print(f"생성 완료: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
