"""원본 → parquet 변환 — 바꾼 파일이 원본과 같은 결과를 내야만 의미가 있다.

이 기능의 목적은 속도가 아니라 **의존성 제거**다. DRM 이 풀리는 PC 에서 한 번
변환해 두면 그 뒤로는 아무도 Excel·xlwings 가 필요 없다. 그래서 여기서 가장
중요한 테스트는 "변환한 것과 원본이 같은가" 와 "xlwings 없이 도는가" 두 개다.
"""
import shutil
from pathlib import Path

import pandas as pd
import pytest

from app.coating import convert, parse
from app.coating import schemas as S

SAMPLE = Path(__file__).parent / "fixtures" / "coating" / "sample_long.csv"
ZH = Path(__file__).parent / "fixtures" / "coating" / "sample_long_zh.csv"
DICT = parse.DEFAULT_DICT_PATH


@pytest.fixture
def src(tmp_path):
    p = tmp_path / "원본.csv"
    shutil.copy(SAMPLE, p)
    return p


# ── 핵심 계약 ───────────────────────────────────────────────────────────

def test_converted_parquet_reads_back_identically(src):
    """변환한 parquet 을 읽은 결과가 원본을 직접 읽은 것과 완전히 같아야 한다.
    여기가 어긋나면 이 기능은 '원본을 바꾸는' 것이 아니라 '다른 데이터를 만드는'
    것이 된다."""
    out = convert.to_parquet(src, source="csv")
    pd.testing.assert_frame_equal(
        parse.load_readings(src, DICT, source="csv"),
        parse.load_readings(out, DICT, source="parquet"),
    )


def test_chinese_header_source_lands_as_canonical_names(tmp_path):
    """받아보는 사람이 별칭 표를 몰라도 되게 표준 이름으로 담는다."""
    out = convert.to_parquet(ZH, tmp_path / "zh.parquet", source="csv")
    assert list(pd.read_parquet(out).columns) == [
        S.LOT, S.AT, S.PRODUCT, S.ITEM, S.VALUE
    ]
    # 그리고 영문 헤더 원본과 결과가 같다.
    pd.testing.assert_frame_equal(
        parse.load_readings(SAMPLE, DICT),
        parse.load_readings(out, DICT, source="parquet"),
    )


def test_parquet_holds_no_dictionary_or_row_no(src):
    """사전과 row_no 는 읽을 때 붙는다. 파일에 담으면 그 파일은 원본이 아니라
    파생물이 되고, 사전을 고칠 때마다 다시 만들어야 한다."""
    out = convert.to_parquet(src, source="csv")
    stored = pd.read_parquet(out)
    for col in (S.ROW_NO, S.IO, S.ROLE, S.ZONE, S.ITEM_NAME):
        assert col not in stored.columns

    loaded = parse.load_readings(out, DICT, source="parquet")
    for col in (S.ROW_NO, S.IO, S.ROLE, S.ZONE, S.ITEM_NAME):
        assert col in loaded.columns


def test_updated_dictionary_applies_without_reconverting(src, tmp_path):
    """사전을 파일 밖에 둔 이유. 사전만 고치면 즉시 반영돼야 한다."""
    out = convert.to_parquet(src, source="csv")
    my_dict = tmp_path / "사전.csv"
    text = my_dict.write_text(
        DICT.read_text(encoding="utf-8-sig").replace(
            "(A면) T_Block UNIT Gap Offset 6Zone", "여섯번째 존"
        ),
        encoding="utf-8-sig",
    )
    r = parse.load_readings(out, my_dict, source="parquet")
    assert "여섯번째 존" in set(r[S.ITEM_NAME])


def test_dtypes_are_in_the_file(src):
    """parquet 을 고른 이유. 읽을 때 dtype 을 지정하지 않아도 item_id 가
    문자열로 온다 - int64 로 돌아와 사전 조인이 통째로 미매칭되는 사고가
    구조적으로 불가능하다."""
    out = convert.to_parquet(src, source="csv")
    stored = pd.read_parquet(out)
    assert not pd.api.types.is_numeric_dtype(stored[S.ITEM])
    assert stored[S.AT].dtype.kind == "M"
    assert pd.api.types.is_numeric_dtype(stored[S.VALUE])


# ── 이 기능의 존재 이유 ─────────────────────────────────────────────────

def test_parquet_input_works_without_xlwings(tmp_path, monkeypatch):
    """폐쇄망 시나리오. 사내 PC 에서 xlsx 를 한 번 변환해 두면, 받는 쪽은
    xlwings·pywin32 없이 parquet 만으로 끝까지 돌아야 한다. 그게 안 되면 이
    기능은 의존성을 옮겼을 뿐 없앤 것이 아니다.

    이미 import 된 모듈을 지우고 xlwings 를 막은 채 **다시 import** 하는 것이
    핵심이다(test_coating_parse.py 의 같은 테스트와 같은 이유)."""
    import importlib
    import sys

    import app.coating

    src = tmp_path / "원본.csv"
    shutil.copy(SAMPLE, src)
    out = convert.to_parquet(src, source="csv")

    for name in ("app.coating.parse", "app.coating.excel_source", "app.excel.workbook"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.delattr(app.coating, "excel_source", raising=False)
    monkeypatch.setitem(sys.modules, "xlwings", None)
    monkeypatch.setitem(sys.modules, "pythoncom", None)

    fresh = importlib.import_module("app.coating.parse")
    r = fresh.load_readings(out, DICT, source="parquet")
    assert len(r) == 31
    assert fresh.unknown_item_ids(r) == []


# ── 실수를 막는다 ───────────────────────────────────────────────────────

def test_unreadable_header_fails_at_convert_time(tmp_path):
    """배포하기 전에 안다. 변환은 통과하고 나중에 읽을 때 죽으면, 못 쓰는
    파일이 이미 사람들 손에 가 있다."""
    bad = tmp_path / "이상한헤더.csv"
    bad.write_text("구분,비고,수량\nA,B,1\n", encoding="utf-8-sig")
    with pytest.raises(ValueError) as e:
        convert.to_parquet(bad, source="csv")
    assert "구분" in str(e.value)          # 실제 헤더를 보여준다
    assert not (tmp_path / "이상한헤더.parquet").exists()   # 반쪽 파일을 안 남긴다


def test_overwriting_the_input_is_refused(tmp_path):
    """DRM 때문에 다시 만들기 어려운 파일이다. 실패하면 원본이 사라진 채로
    끝나는 상황을 애초에 만들지 않는다."""
    p = tmp_path / "원본.parquet"
    convert.to_parquet(SAMPLE, p, source="csv")
    with pytest.raises(ValueError, match="같은 파일"):
        convert.to_parquet(p, p, source="parquet")


# ── 편의 ────────────────────────────────────────────────────────────────

def test_default_out_path_keeps_name_and_place(tmp_path):
    """--out 을 안 주면 같은 위치·같은 이름, 확장자만 바뀐다."""
    assert convert.default_out_path(tmp_path / "raw" / "2026년1월.xlsx") == (
        tmp_path / "raw" / "2026년1월.parquet"
    )


def test_summary_reports_unknown_items(tmp_path):
    """배포 전에 사전 갱신이 필요한지 알려준다. 실패가 아니라 경고다."""
    csv = tmp_path / "모르는항목.csv"
    csv.write_text(
        "lot_id,worked_at,product,item_id,value\n"
        "L1,2026-01-31 18:55,BNB48X1,99999999,163\n",
        encoding="utf-8-sig",
    )
    out = convert.to_parquet(csv, source="csv")
    text = convert.summarize(pd.read_parquet(out), out, csv)
    assert "99999999" in text
    assert "사전에 없는 항목" in text


def test_cli_converts_and_prints_where_it_went(src, capsys):
    out = convert.main(["--input", str(src)])
    assert Path(out).exists()
    printed = capsys.readouterr().out
    assert "원본.parquet" in printed
    assert "행 31" in printed


def test_cli_reports_missing_input(tmp_path):
    with pytest.raises(SystemExit) as e:
        convert.main(["--input", str(tmp_path / "없는파일.csv")])
    assert "없는파일.csv" in str(e.value)
    assert "COATING_INPUT_PATH" in str(e.value)
