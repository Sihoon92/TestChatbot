"""MES/IQC 행을 금형 레코드로 조립한다.

MES 가 마스터다 — 금형의 존재를 선언하는 것은 MES 뿐이고, IQC 는 이미 있는
금형에 정보를 덧붙인다. 방향이 뒤집히면 "대시보드 목록에 무엇이 나오는가" 의
정의가 흔들린다.

손실은 전부 셈해서 돌려준다. 조용히 빠지는 데이터는 화면만 봐서 알 수 없다.
"""
from pydantic import BaseModel

from app.ingest.normalize import (
    normalize_mold_no,
    normalize_status,
    to_float,
    to_int,
)
from app.ingest.schemas import (
    IQC_VALUE_FIELDS,
    MoldRecord,
    Row,
    StageItemRecord,
)

# 금형 귀속에만 쓰이고 화면 항목으로는 보여줄 필요가 없는 키.
_IQC_SKIP_FIELDS = {"mold_no"}


class AssembleResult(BaseModel):
    records: list[MoldRecord] = []
    orphan_mold_nos: list[str] = []
    unknown_statuses: list[str] = []
    # 상태를 인식하지 못해 제외한 MES 행 수. unknown_statuses 는 원문만
    # 모으므로 어휘 하나가 몇 건을 삼켰는지는 알 수 없는데, 실물 어휘가
    # STATUS_MAP 밖에 있으면 화면이 통째로 빈다 — 손실 규모가 보여야
    # 사용자가 "아, STATUS_MAP 에 '가동'을 넣어야겠다"를 알 수 있다.
    # skipped_rows 에도 함께 집계된다(부분집합이다).
    unknown_status_rows: int = 0
    skipped_rows: int = 0
    iqc_matched: int = 0


def _mold_records_from_mes(
    mes_rows: list[Row],
) -> tuple[dict[str, MoldRecord], list[str], int, int]:
    """MES 행 → {금형번호: MoldRecord}.

    MES 한 행은 생산 이벤트 1건이라 같은 금형이 여러 번 나온다. 뒤에 나온
    행이 더 최신 상태라고 보고 덮어쓴다.
    """
    records: dict[str, MoldRecord] = {}
    unknown: list[str] = []
    unknown_rows = 0
    skipped = 0

    for row in mes_rows:
        mold_no = normalize_mold_no(row.values.get("mold_no"))
        if mold_no is None:
            skipped += 1
            continue

        raw_status = row.values.get("status")
        status = normalize_status(raw_status)
        if status is None:
            # 추측하지 않는다. 원문을 모아 드러내고 사람이 STATUS_MAP 을 고친다.
            text = str(raw_status).strip() if raw_status is not None else "(빈 값)"
            if text not in unknown:
                unknown.append(text)
            # 원문 목록은 중복을 제거하지만 건수는 행마다 센다 — 어휘 하나가
            # 몇 건을 삼켰는지가 곧 손실 규모다.
            unknown_rows += 1
            skipped += 1
            continue

        line = row.values.get("line")
        machine = row.values.get("machine")
        if status != "in_use":
            # 사용중이 아니면 호기가 없다. 남겨두면 화면이 "3-2에 걸려 있다"는
            # 거짓 정보를 보여준다(대시보드 필터도 같은 규칙을 쓴다).
            line = machine = None

        records[mold_no] = MoldRecord(
            mold_no=mold_no,
            status=status,
            line=line,
            machine=machine,
            shot_count=to_int(row.values.get("shot_count")),
            total_installs=to_int(row.values.get("total_installs")),
            total_production=to_int(row.values.get("total_production")),
            latest_defect_rate=to_float(row.values.get("defect_rate")),
            source_file=row.source_file,
        )
    return records, unknown, unknown_rows, skipped


def _iqc_items(row: Row) -> list[StageItemRecord]:
    """IQC 행 → 화면 항목. 고정 수치 필드를 앞에, 자유 컬럼을 뒤에 둔다.

    자유 컬럼(측정자·조립자·연마자 등)을 버리지 않는 이유는 미리 고정 필드로
    잡을 수 없는 항목이기 때문이다 — 유연 스키마를 둔 목적이 이것이다.
    """
    items: list[StageItemRecord] = []
    for field in IQC_VALUE_FIELDS:
        value = row.values.get(field)
        if value is not None:
            items.append(
                StageItemRecord(
                    label=field,
                    value=value,
                    source_file=row.source_file,
                    source_sheet=row.sheet,
                )
            )
    for field, value in row.values.items():
        if field in _IQC_SKIP_FIELDS or field in IQC_VALUE_FIELDS:
            continue
        if value is None:
            continue
        items.append(
            StageItemRecord(
                label=field,
                value=value,
                source_file=row.source_file,
                source_sheet=row.sheet,
            )
        )
    return items


def assemble(mes_rows: list[Row], iqc_rows: list[Row]) -> AssembleResult:
    records, unknown, unknown_rows, skipped = _mold_records_from_mes(mes_rows)
    orphans: list[str] = []
    matched: set[str] = set()

    for row in iqc_rows:
        mold_no = normalize_mold_no(row.values.get("mold_no"))
        if mold_no is None:
            skipped += 1
            continue
        record = records.get(mold_no)
        if record is None:
            # MES 가 마스터이므로 넣지 않는다. 다만 조용히 버리지 않는다 —
            # 진짜 MES 누락일 수도, 오타일 수도 있다.
            if mold_no not in orphans:
                orphans.append(mold_no)
            continue
        items = _iqc_items(row)
        if not items:
            continue
        record.iqc_items.extend(items)
        record.iqc_source_file = row.source_file
        matched.add(mold_no)

    return AssembleResult(
        records=list(records.values()),
        orphan_mold_nos=orphans,
        unknown_statuses=unknown,
        unknown_status_rows=unknown_rows,
        skipped_rows=skipped,
        iqc_matched=len(matched),
    )
