"""xlsx 원본 읽기 — DRM 걸린 파일은 Excel(COM) 안에서만 복호화된다.

이 테스트들은 실제 Excel 을 띄운다. Excel 이 없는 환경(CI·리눅스)에서는 통째로
skip 되고, CSV 경로 테스트는 그대로 돈다.
"""
import pandas as pd
import pytest

xw = pytest.importorskip("xlwings")

from app.coating import excel_source  # noqa: E402
from app.coating import schemas as S  # noqa: E402


def _excel_available() -> bool:
    try:
        app = xw.App(visible=False)
        app.quit()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _excel_available(), reason="Excel COM 미설치")

HEADER = [S.LOT, S.AT, S.PRODUCT, S.ITEM, S.ITEM_NAME, S.VALUE]
ROWS = [
    HEADER,
    ["GRQC48XP29", "2026-01-31 18:55", "BNB48X1", "30030859", None, 314],
    ["GRQC48XP29", "2026-01-31 18:56", "BNB48X1", "90030614", None, 18.21],
]


def _write_xlsx(tmp_path, rows=ROWS, sheet_name=None):
    path = tmp_path / "원본.xlsx"
    app = xw.App(visible=False, add_book=False)
    app.display_alerts = False
    try:
        wb = app.books.add()
        if sheet_name:
            wb.sheets[0].name = sheet_name
        wb.sheets[0].range("A1").value = rows
        wb.save(str(path))
        wb.close()
    finally:
        app.quit()
    return path


def test_item_id_comes_back_as_string_not_float(tmp_path):
    """엑셀 셀은 숫자로 저장된다. xlwings 는 그걸 float 로 주고, 그대로 str()
    하면 '30030859.0' 이 되어 항목사전 조인이 예외 없이 전부 미매칭 된다.
    이 프로젝트에서 가장 조용하고 가장 치명적인 실패다."""
    df = excel_source.read_long_table(_write_xlsx(tmp_path))
    assert list(df[S.ITEM]) == ["30030859", "90030614"]
    assert not pd.api.types.is_numeric_dtype(df[S.ITEM])


def test_worked_at_survives_as_parseable_timestamp(tmp_path):
    """엑셀이 날짜로 인식하든 문자열로 두든, 뒤의 to_datetime 이 읽을 수 있는
    형태로 나와야 한다. 여기서 NaT 가 되면 lot 구간이 통째로 사라진다."""
    df = excel_source.read_long_table(_write_xlsx(tmp_path))
    at = pd.to_datetime(df[S.AT])
    assert at.notna().all()
    assert at.iloc[0].minute == 55


def test_missing_sheet_error_lists_actual_sheet_names(tmp_path):
    """시트 이름은 사업부마다 다르다. '시트 없음' 만 던지면 무엇을 적어야
    하는지 알 수 없어서 왕복이 생긴다."""
    path = _write_xlsx(tmp_path, sheet_name="원본데이터")
    with pytest.raises(ValueError) as e:
        excel_source.read_long_table(path, sheet="없는시트")
    assert "원본데이터" in str(e.value)


def test_missing_required_column_error_lists_found_columns(tmp_path):
    """헤더가 한 칸 밀리거나 컬럼명이 다르면 뒤에서 KeyError 로 죽는다.
    무엇이 필요하고 무엇이 있었는지 여기서 말한다."""
    rows = [["lot", "시각", "제품"], ["L1", "2026-01-31 18:55", "BNB48X1"]]
    with pytest.raises(ValueError) as e:
        excel_source.read_long_table(_write_xlsx(tmp_path, rows=rows))
    message = str(e.value)
    assert S.ITEM in message
    assert "제품" in message


ZH_HEADER = ["批次号", "作业时间", "产品", "项目编号", "项目名称", "数值"]


def test_chinese_header_passes_the_early_sheet_check(tmp_path):
    """조기 검증(시트 힌트)도 별칭을 알아야 한다. 여기서 먼저 죽으면 _finalize
    의 이름 변환까지 가지도 못해, 같은 파일이 csv 로는 되고 xlsx 로는 안 되는
    상태가 된다."""
    rows = [ZH_HEADER] + [r for r in ROWS[1:]]
    df = excel_source.read_long_table(_write_xlsx(tmp_path, rows=rows))
    # 이름 변환은 여기서 하지 않는다 - parse._finalize 한 곳에서만 일어난다.
    assert list(df.columns) == ZH_HEADER


def test_chinese_header_xlsx_gives_the_same_readings_as_english_xlsx(tmp_path):
    """두 입력 경로가 갈라지지 않는다는 보증에 언어 축을 하나 더 얹는다.
    (형식 × 언어) 네 조합이 전부 같은 DataFrame 이어야 한다."""
    from app.coating import parse

    # _write_xlsx 는 디렉터리 안에 고정된 이름으로 쓴다. 두 파일이 필요하므로
    # 디렉터리를 나눈다.
    en_dir, zh_dir = tmp_path / "en", tmp_path / "zh"
    en_dir.mkdir()
    zh_dir.mkdir()

    en = parse.load_readings(
        _write_xlsx(en_dir, ROWS), parse.DEFAULT_DICT_PATH, source="xlsx"
    )
    zh = parse.load_readings(
        _write_xlsx(zh_dir, [ZH_HEADER] + ROWS[1:]),
        parse.DEFAULT_DICT_PATH,
        source="xlsx",
    )
    pd.testing.assert_frame_equal(en, zh)


def test_reads_named_sheet_when_workbook_has_several(tmp_path):
    """MES export 는 안내 시트가 앞에 붙는 경우가 있다. 첫 시트 고정이면
    엉뚱한 시트를 읽고 '컬럼이 없다' 로 죽는다."""
    path = tmp_path / "여러시트.xlsx"
    app = xw.App(visible=False, add_book=False)
    app.display_alerts = False
    try:
        wb = app.books.add()
        wb.sheets[0].name = "안내"
        wb.sheets[0].range("A1").value = "이 파일은 MES 추출본입니다"
        data = wb.sheets.add("데이터", after=wb.sheets[0])
        data.range("A1").value = ROWS
        wb.save(str(path))
        wb.close()
    finally:
        app.quit()

    df = excel_source.read_long_table(path, sheet="데이터")
    assert list(df[S.ITEM]) == ["30030859", "90030614"]


def test_xlsx_path_and_csv_path_produce_the_same_readings(tmp_path):
    """두 입력 경로가 갈라지지 않는다는 유일한 보증. 뒤 단계(pivot·events·
    features)는 데이터가 CSV 에서 왔는지 엑셀에서 왔는지 알 필요가 없어야 한다."""
    from pathlib import Path

    from app.coating import parse

    csv_path = Path(__file__).parent / "fixtures" / "coating" / "sample_long.csv"
    csv_df = parse.load_readings(csv_path, parse.DEFAULT_DICT_PATH)

    src = pd.read_csv(csv_path, encoding="utf-8-sig", dtype=str).fillna("")
    rows = [list(src.columns)] + src.values.tolist()

    xlsx_path = tmp_path / "동일데이터.xlsx"
    app = xw.App(visible=False, add_book=False)
    app.display_alerts = False
    try:
        wb = app.books.add()
        wb.sheets[0].range("A1").value = rows
        wb.save(str(xlsx_path))
        wb.close()
    finally:
        app.quit()

    xlsx_df = parse.load_readings(
        xlsx_path, parse.DEFAULT_DICT_PATH, source="xlsx"
    )
    pd.testing.assert_frame_equal(csv_df, xlsx_df, check_dtype=False)
