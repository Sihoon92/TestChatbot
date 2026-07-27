"""금형 대시보드 데이터 계약(서버 쪽 정의).

`frontend/src/types/mold.ts` 와 같은 모양을 유지해야 한다 — 한쪽만 고치면
화면과 API 가 조용히 어긋난다. 필드를 바꿀 때는 반드시 양쪽을 함께 고친다.

고정 스키마(설계·생산결과)와 유연 스키마(IQC/PQC/AI복검)를 섞는다. 실제 엑셀
파일을 확인할 수 없는 상태이므로, 필드가 명시된 영역만 고정하고 나머지는
label-value 리스트로 받는다 — 어떤 항목이 오든 화면이 그릴 수 있게 하기 위함이다.
"""
from typing import Literal

from pydantic import BaseModel

MoldStatus = Literal["in_use", "standby", "repair", "retired"]
StageKey = Literal["design", "iqc", "pqc", "install", "ai_recheck"]
StageStatus = Literal["ok", "missing", "error"]

# 도메인 어휘이지 데이터의 산물이 아니다. 현재 '폐기' 금형이 없다고 해서
# '폐기'로 조회할 수단이 사라지면 안 된다(0건과 조회 불가는 다르다).
ALL_STATUSES: tuple[MoldStatus, ...] = ("in_use", "standby", "repair", "retired")
ALL_STAGES: tuple[StageKey, ...] = ("design", "iqc", "pqc", "install", "ai_recheck")


class SourceRef(BaseModel):
    """추출된 값의 출처. 값이 이상할 때 원본을 추적하기 위한 최소 정보.

    `app/excel/agent.py` 의 시스템 프롬프트가 요구하는 "근거가 된 셀 주소를
    제시하라" 와 같은 원칙이다. 추적 경로가 없으면 값 하나가 틀린 순간
    대시보드 전체를 신뢰할 수 없게 된다.
    """

    file: str
    sheet: str | None = None
    cell: str | None = None


class StageItem(BaseModel):
    label: str
    value: str  # 표시 전용이므로 문자열로 통일한다(단위·판정이 섞여도 그대로 보여준다)
    judgment: Literal["ok", "ng"] | None = None
    source: SourceRef | None = None


class StagePanel(BaseModel):
    stage: StageKey
    status: StageStatus
    updated_at: str | None = None
    error: str | None = None  # status == "error" 일 때 실패 사유
    items: list[StageItem] = []


class DesignSpec(BaseModel):
    # 전부 nullable — 추출 실패를 표현할 수 있어야 한다.
    angle_deg: float | None = None
    height_mm: float | None = None
    step_mm: float | None = None  # 단차
    overall_mm: float | None = None  # 전체
    plate_height_mm: float | None = None
    plate_width_mm: float | None = None


class CumulativeHistory(BaseModel):
    total_installs: int
    total_production: int
    first_installed_at: str | None = None


class CurrentState(BaseModel):
    status: MoldStatus
    line: str | None = None
    machine: str | None = None
    shot_count: int
    installed_at: str | None = None


class DefectRate(BaseModel):
    label: str
    rate: float


class ProductionRun(BaseModel):
    install_seq: int
    line: str
    machine: str
    started_at: str
    ended_at: str | None = None  # None = 진행 중
    grind_result: str | None = None  # 어휘가 미확정이라 문자열로 둔다
    defect_rate: float | None = None
    # 불량 항목은 제품·시기마다 달라질 수 있어 고정 컬럼으로 잡지 않는다.
    defects: list[DefectRate] = []


class MoldSummary(BaseModel):
    mold_no: str
    status: MoldStatus
    line: str | None = None  # status != "in_use" 면 None
    machine: str | None = None
    shot_count: int
    latest_defect_rate: float | None = None  # 가장 큰 install_seq 의 defect_rate
    total_production: int
    stage_status: dict[StageKey, StageStatus]  # 5단계 전부(탭 배지용)


class MoldDetail(BaseModel):
    summary: MoldSummary
    design: DesignSpec
    history: CumulativeHistory
    current: CurrentState
    productions: list[ProductionRun] = []
    # 유연 단계 3개(iqc/pqc/ai_recheck)만 담는다. design/install 은 전용 필드가
    # 있으므로 여기 중복으로 넣지 않는다 — 같은 값이 두 곳에 있으면 어느 쪽이
    # 진실인지 모호해진다.
    stages: list[StagePanel] = []


class Installation(BaseModel):
    line: str
    machine: str


class FilterOptions(BaseModel):
    statuses: list[MoldStatus]
    # 라인·호기를 독립 리스트로 주면 UI 가 실제로 없는 조합을 만들어낼 수 있다.
    # 화면의 드롭다운은 "3-2" 하나이므로 존재하는 쌍만 돌려준다.
    installations: list[Installation]
