"""원본 CSV → readings DataFrame.

이 모듈만 파일을 읽는다. 이후 단계는 전부 DataFrame 만 받는 순수 함수다.

item_id 를 문자열로 고정하는 것이 이 파일의 유일한 존재 이유에 가깝다.
pandas 는 숫자로 보이는 열을 int64 로 읽는데, 그러면 사전(문자열 키)과
조인이 전부 미매칭이 되고 예외 없이 빈 결과가 나온다. 그래서 원본은 전부
문자열로 읽고, 숫자·시각 변환은 _finalize 가 표준 컬럼명 위에서 한 번에 한다.
"""
import codecs
from pathlib import Path

import pandas as pd

from app.coating import excel_source
from app.coating import schemas as S

# 인코딩 후보. 실데이터는 사내 MES·엑셀 export 라 cp949 인 경우가 흔하고,
# 우리가 만든 사전·픽스처는 utf-8-sig 다. 둘을 순서대로 시도한다.
#
# 순서가 핵심이다. utf-8 로 저장된 한글을 cp949 로 읽으면 예외가 나기도 하지만
# 조용히 성공하기도 한다("코팅" -> 엉뚱한 한자 3자). 조용히 성공하면 깨진 글자가
# 에러 없이 파이프라인 끝까지 흘러간다. 반대 방향(cp949 한글을 utf-8 로)은 거의
# 항상 즉시 예외라 폴백으로 복구된다. 그래서 utf-8 을 먼저 시도한다.
DEFAULT_ENCODINGS = ("utf-8-sig", "cp949")


# 바이너리 시그니처. 엑셀을 이름만 .csv 로 바꿔 넣는 일이 잦은데, 이걸
# "인코딩 판별 실패" 로 말하면 인코딩만 몇 시간을 뒤지게 된다.
_SIGNATURES = (
    (b"<## NASCA DRM", "사내 문서보안(NASCA DRM)으로 암호화된 파일"),
    (b"PK\x03\x04", "엑셀 파일(.xlsx) 또는 zip"),
    (b"\xd0\xcf\x11\xe0", "옛 엑셀 파일(.xls)"),
    (b"%PDF", "PDF 파일"),
)

# BOM 은 추측이 아니라 파일이 스스로 밝힌 정답이다. 후보 목록보다 먼저 본다.
# utf-32 를 utf-16 보다 먼저 검사해야 한다(FF FE 00 00 은 FF FE 로도 시작한다).
_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)

_HOWTO_DEFAULT = "  엑셀에서 '다른 이름으로 저장 > CSV UTF-8' 로 다시 내보낸다."

# DRM 은 파일 형식 문제가 아니라 권한 문제다. 다시 내보내라고 하면 안 된다 -
# 등록되지 않은 프로세스(python.exe)는 복호화된 내용을 아예 못 본다.
_HOWTO = {
    b"<## NASCA DRM": (
        "  DRM 은 등록된 프로그램에만 복호화해서 보여준다. python 은 암호문을 그대로 받는다.\n"
        "  보안팀에 둘 중 하나를 요청한다:\n"
        "    (1) 이 CSV 의 DRM 해제(복호화) 반출\n"
        "    (2) 분석용 python.exe 를 DRM 예외 애플리케이션으로 등록\n"
        "  사내 정책이 허용하면 엑셀로 연 뒤 '다른 이름으로 저장 > CSV UTF-8' 로\n"
        "  받은 사본이 평문일 수 있다(정책에 따라 사본도 자동 암호화된다)."
    ),
}

_HEAD_BYTES = 4096


def sniff_encoding(head: bytes) -> str | None:
    """앞부분 바이트만 보고 확실할 때만 인코딩을 단정한다. 아니면 None.

    확실한 근거는 둘뿐이다: BOM, 그리고 NUL 바이트. 일반 CSV 텍스트에는
    NUL 이 나올 수 없으므로, NUL 이 규칙적으로 섞여 있으면 BOM 없는
    utf-16 이다(짝수 위치가 비면 BE, 홀수 위치가 비면 LE).
    """
    for bom, enc in _BOMS:
        if head.startswith(bom):
            return enc
    if b"\x00" not in head[:512]:
        return None
    even = head[0:512:2].count(0)
    odd = head[1:512:2].count(0)
    if odd > even * 4:
        return "utf-16-le"
    if even > odd * 4:
        return "utf-16-be"
    return None


def read_csv_any(
    path: str | Path, encodings=None, force_encoding=None, **kwargs
) -> pd.DataFrame:
    """CSV 를 읽는다. BOM/시그니처로 단정되면 그것을, 아니면 후보를 차례로.

    force_encoding 이 오면(--encoding) 판별을 건너뛰고 그것만 쓴다. 자동 판별이
    틀리는 파일을 사람이 덮어쓸 수 있어야 override 가 override 다.

    전부 실패하면 파일이 실제로 어떤 바이트로 시작하는지까지 적어서 올린다.
    "byte 0xba in position 69" 만으로는 원인을 좁힐 수 없고, 재현이 안 되는
    사내 PC 에서는 이 한 줄이 왕복 한 번을 없앤다.
    """
    head = Path(path).open("rb").read(_HEAD_BYTES)
    for sig, kind in _SIGNATURES:
        if head.startswith(sig):
            raise ValueError(
                f"CSV 가 아니다: {path}\n"
                f"  파일 내용이 {kind}이다(첫 바이트 {head[:4].hex(' ')}).\n"
                + _HOWTO.get(sig, _HOWTO_DEFAULT)
            )

    if force_encoding:
        tried = [force_encoding]
    else:
        tried = list(encodings) if encodings else list(DEFAULT_ENCODINGS)
        sniffed = sniff_encoding(head)
        if sniffed and sniffed not in tried:
            # 근거가 확실하므로 후보보다 앞에 둔다.
            tried.insert(0, sniffed)

    errors = []
    for enc in tried:
        try:
            return pd.read_csv(path, encoding=enc, **kwargs)
        except UnicodeDecodeError as e:
            errors.append(f"{enc}: position {e.start}")
    raise ValueError(
        f"CSV 인코딩을 판별하지 못했다: {path}\n"
        f"  시도: {', '.join(errors)}\n"
        f"  첫 16바이트: {head[:16].hex(' ')}\n"
        "  COATING_CSV_ENCODINGS 에 인코딩을 추가하거나 --encoding 으로 지정한다."
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
    force_encoding=None,
    *,
    source: str = "csv",
    sheet: str | None = None,
) -> pd.DataFrame:
    """원본 → readings. source 가 csv 든 xlsx 든 같은 계약으로 나온다.

    xlsx 경로는 DRM 때문에 존재한다(모듈 excel_source 참고). 읽는 방법만
    다르고 그 뒤 정규화·사전 조인은 완전히 공유한다 - 갈라지면 두 경로의
    결과가 조용히 달라진다."""
    raw = read_source(csv_path, encodings, force_encoding, source=source, sheet=sheet)
    return _finalize(raw, dict_path, encodings, csv_path)


def read_source(
    path, encodings=None, force_encoding=None, *, source="csv", sheet=None
) -> pd.DataFrame:
    """원본을 형식별로 읽기만 한다. 정규화도 사전 조인도 하지 않는다.

    load_readings 와 convert 가 공유한다 - 분기를 복제하면 두 경로가 언젠가
    달라지고, 그때 변환한 parquet 과 직접 읽은 결과가 조용히 어긋난다.
    """
    if source == "parquet":
        # 이미 normalize 를 거쳐 담긴 파일이다. dtype 이 파일 안에 있어서 지정할
        # 것이 없다 - item_id 가 int64 로 돌아오는 사고가 구조적으로 불가능하다.
        # (그래도 _finalize 를 지난다. 손으로 만든 parquet 이 표준 이름이 아니어도
        #  별칭 표가 그대로 동작하고, 계약 검증도 건너뛰지 않는다.)
        raw = pd.read_parquet(path)
    elif source == "xlsx":
        raw = excel_source.read_long_table(path, sheet)
    elif source == "csv":
        # 모든 열을 문자열로 읽는다. 숫자·시각 변환은 _finalize 가 표준 이름으로
        # 바꾼 뒤에 한다 - xlsx 경로(excel_source)와 정확히 같은 계약이다.
        #
        # 예전에는 dtype={item_id: str} 로 그 열만 고정했는데, 헤더가 다른 언어면
        # ("项目编号") 그 키가 파일에 없어 pandas 가 **조용히 무시**한다. 그러면
        # item_id 가 int64 로 읽히고 사전(문자열 키) 조인이 예외 없이 전부
        # 미매칭된다. 결측이 섞이면 float64 가 되어 '30030859.0' 까지 된다.
        raw = read_csv_any(path, encodings, force_encoding, dtype=str)
    else:
        raise ValueError(f"알 수 없는 입력 형식: {source!r} (csv | xlsx | parquet)")
    return raw


# 확장자로 확실히 알 수 있는 것만 적는다. ".txt" 는 CSV 라는 보장이 없어 뺐다 -
# 모르는 확장자는 추측하지 않고 설정 기본값으로 떨어진다.
SUFFIX_FORMATS = {
    ".csv": "csv",
    ".xlsx": "xlsx",
    ".xlsm": "xlsx",
    ".xls": "xlsx",
    ".parquet": "parquet",
    ".pq": "parquet",
}


def format_for(path, explicit: str | None = None, default: str | None = None) -> str:
    """입력 형식을 정한다: 명시(--format) > 확장자 > 설정 기본값.

    확장자를 보는 이유는 형식이 셋으로 늘었기 때문이다. 매번 --format 을 적게 하면
    빼먹는 일이 생기고, 그 실수는 조용히 지나가기도 한다(csv 에 --format xlsx 를
    줘도 Excel 이 csv 를 열어줘서 통과한다 - 대신 인코딩 판별 로직을 통째로
    건너뛴다). 확장자가 이미 답을 알고 있으면 사람에게 다시 묻지 않는다.

    설정을 무시하는 것은 아니다. 확장자를 모르면(사내 MES 가 내리는 '.dat' 같은
    것) 설정이 정한다.
    """
    if explicit:
        return explicit
    return SUFFIX_FORMATS.get(Path(path).suffix.lower()) or default or "csv"


def normalize(raw: pd.DataFrame, path=None) -> pd.DataFrame:
    """검증 + 표준명 변환 + 타입 보정까지. 사전과 row_no 는 아직 안 붙인다.

    변환(app.coating.convert)이 parquet 에 담는 것이 딱 여기까지다. 사전을 담으면
    사전을 고칠 때마다 파일을 다시 만들어야 하고, 그 순간 그 파일은 원본이 아니라
    파생물이 된다. 사전은 파일 밖에 두고 읽을 때 붙인다.
    """
    mapping = S.require_columns(
        raw.columns,
        path,
        "  첫 행이 헤더인지, 위에 제목 행이 끼어 있지 않은지 확인한다.\n"
        "  헤더 이름이 다르면 원본에서 위 이름으로 바꿔 저장한다.",
    )
    # 다국어 헤더(批次号·작업일시…)를 표준 이름으로 바꾸는 유일한 지점이다.
    # 여기서부터 아래로는 아무도 원본이 무슨 언어였는지 몰라도 된다.
    raw = raw.rename(columns=mapping)
    # 원본에도 item_name 열이 있지만 대부분 비어 있다. 사전 것을 쓴다.
    # rename 뒤에 와야 "项目名称" 도 같이 버려진다 - 안 그러면 같은 데이터인데
    # 헤더 언어에 따라 결과 DataFrame 의 컬럼 수가 달라진다.
    raw = raw.drop(columns=[S.ITEM_NAME], errors="ignore")
    raw[S.AT] = pd.to_datetime(raw[S.AT])
    raw[S.VALUE] = pd.to_numeric(raw[S.VALUE], errors="coerce")
    return raw


def _finalize(raw: pd.DataFrame, dict_path, encodings, path=None) -> pd.DataFrame:
    """모든 입력 경로가 만나는 곳. 정규화 + 순서 보존 + 사전 조인.

    이미 정규화된 입력(parquet)에 다시 적용해도 결과가 같다 - require_columns 는
    항등 매핑이 되고, to_datetime·to_numeric 은 맞은 타입에 no-op 이며, row_no 는
    parquet 이 행 순서를 보존하므로 같은 값으로 재생성된다. 그래서 parquet 입력에
    특수 분기가 필요 없다.
    """
    raw = normalize(raw, path)
    # 파일에 적힌 순서가 곧 "나중에 찍힌 값" 의 순서다. 인덱스를 열로 박아
    # 정렬·groupby 를 거쳐도 그 순서를 잃지 않게 한다.
    raw[S.ROW_NO] = range(len(raw))
    # 사전에는 force_encoding 을 넘기지 않는다. 그건 사용자 데이터 파일에 대한
    # 지시이고, 사전은 우리가 utf-8-sig 로 배포하는 별개의 파일이다. 같이 강제하면
    # --encoding cp949 한 번에 사전 조인이 통째로 깨진다.
    return raw.merge(load_item_dictionary(dict_path, encodings), on=S.ITEM, how="left")

def unknown_item_ids(readings: pd.DataFrame) -> list[str]:
    """사전에 없는 항목 ID. 버리지 않고 보고한다."""
    return sorted(readings.loc[readings[S.IO].isna(), S.ITEM].unique().tolist())
