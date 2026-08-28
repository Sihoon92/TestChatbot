"""원본 CSV → readings DataFrame.

이 모듈만 파일을 읽는다. 이후 단계는 전부 DataFrame 만 받는 순수 함수다.

item_id 를 문자열로 고정하는 것이 이 파일의 유일한 존재 이유에 가깝다.
pandas 는 숫자로 보이는 열을 int64 로 읽는데, 그러면 사전(문자열 키)과
조인이 전부 미매칭이 되고 예외 없이 빈 결과가 나온다.
"""
from pathlib import Path

import pandas as pd

from app.coating import schemas as S

# 인코딩 후보. 실데이터는 사내 MES·엑셀 export 라 cp949 인 경우가 흔하고,
# 우리가 만든 사전·픽스처는 utf-8-sig 다. 둘을 순서대로 시도한다.
#
# 순서가 핵심이다. utf-8 로 저장된 한글을 cp949 로 읽으면 예외가 나기도 하지만
# 조용히 성공하기도 한다("코팅" -> 엉뚱한 한자 3자). 조용히 성공하면 깨진 글자가
# 에러 없이 파이프라인 끝까지 흘러간다. 반대 방향(cp949 한글을 utf-8 로)은 거의
# 항상 즉시 예외라 폴백으로 복구된다. 그래서 utf-8 을 먼저 시도한다.
DEFAULT_ENCODINGS = ("utf-8-sig", "cp949")


def read_csv_any(path: str | Path, encodings=None, **kwargs) -> pd.DataFrame:
    """후보 인코딩을 순서대로 시도해 CSV 를 읽는다.

    전부 실패하면 무엇을 시도했는지 적어서 올린다 — 원본 UnicodeDecodeError 의
    "byte 0xba in position 69" 만으로는 무엇을 고쳐야 할지 알 수 없다.
    """
    tried = list(encodings) if encodings else list(DEFAULT_ENCODINGS)
    for enc in tried:
        try:
            return pd.read_csv(path, encoding=enc, **kwargs)
        except UnicodeDecodeError:
            continue
    raise ValueError(
        f"CSV 인코딩을 판별하지 못했다: {path}\n"
        f"  시도한 인코딩: {tried}\n"
        "  다른 인코딩이면 COATING_CSV_ENCODINGS 에 추가하거나 --encoding 으로 지정한다."
    )


# 항목 사전과 제품 스펙은 '데이터' 가 아니라 '스키마' 다 — item_id 가 무엇을
# 뜻하는지 모르면 코드가 아무것도 못 한다. 그래서 backend/data/(gitignore)가
# 아니라 패키지 안에 두고 함께 배포한다.
_META = Path(__file__).with_name("meta")
DEFAULT_DICT_PATH = _META / "item_dictionary.csv"
DEFAULT_SPEC_PATH = _META / "product_spec.csv"


def load_item_dictionary(path: str | Path | None = None, encodings=None) -> pd.DataFrame:
    d = read_csv_any(path or DEFAULT_DICT_PATH, encodings, dtype={S.ITEM: str})
    return d[[S.ITEM, S.ITEM_NAME, S.IO, S.ROLE, S.ZONE]]


def load_product_spec(path: str | Path | None = None, encodings=None) -> pd.DataFrame:
    """제품별 목표 L/L 과 스펙 폭. 합격 판정(모든 zone AND)의 기준값이다."""
    return read_csv_any(path or DEFAULT_SPEC_PATH, encodings)


def load_readings(
    csv_path: str | Path,
    dict_path: str | Path | None = None,
    encodings=None,
) -> pd.DataFrame:
    raw = read_csv_any(csv_path, encodings, dtype={S.ITEM: str})
    # 원본에도 item_name 열이 있지만 대부분 비어 있다. 사전 것을 쓴다.
    raw = raw.drop(columns=[S.ITEM_NAME], errors="ignore")
    raw[S.AT] = pd.to_datetime(raw[S.AT])
    raw[S.VALUE] = pd.to_numeric(raw[S.VALUE], errors="coerce")
    # 파일에 적힌 순서가 곧 "나중에 찍힌 값" 의 순서다. 인덱스를 열로 박아
    # 정렬·groupby 를 거쳐도 그 순서를 잃지 않게 한다.
    raw[S.ROW_NO] = range(len(raw))
    return raw.merge(load_item_dictionary(dict_path, encodings), on=S.ITEM, how="left")


def unknown_item_ids(readings: pd.DataFrame) -> list[str]:
    """사전에 없는 항목 ID. 버리지 않고 보고한다."""
    return sorted(readings.loc[readings[S.IO].isna(), S.ITEM].unique().tolist())
