"""원본 CSV 파싱 — item_id 가 숫자로 읽히면 사전 조인이 통째로 실패한다."""
from pathlib import Path

import pandas as pd
import pytest

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


def test_reads_cp949_csv(tmp_path):
    """실데이터는 MES·엑셀에서 나와 cp949 인 경우가 많다. utf-8 고정이면
    한글이 한 글자만 있어도 UnicodeDecodeError 로 파이프라인 전체가 멈춘다."""
    csv = tmp_path / "cp949.csv"
    csv.write_bytes(
        (
            "lot_id,worked_at,product,item_id,item_name,value\n"
            "L1,2026-01-31 18:55,비앤비,10030271,오에스 갭,163\n"
        ).encode("cp949")
    )
    r = parse.load_readings(csv, DICT)
    assert list(r[S.PRODUCT]) == ["비앤비"]


def test_utf8_korean_is_not_misread_as_cp949(tmp_path):
    """후보 순서 보장. '코팅' 의 utf-8 바이트는 cp949 에서 **예외 없이** 다른
    글자 3자로 디코드된다. DEFAULT_ENCODINGS 를 뒤집으면 에러 하나 없이 깨진
    제품명이 파이프라인 끝까지 흘러간다. 비ASCII 를 이 한 단어로 제한하는 것이
    이 테스트의 요점이다 — 예외를 내는 한글이 한 글자라도 섞이면 폴백이
    되살려버려서 순서가 뒤집혀도 통과한다."""
    csv = tmp_path / "utf8.csv"
    csv.write_bytes(
        (
            "lot_id,worked_at,product,item_id,item_name,value\n"
            "L1,2026-01-31 18:55,코팅,10030271,,163\n"
        ).encode("utf-8")
    )
    r = parse.load_readings(csv, DICT)
    assert list(r[S.PRODUCT]) == ["코팅"]


def test_undecodable_csv_names_the_encodings_it_tried(tmp_path):
    """'byte 0xff in position 0' 만 보고는 무엇을 고쳐야 할지 알 수 없다.

    utf-16 BOM 이 있는 파일이라 자동 판별이면 읽힌다. 여기서는 --encoding 으로
    강제했을 때 그 지시가 지켜지는지(그리고 실패를 설명하는지)를 본다."""
    csv = tmp_path / "utf16.csv"
    csv.write_bytes(
        "lot_id,worked_at,product,item_id,item_name,value\n".encode("utf-16")
    )
    with pytest.raises(ValueError) as e:
        parse.load_readings(csv, DICT, force_encoding="utf-8")
    assert "utf-8" in str(e.value)
    assert "utf16.csv" in str(e.value)


def _csv_bytes(encoding):
    return (
        "lot_id,worked_at,product,item_id,item_name,value\n"
        "L1,2026-01-31 18:55,비앤비,10030271,오에스 갭,163\n"
    ).encode(encoding)


def test_reads_utf16_with_bom(tmp_path):
    """Windows PowerShell 5.1 의 Out-File / '>' 기본 출력이 utf-16 BOM 이다.
    MES·SSMS export 도 흔하다. BOM 은 추측이 아니라 파일이 스스로 밝힌
    정답이므로 후보 목록보다 먼저 본다."""
    csv = tmp_path / "utf16.csv"
    csv.write_bytes(_csv_bytes("utf-16"))
    r = parse.load_readings(csv, DICT)
    assert list(r[S.PRODUCT]) == ["비앤비"]


def test_reads_utf16le_without_bom(tmp_path):
    """BOM 이 없어도 본문 절반이 0x00 이면 utf-16 이다. 일반 CSV 텍스트에는
    NUL 이 나올 수 없으므로 오탐이 아니다."""
    csv = tmp_path / "utf16le.csv"
    csv.write_bytes(_csv_bytes("utf-16-le"))
    r = parse.load_readings(csv, DICT)
    assert list(r[S.PRODUCT]) == ["비앤비"]


def test_excel_file_renamed_to_csv_is_not_reported_as_encoding_problem(tmp_path):
    """xlsx 를 이름만 .csv 로 바꿔 넣는 일이 잦다. 이걸 '인코딩 판별 실패' 로
    말하면 인코딩만 몇 시간 뒤진다. 파일 종류를 짚어줘야 한다."""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml", "<workbook/>")
    csv = tmp_path / "가짜.csv"
    csv.write_bytes(buf.getvalue())

    with pytest.raises(ValueError) as e:
        parse.load_readings(csv, DICT)
    assert "엑셀" in str(e.value)


def test_undecodable_file_shows_first_bytes(tmp_path):
    """재현이 안 되는 환경에서 원인을 한 번에 좁히려면 파일이 실제로 어떤
    바이트로 시작하는지가 있어야 한다."""
    csv = tmp_path / "이상한.csv"
    csv.write_bytes(bytes([0x8F, 0xFF, 0xFE, 0x41, 0x42]))
    with pytest.raises(ValueError) as e:
        parse.load_readings(csv, DICT, encodings=["utf-8"])
    assert "8f ff fe" in str(e.value).lower()


def test_drm_wrapped_file_is_named_as_drm_not_encoding(tmp_path):
    """사내 문서보안(NASCA DRM)이 감싼 파일은 어떤 인코딩으로도 안 읽힌다.
    이걸 '인코딩 판별 실패' 로 말하면 인코딩을 며칠 뒤지게 된다. 실제로
    한 번 겪었다(첫 바이트 3c 23 23 20 4e 41 53 43 41 = '<## NASCA')."""
    csv = tmp_path / "보안.csv"
    csv.write_bytes(b"<## NASCA DRM FILE ##>" + bytes(range(200, 256)))
    with pytest.raises(ValueError) as e:
        parse.load_readings(csv, DICT)
    assert "DRM" in str(e.value)
