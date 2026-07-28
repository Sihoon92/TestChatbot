"""셀 값 정규화 — 이 모듈이 틀리면 같은 금형이 둘로 갈라진다."""
from datetime import date, datetime

from app.ingest.normalize import (
    cell_to_text,
    normalize_mold_no,
    normalize_status,
    normalize_text,
    to_float,
    to_int,
)


def test_cell_to_text_integer_float_has_no_decimal_point():
    """xlwings 는 정수 셀도 float 로 준다. str(1.0) == '1.0' 이면 금형번호나
    행번호가 '1.0' 이 되어 매칭이 전부 깨진다."""
    assert cell_to_text(1.0) == "1"
    assert cell_to_text(28312.0) == "28312"
    assert cell_to_text(1.5) == "1.5"


def test_cell_to_text_blank_is_none():
    """빈 문자열과 공백만 있는 셀은 '값 없음'이다. 빈 문자열로 두면
    '값이 없음'과 '빈 문자열'이 섞인다."""
    assert cell_to_text(None) is None
    assert cell_to_text("") is None
    assert cell_to_text("   ") is None


def test_cell_to_text_datetime_is_iso():
    assert cell_to_text(datetime(2026, 7, 1, 14, 30)) == "2026-07-01T14:30:00"
    assert cell_to_text(date(2026, 7, 1)) == "2026-07-01"


def test_cell_to_text_strips():
    assert cell_to_text("  RX28312 ") == "RX28312"


def test_normalize_text_collapses_whitespace_and_case():
    assert normalize_text("  금형  번호 ") == "금형 번호"
    assert normalize_text("Punch") == normalize_text("PUNCH")
    assert normalize_text(None) == ""


def test_normalize_mold_no_strips_hash_and_uppercases():
    """이력표는 '#RX41194', 대장은 'RX28312' 로 쓴다. # 만 떼면 같은 식별자다."""
    assert normalize_mold_no("#RX41194") == "RX41194"
    assert normalize_mold_no(" rx28312 ") == "RX28312"
    assert normalize_mold_no("RX28312") == "RX28312"


def test_normalize_mold_no_rejects_blank_and_aggregate_rows():
    """소계/합계 행은 금형이 아니다. 숫자만 있는 칸도 금형번호가 아니다."""
    assert normalize_mold_no(None) is None
    assert normalize_mold_no("") is None
    assert normalize_mold_no("  ") is None
    assert normalize_mold_no("소계") is None
    assert normalize_mold_no("합계") is None
    assert normalize_mold_no("총계") is None


def test_normalize_status_maps_korean_vocabulary():
    assert normalize_status("사용중") == "in_use"
    assert normalize_status("사용 중") == "in_use"
    assert normalize_status("대기중") == "standby"
    assert normalize_status("수리중") == "repair"
    assert normalize_status("폐기") == "retired"


def test_normalize_status_returns_none_for_unknown():
    """인식 못 한 어휘는 추측하지 않는다. 호출자가 그 원문을 수집해
    RunSummary.unknown_statuses 로 드러내고, 사람이 STATUS_MAP 을 고친다."""
    assert normalize_status("가동") is None
    assert normalize_status(None) is None
    assert normalize_status("") is None


def test_to_float_and_to_int():
    assert to_float("0.8") == 0.8
    assert to_float(0.8) == 0.8
    assert to_float("8,412") == 8412.0  # 천단위 쉼표
    assert to_float("0.8%") == 0.008    # 퍼센트 표기는 비율로
    assert to_float("측정불가") is None
    assert to_float(None) is None

    assert to_int("8,412") == 8412
    assert to_int(8412.0) == 8412
    assert to_int("체크") is None
    assert to_int(None) is None
