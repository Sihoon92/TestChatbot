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

from pydantic import BaseModel

SourceKind = Literal["mes", "iqc", "pqc", "design", "install", "ai_recheck"]

# 단계별 고정 필드 어휘. discover 의 프롬프트가 이 이름을 요구하고, assemble 이
# 같은 이름으로 읽는다. 양쪽이 공유하므로 스키마 모듈에 둔다 — assemble 에 두면
# discover 가 조립 로직을 import 하게 되어 의존 방향이 뒤집힌다.
MES_FIELDS = [
    "mold_no",
    "status",
    "line",
    "machine",
    "date",
    "process",
    "time",
    "shot_count",
    "total_installs",
    "total_production",
    "defect_rate",
]

# IQC 에서 수치로 뽑을 고정 필드. 나머지 컬럼은 헤더 텍스트 그대로 항목이 된다.
IQC_VALUE_FIELDS = ["punch", "die", "diff", "gap"]


class ColumnMap(BaseModel):
    """표의 한 열이 어느 필드에 해당하는지."""

    field: str  # 고정 어휘(mold_no 등) 또는 헤더 텍스트 그대로
    column: str  # "C"


class KeyValueItem(BaseModel):
    """표가 아닌 블록. 라벨 옆/아래에 값이 있는 형태(상단 기본정보 등)."""

    field: str
    value_cell: str  # "D5"
    # 어느 라벨을 보고 그렇게 판단했는지. 값이 이상할 때 근거를 되짚는 용도.
    label_cell: str | None = None


class TableBlock(BaseModel):
    """헤더 행(들) + 그 아래 데이터 행들.

    role 이 필수인 이유: 실물 IQC 시트에는 summary 표(양극 성형/소계/총계)와
    detail 표가 함께 있다. 구분하지 않으면 '소계'를 금형번호로 읽는다.
    """

    name: str
    role: Literal["detail", "summary"]
    category: str | None = None  # "측정 이력" / "금형 측정 대장"
    # 멀티헤더(병합 셀 2단)를 표현하려면 여러 행이 필요하다.
    header_rows: list[int]
    data_start_row: int
    data_end_row: int | None = None  # None = 빈 행 만날 때까지
    columns: list[ColumnMap] = []


class AnchorCheck(BaseModel):
    """'여기에 이 텍스트가 있으면 같은 양식이다.'

    에이전트가 직접 지목한다 — 양식을 이해한 주체가 무엇이 같으면 같은
    양식인지 가장 잘 안다. 헤더 행을 해시하는 방식은 두 번 무너졌다:
    헤더 위치를 모르는 상태에서 해시할 수 없고, 표가 여러 개면 헤더도 여럿이다.
    """

    cell: str  # "B4"
    text: str  # "금형번호"


class SheetLayout(BaseModel):
    sheet_name: str
    key_values: list[KeyValueItem] = []
    tables: list[TableBlock] = []
    # 비우면 캐시 판정이 불가능하므로 필수다.
    anchors: list[AnchorCheck]
    notes: str | None = None  # 에이전트가 애매하다고 본 점 (로그용)


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
    skipped_rows: int = 0  # 금형번호가 없어 버린 행
    files: list[str] = []  # 이번 배치에서 읽은 파일
    error: str | None = None
    # 디스크에는 있는데 이번 회차에 읽지 못한 파일. 대개 사람이 엑셀을 열어둔
    # 경우다. 이 목록이 비어 있지 않으면 배치를 건너뛴다 — 배치는 DB 를 전체
    # 교체하므로, 못 읽은 파일의 데이터가 화면에서 조용히 사라지기 때문이다.
    unreadable_files: list[str] = []
