import pytest

xw = pytest.importorskip("xlwings")

from app.excel.workbook import open_workbook  # noqa: E402


def _excel_available() -> bool:
    try:
        app = xw.App(visible=False)
        app.quit()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _excel_available(), reason="Excel COM 미설치")


def test_open_and_read(tmp_path):
    path = tmp_path / "sample.xlsx"
    app = xw.App(visible=False)
    try:
        wb = app.books.add()
        sht = wb.sheets[0]
        sht.name = "데이터"
        sht.range("A1").value = [["라인", "제품", "2026-01-01"], ["A", "제품1", 3]]
        wb.save(str(path))
        wb.close()
    finally:
        app.quit()

    with open_workbook(str(path)) as book:
        assert "데이터" in book.sheet_names()
        rows, top_left = book.used_values("데이터")
        assert top_left == "A1"
        assert rows[0][0] == "라인"
        assert book.column_values("데이터", "A")[:2] == ["라인", "A"]
