"""금형 데이터 수집 파이프라인의 데이터 계약.

두 갈래가 있다.
1. 에이전트가 제출하는 구조 서술(SheetLayout 계열) — submit_layout 도구의
   인자 스키마가 되므로, 필수/선택 구분이 곧 에이전트에 대한 요구사항이다.
2. 파이프라인 내부를 흐르는 자료(Row, MoldRecord, RunSummary).

주의: 여기의 SourceKind 는 대시보드의 StageKey 와 다르다. 대시보드는 화면 탭
5종(design/iqc/pqc/install/ai_recheck)이고, 여기는 업로드 폴더의 종류라 mes 가
추가된다. 이름을 달리한 이유는 둘을 서로 대입하는 실수를 타입 단계에서 막기
위해서다.
"""
from typing import Literal

from pydantic import BaseModel, Field

SourceKind = Literal[
    "ees", "jig_master", "mes", "iqc", "pqc", "design", "install", "ai_recheck"
]

# 단계별 고정 필드 어휘. discover 의 프롬프트가 이 이름을 요구하고, assemble 이
# 같은 이름으로 읽는다. 양쪽이 공유하므로 스키마 모듈에 둔다 — assemble 에 두면
# discover 가 조립 로직을 import 하게 되어 의존 방향이 뒤집힌다.

# JIG 관리대장: **시트 하나가 금형 하나**이고 시트 이름이 그 금형의 JIG ID 다.
# 시트 안에 JIG ID 열은 없다 — 금형 귀속은 join.py 가 시트 이름으로 한다.
# 한 행은 이벤트 하나(그 금형이 어디로 옮겨졌는지)다.
EES_FIELDS = [
    "event_at",    # 이벤트시간
    "location",    # 위치. 이 값이 "설비" 인 구간이 곧 사용구간이다
    "equipment",   # 설비명 — 식별용이 아니라 **그 구간의 MES 조회 키**를 고른다
    "line",        # 관리대장의 라인(공장 접두사가 없어 MES 와 직접 비교 불가)
    "process",
]

# JIG 기준정보: 금형번호 ↔ 설비명 ↔ 설비코드 ↔ 라인 매핑표.
# 이것이 빠지면 MES 조회 키를 얻을 수 없어 불량율이 통째로 끊긴다.
JIG_MASTER_FIELDS = [
    "mold_no",          # JIG ID (#RX39513 → RX39513)
    "jig_name",         # JIG명
    "equipment",        # 설비명
    "equipment_code",   # 설비코드 — MES 조회 키
    "line",             # Line명 — MES 조회 키
]

# MES: 하루 한 파일, 한 행이 (라인, 설비코드) 하나의 그날 실적이다.
# 여기에도 금형번호가 없다.
MES_FIELDS = [
    "run_date",         # 상단 라벨의 날짜(표가 아니라 key_values 로 온다)
    "line",
    "equipment_code",
    "produced",         # 투입수량
    "good",             # 양품수량
    "defects",          # 불량수량
    "defect_ppm",       # 종합/불량율(PPM)
]

# IQC 에서 수치로 뽑을 고정 필드. 나머지 컬럼은 헤더 텍스트 그대로 항목이 된다.
IQC_VALUE_FIELDS = ["punch", "die", "diff", "gap"]


class ColumnMap(BaseModel):
    """표의 한 열이 어느 필드에 해당하는지.

    description 이 붙어 있는 이유: 이 계열 모델은 submit_layout 도구의 인자
    스키마가 되고, 에이전트는 Python 주석이 아니라 JSON 스키마만 본다.
    설명이 없으면 필드명과 타입만 보고 관례를 추측한다.
    """

    field: str = Field(
        description="고정 어휘(mold_no 등) 또는 헤더 텍스트 그대로"
    )
    column: str = Field(description='열 문자. 예: "C"')


class KeyValueItem(BaseModel):
    """표가 아닌 블록. 라벨 옆/아래에 값이 있는 형태(상단 기본정보 등)."""

    field: str = Field(
        description="고정 어휘(mold_no 등) 또는 라벨 텍스트 그대로"
    )
    value_cell: str = Field(description='값이 있는 셀 주소. 예: "D5"')
    label_cell: str | None = Field(
        default=None,
        description=(
            "어느 라벨을 보고 그렇게 판단했는지. 값이 이상할 때 근거를 "
            "되짚는 용도이므로 가능하면 채워라"
        ),
    )


class TableBlock(BaseModel):
    """헤더 행(들) + 그 아래 데이터 행들.

    role 이 필수인 이유: 실물 IQC 시트에는 summary 표(양극 성형/소계/총계)와
    detail 표가 함께 있다. 구분하지 않으면 '소계'를 금형번호로 읽는다.
    """

    name: str = Field(description="이 표를 사람이 알아볼 이름")
    role: Literal["detail", "summary"] = Field(
        description=(
            "detail=실제 데이터 행이 있는 표, summary=소계/합계 등 집계표. "
            "summary 는 파싱하지 않는다"
        )
    )
    category: str | None = Field(
        default=None,
        description='표가 속한 카테고리 제목(있으면). 예: "금형 측정 대장"',
    )
    header_rows: list[int] = Field(
        description=(
            "헤더가 있는 행 번호들(1-based). 2단 병합 헤더면 두 행을 모두 넣는다"
        )
    )
    data_start_row: int = Field(
        description="데이터가 시작하는 첫 행 번호(1-based, 헤더 다음)"
    )
    data_end_row: int | None = Field(
        default=None,
        description=(
            "데이터 마지막 행 번호. 표 아래에 다른 블록이 없으면 null 로 두면 "
            "빈 행에서 자동으로 멈춘다. 헤더 행 번호를 넣지 마라"
        ),
    )
    columns: list[ColumnMap] = Field(
        default=[],
        description="이 표의 컬럼 매핑. detail 표는 비어 있으면 안 된다",
    )


class AnchorCheck(BaseModel):
    """'여기에 이 텍스트가 있으면 같은 양식이다.'

    에이전트가 직접 지목한다 — 양식을 이해한 주체가 무엇이 같으면 같은
    양식인지 가장 잘 안다. 헤더 행을 해시하는 방식은 두 번 무너졌다:
    헤더 위치를 모르는 상태에서 해시할 수 없고, 표가 여러 개면 헤더도 여럿이다.
    """

    cell: str = Field(description='확인할 셀 주소. 예: "B4"')
    text: str = Field(
        description=(
            "그 셀에 있어야 하는 텍스트. 행이 추가돼도 안 바뀌는 "
            "**헤더 텍스트**를 골라라 — 데이터 값은 안 된다"
        )
    )


class SheetLayout(BaseModel):
    sheet_name: str = Field(description="시트 이름. 질문에 주어진 그대로 적는다")
    key_values: list[KeyValueItem] = Field(
        default=[],
        description="표가 아니라 라벨 옆/아래에 값이 있는 블록(상단 기본정보 등)",
    )
    tables: list[TableBlock] = Field(
        default=[], description="이 시트의 표들. 한 시트에 여러 개일 수 있다"
    )
    anchors: list[AnchorCheck] = Field(
        description=(
            "같은 양식인지 판정할 근거 셀 3개 이상. 비우면 캐시 판정이 "
            "불가능하므로 필수다"
        )
    )
    notes: str | None = Field(
        default=None, description="애매하다고 본 점을 적어라(로그로만 쓰인다)"
    )


class Row(BaseModel):
    """파서가 뽑아낸 한 행. 아직 어느 금형의 것인지 모른다.

    금형 귀속을 파싱에서 분리한 이유: IQC 는 금형번호 열로, PQC 는 (날짜·공정·
    호기·시간) 조인으로 귀속된다. 그 차이가 한 모듈에 섞이면 읽을 수 없다.
    """

    source_file: str
    sheet: str
    row_no: int  # 원본 행 번호 (추적용)
    values: dict[str, str | None]  # field → 셀 텍스트(정규화 전)


class FoundFile(BaseModel):
    path: str
    kind: SourceKind
    content_hash: str


class ScanResult(BaseModel):
    """스캔 결과. 찾은 파일과, 디스크에 있지만 읽지 못한 파일을 함께 돌려준다.

    파이프라인이 "삭제된 파일"과 "이번에 못 읽은 파일"을 구분해야 하는데,
    경로 존재 여부로 추론하면 처음 등장하는 파일(이력에 없는)을 놓치고,
    설정에서 폴더 매핑을 뺀 경우를 잠금으로 오인한다.
    """

    files: list[FoundFile] = []
    unreadable: list[str] = []


class JigInfo(BaseModel):
    """기준정보 한 행 — 금형/설비에서 MES 조회 키(설비코드·라인)를 얻는 다리.

    MES 는 금형번호를 모른다. 이 표가 없으면 관리대장의 사용구간에 붙일
    실적을 찾을 수 없어 불량율이 통째로 비어 버린다.

    설비명은 비어 있을 수 있다 — JIG ID 로 조회하는 경로에는 필요 없기
    때문이다. 필수 키는 mold_no 하나다.
    """

    mold_no: str
    equipment: str | None = None
    equipment_code: str | None = None
    line: str | None = None
    jig_name: str | None = None


class DailyDefect(BaseModel):
    """MES 하루치 실적. 근거를 되짚을 수 있게 원값 그대로 남긴다."""

    date: str          # YYYY-MM-DD
    produced: int
    defects: int


class UsageRun(BaseModel):
    """금형이 설비에 들어가 있던 구간 하나 = production_run 한 행.

    ended_at 이 None 이면 아직 설비에 있다는 뜻이다. 불량율이 비어 있는 것이
    '가동 중이라 아직 없음'인지 '조인이 깨짐'인지 구분되어야 하므로, 이 둘을
    같은 None 으로 뭉개지 않는다(호출자가 ended_at 으로 판단한다).
    """

    mold_no: str
    # 그 구간에 금형이 들어가 있던 설비명. 관리대장 이벤트의 원문 그대로다
    # (기준정보에 없는 이름일 수도 있다 — 그때는 조회만 JIG ID 행으로 폴백하고
    # 표시는 원문을 남긴다). 비어 있는 이벤트가 있을 수 있어 None 을 허용한다.
    equipment: str | None = None
    equipment_code: str | None = None
    line: str | None = None
    started_at: str                    # isoformat
    ended_at: str | None = None
    source_file: str
    source_sheet: str
    # ── MES 조인 결과 ──
    produced: int | None = None
    defects: int | None = None
    defect_rate: float | None = None   # 비율(0.0154 = 1.54%)
    daily: list[DailyDefect] = []


class StageItemRecord(BaseModel):
    """대시보드 StageItem 이 될 항목 + 출처."""

    label: str
    value: str
    source_file: str
    source_sheet: str | None = None
    source_cell: str | None = None


class MoldRecord(BaseModel):
    """DB 에 저장될 금형 하나. 대시보드 MoldDetail 의 재료."""

    mold_no: str
    status: str  # 이미 정규화된 MoldStatus 값("in_use" 등)
    line: str | None = None
    machine: str | None = None
    # 수량은 전부 미상이 기본이다. 0 을 기본값으로 두면 '신품'이라는 거짓말이 된다.
    shot_count: int | None = None
    total_installs: int | None = None
    total_production: int | None = None
    latest_defect_rate: float | None = None
    source_file: str
    iqc_items: list[StageItemRecord] = []
    iqc_source_file: str | None = None
    # 설비 사용구간(최근순). production_run 테이블이 되고, 가장 최근 구간의
    # 불량율이 latest_defect_rate 다.
    runs: list[UsageRun] = []


class RunSummary(BaseModel):
    """배치 한 번의 결과. 조용히 빠진 데이터가 없음을 증명하는 자리다.

    skipped/orphan/unknown 계열이 0 이 아니면 사람이 원인을 찾아야 한다 —
    화면만 봐서는 절대 알 수 없는 손실이기 때문이다.
    """

    status: Literal["ok", "error", "skipped"]
    started_at: str
    finished_at: str | None = None
    mold_count: int = 0
    iqc_matched: int = 0  # IQC 항목이 하나라도 붙은 금형 수
    orphan_mold_nos: list[str] = []  # IQC 에 있는데 MES 에 없는 금형
    unknown_statuses: list[str] = []  # 인식하지 못한 MES 상태 원문
    # 그 상태 때문에 제외된 MES 행 수. 원문 목록만으로는 어휘 하나가 몇 건을
    # 삼켰는지 알 수 없는데, 실물 어휘가 STATUS_MAP 밖이면 화면이 통째로
    # 빈다 — 손실 규모가 보여야 사람이 STATUS_MAP 을 고칠 판단을 한다.
    unknown_status_rows: int = 0
    skipped_rows: int = 0  # 금형번호가 없어 버린 행(unknown_status_rows 포함)
    files: list[str] = []  # 이번 배치에서 읽은 파일
    error: str | None = None
    # 디스크에는 있는데 이번 회차에 열지도 못한 파일(스캐너가 해시조차 못 뜬
    # 경우). 대개 사람이 엑셀을 열어둔 경우다. 이 목록이 비어 있지 않으면
    # 배치를 건너뛴다 — 배치는 DB 를 전체 교체하므로, 못 읽은 파일의 데이터가
    # 화면에서 조용히 사라지기 때문이다.
    unreadable_files: list[str] = []
    # 열리긴 했으나 파싱에 실패한 IQC 파일과 그 사유("경로: 예외: 메시지").
    # 이 파일들만 건너뛰고 배치는 계속한다 — 부속 정보 하나가 없다고 금형
    # 목록을 버릴 이유가 없다. 다만 사유 없이 넘기면 그 파일의 IQC 항목이
    # 화면에서 사라지는데 아무도 원인을 못 찾는다.
    failed_files: list[str] = []

    # ── EES/MES 조인에서 생기는 손실 ────────────────────────────────
    # 관리대장에 있는데 기준정보에 없는 JIG ID. MES 조회 키를 얻지 못해 그
    # 금형이 통째로 안 나온다 — 기준정보가 낡았다는 가장 흔한 신호다.
    unknown_jig_id: list[str] = []
    # 관리대장에 있는데 기준정보에 없는 설비명. **금형은 나온다** — JIG ID 행의
    # 설비코드로 대신 조회한다. 다만 그 구간의 실적이 '그때 그 설비'가 아니라
    # '현재 등록된 설비' 기준이라는 뜻이므로 값을 그대로 믿으면 안 된다.
    unknown_equipment: list[str] = []
    # 사용구간이 덮는 날인데 MES 파일이 없는 날짜. 불량율이 일부 날만
    # 반영되므로 값이 있어도 믿을 수 없다.
    missing_mes_days: list[str] = []
    # MES 파일은 있는데 그 (라인, 설비코드) 행이 없는 경우. 라인 표기가
    # 어긋났거나 그날 그 설비가 안 돌았다.
    unmatched_runs: int = 0
    # 아직 설비에 있어 종료가 없는 구간 수. **손실이 아니다** — 불량율이
    # 비어 있는 이유가 '가동 중'인지 '조인 실패'인지 구분하려고 따로 센다.
    open_runs: int = 0
