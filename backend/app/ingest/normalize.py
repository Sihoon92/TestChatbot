"""셀 값 정규화 — 파이프라인 전체에서 이 모듈만 사용한다.

정규화가 여러 곳에 흩어지면 같은 금형이 둘로 갈라지고(대장의 'RX28312' 와
이력표의 '#RX41194'), 조인 키가 영영 맞지 않는다. 그래서 한 곳에만 둔다.

xlwings 가 돌려주는 값의 타입은 셀 서식에 따라 달라진다: 숫자는 float,
날짜는 datetime, 빈 칸은 None. 정수 셀도 float(1.0) 로 오기 때문에 그대로
str() 하면 '1.0' 이 되어 매칭이 전부 깨진다 — cell_to_text 가 그것을 막는다.
"""
import re
from datetime import date, datetime

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


def cell_to_text(v: object) -> str | None:
    """셀 값을 표시·비교용 문자열로. 빈 값은 None."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, bool):
        # bool 은 int 의 하위 타입이라 아래 숫자 분기보다 먼저 처리해야 한다.
        return "TRUE" if v else "FALSE"
    if isinstance(v, float):
        # 정수값 float 는 소수점을 떼야 한다 — str(28312.0) == '28312.0'.
        if v.is_integer():
            return str(int(v))
        return str(v)
    if isinstance(v, int):
        return str(v)
    s = str(v).strip()
    return s or None


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
