"""셀 값 정규화 — 파이프라인 전체에서 이 모듈만 사용한다.

정규화가 여러 곳에 흩어지면 같은 금형이 둘로 갈라지고(대장의 'RX28312' 와
이력표의 '#RX41194'), 조인 키가 영영 맞지 않는다. 그래서 한 곳에만 둔다.

xlwings 가 돌려주는 값의 타입은 셀 서식에 따라 달라진다: 숫자는 float,
날짜는 datetime, 빈 칸은 None. 정수 셀도 float(1.0) 로 오기 때문에 그대로
str() 하면 '1.0' 이 되어 매칭이 전부 깨진다 — cell_to_text 가 그것을 막는다.
"""
import re
from datetime import date, datetime

# 셀 값 → 텍스트 변환은 금형 도메인이 아니라 '엑셀 값 다루기' 의 문제라
# 순수 계층(app.excel.grid)에 산다. 여기서는 재수출만 한다 — 이 모듈이
# 정규화의 단일 창구라는 성질은 그대로 유지된다.
from app.excel.grid import cell_to_text

# 금형번호 칸에 들어올 수 있지만 금형이 아닌 값들. 실물 IQC 시트의 summary
# 표와 소계 행에서 나온다. role="summary" 로 걸러지는 것이 1차 방어이고,
# 이것은 detail 표 안에 섞인 소계 행에 대한 2차 방어다.
_NOT_A_MOLD = {"소계", "합계", "총계", "계", "-", "n/a", "na"}

# MES 의 상태 어휘 → 대시보드 MoldStatus. 실물 어휘가 다르면 여기만 고친다.
# 인식 못 한 값은 추측하지 않고 None 을 돌려준다 — 호출자가 원문을 수집해
# 드러내고, 사람이 이 표를 고치는 것이 올바른 대응이다.
STATUS_MAP: dict[str, str] = {
    "사용중": "in_use",
    "사용": "in_use",
    "가동중": "in_use",
    "대기중": "standby",
    "대기": "standby",
    "보관중": "standby",
    "보관": "standby",
    "수리중": "repair",
    "수리": "repair",
    "정비중": "repair",
    "폐기": "retired",
    "폐기됨": "retired",
    "불용": "retired",
}


# JIG 관리대장의 '위치' → 대시보드 MoldStatus.
#
# 상태를 적은 열이 따로 없다. 금형이 **어디에 있는가**가 곧 상태다: 설비에
# 있으면 가동 중이고, 수리실에 있으면 수리 중이다. 보관 위치의 이름은 공장마다
# 다르므로(입고 대기 보관함·통합 Jig Room·사용 대기 보관함·반납 대기 보관함…)
# 하나씩 나열하는 대신 '설비/수리/폐기'만 알아보고 나머지는 대기로 본다 —
# 보관함 이름이 하나 늘 때마다 금형이 화면에서 사라지면 안 된다.
_LOCATION_IN_USE = "설비"
_LOCATION_KEYWORDS = (
    ("수리", "repair"),
    ("폐기", "retired"),
    ("불용", "retired"),
)


def status_from_location(raw: object) -> str | None:
    """관리대장의 위치 → MoldStatus. 위치를 못 읽으면 None."""
    text = cell_to_text(raw)
    if text is None:
        return None
    squeezed = re.sub(r"\s+", "", text)
    if squeezed == _LOCATION_IN_USE:
        return "in_use"
    for keyword, status in _LOCATION_KEYWORDS:
        if keyword in squeezed:
            return status
    return "standby"


def normalize_text(s: str | None) -> str:
    """비교용 정규화: 앞뒤 공백 제거, 연속 공백 1개로, 대소문자 무시."""
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s).strip()).casefold()


def normalize_mold_no(raw: object) -> str | None:
    """'#RX41194' → 'RX41194'. 금형이 아닌 값(소계/합계/빈칸)은 None."""
    text = cell_to_text(raw)
    if text is None:
        return None
    cleaned = text.strip().lstrip("#").strip()
    if not cleaned:
        return None
    if cleaned.casefold() in _NOT_A_MOLD:
        return None
    return cleaned.upper()


def normalize_status(raw: object) -> str | None:
    """MES 상태 원문 → MoldStatus. 인식 못 하면 None(추측하지 않는다)."""
    text = cell_to_text(raw)
    if text is None:
        return None
    key = re.sub(r"\s+", "", text)  # "사용 중" → "사용중"
    return STATUS_MAP.get(key)


def to_float(v: object) -> float | None:
    """숫자로 읽는다. 못 읽으면 None — 0 으로 대체하지 않는다."""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if not s:
        return None
    percent = s.endswith("%")
    if percent:
        s = s[:-1].strip()
    try:
        n = float(s)
    except ValueError:
        return None
    # '0.8%' 는 비율 0.008 이다. 대시보드는 defect_rate 를 비율로 다루고
    # fmtPercent 가 ×100 을 하므로, 여기서 %를 그대로 두면 100배가 된다.
    return n / 100 if percent else n


def to_int(v: object) -> int | None:
    n = to_float(v)
    if n is None:
        return None
    return int(n)


# 실물 이벤트시간에서 본 표기들. cell_to_text 가 datetime 을 isoformat 으로
# 바꾸므로 대개 첫 번째로 맞지만, 사람이 손으로 적은 칸은 형식이 흔들린다.
_DATETIME_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y.%m.%d %H:%M:%S",
    "%Y.%m.%d %H:%M",
    "%Y.%m.%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d",
)


def to_datetime(v: object) -> datetime | None:
    """이벤트 시각을 datetime 으로. 못 읽으면 None — 추측하지 않는다.

    사용구간의 시작과 끝이 곧 MES 조회 날짜이므로, 여기서 조용히 틀리면
    엉뚱한 날의 불량율이 금형에 붙는다. 형식을 못 알아보면 그 행을 버리고
    호출자가 건수를 드러내는 편이 낫다.
    """
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    text = cell_to_text(v)
    if text is None:
        return None
    text = text.strip()
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None
