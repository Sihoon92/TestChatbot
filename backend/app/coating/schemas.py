"""컬럼 계약과 항목 ID 매핑. 문자열 리터럴은 이 파일에만 존재한다.

다른 모듈이 "lot_id" 를 직접 적기 시작하면 오타가 조용한 조인 실패로 나타난다
(빈 결과가 나오는데 예외는 안 난다). 그래서 상수로 고정한다.
"""

# 원본 readings
LOT = "lot_id"
AT = "worked_at"
PRODUCT = "product"
ITEM = "item_id"
VALUE = "value"
ROW_NO = "row_no"

# 항목 사전
ITEM_NAME = "item_name"
IO = "io"
ROLE = "role"
ZONE = "zone"

IO_INPUT = "input"
IO_OUTPUT = "output"

# run-length 압축 결과
PREV_VALUE = "prev_value"

# 이벤트
EVENT = "event_id"
DELTA = "delta"
SETTLED_AT = "settled_at"
CONTAMINATED = "contaminated"
DROP_REASON = "drop_reason"

# 파생
WET_MEAN = "wet_mean"
SEGMENT = "segment_id"

N_ZONES = 25

# T_Block UNIT Gap Offset 1~25Zone
GAP_ITEM_IDS = [str(30030837 + z) for z in range(1, N_ZONES + 1)]
# (A side)GV UNIT Wet 1~25Zone
WET_ITEM_IDS = [str(90030610 + z) for z in range(1, N_ZONES + 1)]

# zone 이 없는 스칼라 제어값. 레벨 모델의 피처가 된다.
CONTROL_SCALARS = {
    "10030009": "bp_open_rate",
    "50030111": "pump_rpm",
    "10030271": "os_gap",
    "10030272": "ds_gap",
}

# 제어 항목 전체 = 스칼라 4 + zone 25
CONTROL_ITEM_IDS = list(CONTROL_SCALARS) + GAP_ITEM_IDS


def zone_col(z: int) -> str:
    """wide 테이블의 zone 컬럼명. 1-based."""
    return f"z{z}"


ZONE_COLS = [zone_col(z) for z in range(1, N_ZONES + 1)]
