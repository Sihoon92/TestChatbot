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


def _write_sample(tmp_path, values):
    """values 를 시트 '데이터' 의 A1 에 쓴 새 워크북을 만들고 경로를 반환."""
    path = tmp_path / "shapes.xlsx"
    app = xw.App(visible=False)
    try:
        wb = app.books.add()
        sht = wb.sheets[0]
        sht.name = "데이터"
        if values is not None:
            sht.range("A1").value = values
        wb.save(str(path))
        wb.close()
    finally:
        app.quit()
    return path


def test_used_shape_and_range_values_multi_row_multi_col(tmp_path):
    path = _write_sample(
        tmp_path, [["라인", "제품", "수량"], ["A", "제품1", 3], ["B", "제품2", 5]]
    )
    with open_workbook(str(path)) as book:
        assert book.used_shape("데이터") == (3, 3)
        block = book.range_values("데이터", "A1:C3")
        assert block == [
            ["라인", "제품", "수량"],
            ["A", "제품1", 3.0],
            ["B", "제품2", 5.0],
        ]


def test_range_values_single_row_is_2d_one_row(tmp_path):
    path = _write_sample(tmp_path, [["라인", "제품", "수량"]])
    with open_workbook(str(path)) as book:
        assert book.used_shape("데이터") == (1, 3)
        row = book.range_values("데이터", "A1:C1")
        assert row == [["라인", "제품", "수량"]]
        assert len(row) == 1
        assert len(row[0]) == 3


def test_range_values_single_column_is_2d_one_col(tmp_path):
    path = _write_sample(tmp_path, [["라인"], ["A"], ["B"]])
    with open_workbook(str(path)) as book:
        assert book.used_shape("데이터") == (3, 1)
        col = book.range_values("데이터", "A1:A3")
        assert col == [["라인"], ["A"], ["B"]]
        assert len(col) == 3
        assert all(len(r) == 1 for r in col)


def test_range_values_single_cell_is_2d_scalar(tmp_path):
    path = _write_sample(tmp_path, [["단일값"]])
    with open_workbook(str(path)) as book:
        assert book.used_shape("데이터") == (1, 1)
        cell = book.range_values("데이터", "A1")
        assert cell == [["단일값"]]


def test_range_values_empty_range_is_2d_none(tmp_path):
    path = _write_sample(tmp_path, [["라인", "제품"]])
    with open_workbook(str(path)) as book:
        # 데이터가 전혀 없는 셀을 명시적으로 읽으면 xlwings 는 None(스칼라)을
        # 돌려주고, ndim=2 정규화 계약상 [[None]] 이어야 한다.
        cell = book.range_values("데이터", "E10")
        assert cell == [[None]]


def test_used_values_and_column_values_single_row_sheet(tmp_path):
    path = _write_sample(tmp_path, [["라인", "제품", "수량"]])
    with open_workbook(str(path)) as book:
        rows, top_left = book.used_values("데이터")
        assert top_left == "A1"
        assert rows == [["라인", "제품", "수량"]]
        assert book.column_values("데이터", "A") == ["라인"]
        assert book.column_values("데이터", "B") == ["제품"]
