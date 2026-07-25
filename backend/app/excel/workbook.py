"""xlwings 로 엑셀을 읽기 전용으로 여는 얇은 래퍼.

openpyxl 을 못 쓰는 환경을 위해 실제 Excel(COM) 을 구동한다. App 을 숨김으로
띄우고, 끝나면 반드시 닫아 유령 EXCEL.EXE 프로세스가 남지 않게 한다.
값 읽기만 제공한다(수정/저장 없음).
"""
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
        # ndim=2: xlwings 는 1행/1열 범위를 평면(1D) 리스트로 돌려줘 행/열 방향을
        # 값 모양만으로는 구분할 수 없다. ndim=2 로 강제하면 실제 범위 크기 기준으로
        # 항상 올바른 2D 리스트를 얻는다.
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
    try:
        book = app.books.open(path, read_only=True, update_links=False)
        try:
            yield Workbook(book)
        finally:
            book.close()
    finally:
        app.quit()


__all__ = ["open_workbook", "Workbook", "col_to_letter"]
