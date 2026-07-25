"""xlwings 로 엑셀을 읽기 전용으로 여는 얇은 래퍼.

openpyxl 을 못 쓰는 환경을 위해 실제 Excel(COM) 을 구동한다. App 을 숨김으로
띄우고, 끝나면 반드시 닫아 유령 EXCEL.EXE 프로세스가 남지 않게 한다.
값 읽기만 제공한다(수정/저장 없음).

Note on `.options(ndim=2)` (applies to every read below — `range_values`,
`used_values`, `column_values`): xlwings 는 1행/1열 범위를 평면(1D) 리스트로,
단일 셀은 스칼라로 돌려줘 값 모양만으로는 행/열 방향을 구분할 수 없다.
`ndim=2` 를 강제하면 실제 범위 크기 기준으로 항상 올바른 모양의 2D 리스트를
얻는다(빈 범위/단일 셀 포함).
"""
import gc
from contextlib import contextmanager
from typing import Iterator

import xlwings as xw

from app.excel.grid import col_to_letter


class Workbook:
    def __init__(self, book: "xw.Book") -> None:
        self._book = book

    def sheet_names(self) -> list[str]:
        return [s.name for s in self._book.sheets]

    def _sheet(self, sheet: str) -> "xw.Sheet":
        return self._book.sheets[sheet]

    def used_shape(self, sheet: str) -> tuple[int, int]:
        rng = self._sheet(sheet).used_range
        return (rng.rows.count, rng.columns.count)

    def range_values(self, sheet: str, address: str) -> list[list]:
        # ndim=2 rationale: see module docstring.
        return self._sheet(sheet).range(address).options(ndim=2).value

    def used_values(self, sheet: str) -> tuple[list[list], str]:
        rng = self._sheet(sheet).used_range
        top_left = rng[0, 0].get_address(False, False)  # 예: "A1"
        return rng.options(ndim=2).value, top_left

    def column_values(self, sheet: str, column: str, max_rows: int = 5000) -> list:
        sht = self._sheet(sheet)
        nrows = min(sht.used_range.rows.count, max_rows)
        rng = sht.range(f"{column}1:{column}{nrows}")
        return [row[0] for row in rng.options(ndim=2).value]


@contextmanager
def open_workbook(path: str) -> Iterator[Workbook]:
    app = xw.App(visible=False, add_book=False)
    app.display_alerts = False
    app.screen_updating = False
    wb: "Workbook | None" = None
    try:
        book = app.books.open(path, read_only=True, update_links=False)
        wb = Workbook(book)
        try:
            yield wb
        finally:
            book.close()
            # book.close() 만으로는 COM 프록시가 즉시 해제되지 않는다: 호출자가
            # `with open_workbook(...) as wb:` 블록을 벗어난 뒤에도 `wb`(Workbook
            # 래퍼) 변수를 계속 들고 있을 수 있고, 그 래퍼의 `_book` 속성이 여전히
            # 이 xw.Book COM 프록시를 강하게 참조한다. 그래서 여기서 래퍼의 내부
            # 참조(`wb._book`)와 로컬 `book` 을 모두 끊고, Excel 프로세스가 아직
            # 살아있는 이 시점에 gc.collect() 로 즉시 파이널라이즈되도록 유도한다
            # (COM 프록시 정리는 앱 종료 전에 하는 것이 원칙).
            #
            # 실측 결과(참고용): 이 조치를 적용한 뒤에도 `pytest`(플래그 없이,
            # faulthandler 기본 활성) 실행 시 `Windows fatal exception: code
            # 0x800706ba`(RPC_S_SERVER_UNAVAILABLE) 가 여전히 출력된다. 이 예외는
            # book.close()/app.quit() 이 모두 끝나고 테스트의 모든 assertion 이
            # 통과한 *이후*, pytest 프로세스 종료 단계에서 다른 스레드로부터
            # 비동기적으로 발생한다(테스트는 여전히 PASSED 로 보고됨). 즉 이
            # 함수의 정리 순서 문제가 아니라, 이 시점 이후 어딘가(인터프리터
            # 종료 시 pywin32 COM 잔여 스텁의 지연 파이널라이즈로 추정)에서
            # 발생하는, 이 레이어에서는 해결되지 않는 현상이다. 러너 플래그나
            # 설정으로 억제하지 않고 있는 그대로 둔다 — 실행 후 EXCEL.EXE 잔여
            # 프로세스는 없음을 확인했다(정상 종료 신호).
            wb._book = None  # type: ignore[union-attr]
            book = None
            gc.collect()
    finally:
        app.quit()


__all__ = ["open_workbook", "Workbook", "col_to_letter"]
