"""원본 CSV 파싱 — item_id 가 숫자로 읽히면 사전 조인이 통째로 실패한다."""
from pathlib import Path

import pandas as pd

from app.coating import parse
from app.coating import schemas as S

# 테스트는 backend/data/ 를 읽지 않는다 — gitignore 대상이라 새로 클론한 곳에서
# 전부 실패한다. 입력은 fixture, 사전은 패키지에 든 스키마를 쓴다.
SAMPLE = Path(__file__).parent / "fixtures" / "coating" / "sample_long.csv"
DICT = parse.DEFAULT_DICT_PATH


def test_item_id_stays_string():
    """'30030838' 이 30030838.0 으로 읽히면 사전의 문자열 키와 안 맞아
    모든 행이 미매칭이 된다. 예외는 안 나고 결과만 빈다."""
    d = parse.load_item_dictionary(DICT)
    assert not pd.api.types.is_numeric_dtype(d[S.ITEM])
    assert "30030838" in set(d[S.ITEM])


def test_load_readings_joins_dictionary_and_keeps_row_order():
    """row_no 는 분 단위 중복을 '마지막 값'으로 접을 때의 유일한 기준이다.
    없으면 어느 값이 마지막인지 판정할 수 없다."""
    r = parse.load_readings(SAMPLE, DICT)
    assert len(r) == 31
    assert list(r[S.ROW_NO]) == list(range(31))
    assert r[S.AT].dtype.kind == "M"
    assert set(r[S.IO].dropna()) == {S.IO_INPUT, S.IO_OUTPUT}


def test_zone_is_parsed_for_zoned_items_only():
    """스칼라 제어값(OS Gap 등)에는 zone 이 없다. 0 으로 채우면 1번 zone 과
    섞인다."""
    r = parse.load_readings(SAMPLE, DICT)
    gap6 = r[r[S.ITEM] == "30030843"].iloc[0]
    assert gap6[S.ZONE] == 6
    os_gap = r[r[S.ITEM] == "10030271"].iloc[0]
    assert pd.isna(os_gap[S.ZONE])


def test_unknown_item_ids_is_empty_for_sample():
    """샘플의 모든 항목이 사전에 있다. 실데이터에서 비어있지 않으면
    사전을 갱신해야 한다는 신호다."""
    r = parse.load_readings(SAMPLE, DICT)
    assert parse.unknown_item_ids(r) == []


def test_product_spec_carries_target_and_tolerance():
    """합격 판정(모든 zone 이 목표±tolerance)의 기준값. 코드에 18.23 을
    적어두면 제품이 늘 때마다 코드를 고쳐야 한다."""
    spec = parse.load_product_spec()
    row = spec[spec["product"] == "BNB48X1"].iloc[0]
    assert row["target"] == 18.23
    assert row["tolerance"] == 0.4


def test_unknown_item_ids_reports_missing(tmp_path):
    """사전에 없는 항목은 버리지 않고 보고한다 — 실데이터에 우리가 모르는
    변수가 있다는 사실 자체가 리포트의 발견 항목이다."""
    csv = tmp_path / "x.csv"
    csv.write_text(
        "lot_id,worked_at,product,item_id,item_name,value\n"
        "L1,2026-01-31 18:55,P1,99999999,,1.0\n",
        encoding="utf-8-sig",
    )
    r = parse.load_readings(csv, DICT)
    assert parse.unknown_item_ids(r) == ["99999999"]
