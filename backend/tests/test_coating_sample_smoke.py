"""--sample 로 준 실제 파일이 파이프라인 계약을 만족하는가.

--sample 없이는 통째로 skip 된다.

    pytest tests/test_coating_sample_smoke.py --sample data/coating/raw/실데이터.csv
    pytest tests/test_coating_sample_smoke.py --sample raw/원본.xlsx --sample-sheet 데이터

픽스처 테스트와 역할이 다르다. 저기는 "이 데이터에서 이 값이 나온다" 를 고정하고,
여기는 "처음 보는 파일이 들어와도 계약이 지켜지는가" 를 본다. 그래서 값을 단언하지
않는다 - 행 수나 특정 item_id 를 박으면 파일이 바뀔 때마다 테스트를 고쳐야 해서
결국 아무도 안 쓰게 된다.

여기서 잡으려는 것은 전부 **예외 없이 조용히 틀리는** 실패들이다. 터지는 실패는
어차피 load_readings 가 문장으로 말해준다.
"""
from pathlib import Path

import pandas as pd
import pytest

from app.coating import parse, report
from app.coating import schemas as S
from app.config import get_settings


@pytest.fixture(scope="session")
def readings(sample_path, sample_source, sample_sheet) -> pd.DataFrame:
    """한 번만 읽는다 - 실데이터는 크고, xlsx 는 Excel 을 띄운다.

    인코딩 후보는 .env 단일 출처를 따른다(COATING_CSV_ENCODINGS).
    """
    return parse.load_readings(
        sample_path,
        encodings=get_settings().coating_csv_encoding_list,
        source=sample_source,
        sheet=sample_sheet,
    )


def test_sample_satisfies_the_column_contract(readings):
    """헤더가 무슨 언어였든 이 지점부터는 표준 이름만 보여야 한다."""
    missing = [c for c in S.REQUIRED_COLUMNS if c not in readings.columns]
    assert not missing, f"표준 이름으로 안 바뀐 컬럼: {missing}"
    assert len(readings) > 0, "행이 하나도 없다 - 헤더만 있는 파일이거나 시트를 잘못 골랐다"


def test_sample_item_ids_join_to_the_dictionary(readings):
    """이 프로젝트에서 가장 조용한 실패. item_id 가 숫자로 읽히면 사전(문자열 키)
    조인이 예외 없이 전부 미매칭되고, 빈 리포트가 정상처럼 나온다.

    사전에 없는 항목은 실데이터에 있을 수 있으므로 '하나도 안 걸림' 만 실패로
    본다. 모르는 항목은 세어서 알려준다 - 사전을 갱신하라는 신호다.
    """
    assert not pd.api.types.is_numeric_dtype(readings[S.ITEM]), (
        f"item_id 가 숫자로 읽혔다({readings[S.ITEM].dtype}). 사전 조인이 전부 깨진다."
    )
    unknown = parse.unknown_item_ids(readings)
    matched = int(readings[S.IO].notna().sum())
    assert matched > 0, (
        "사전에 걸린 항목이 하나도 없다.\n"
        f"  모르는 item_id {len(unknown)}개 중 앞 10개: {unknown[:10]}"
    )


def test_sample_worked_at_is_fully_parsed(readings):
    """NaT 가 되면 그 행이 lot 구간에서 조용히 빠진다. 예외는 안 난다."""
    assert readings[S.AT].dtype.kind == "M", f"시각이 아니다: {readings[S.AT].dtype}"
    bad = int(readings[S.AT].isna().sum())
    assert bad == 0, f"시각을 못 읽은 행 {bad}개 / 전체 {len(readings)}개"


def test_sample_values_are_numeric(readings):
    """to_numeric(errors='coerce') 는 조용히 NaN 을 만든다. 전부 NaN 이면
    value 로 엉뚱한 열을 잡은 것이다(예: 단위 문자열이 든 열)."""
    assert pd.api.types.is_numeric_dtype(readings[S.VALUE])
    good = int(readings[S.VALUE].notna().sum())
    assert good > 0, "value 가 전부 숫자가 아니다 - 다른 열을 value 로 잡았을 수 있다"


def test_report_runs_end_to_end_on_sample(
    tmp_path, sample_path, sample_source, sample_sheet
):
    """읽히는 것과 리포트가 나오는 것은 다른 문제다. 판정까지 실제로 돈다."""
    md_path, html_path = report.run(
        sample_path,
        out_dir=str(tmp_path),
        source=sample_source,
        sheet=sample_sheet,
    )
    assert Path(md_path).stat().st_size > 0
    assert Path(html_path).stat().st_size > 0
