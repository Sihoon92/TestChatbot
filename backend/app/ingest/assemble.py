"""EES/MES/IQC 행을 금형 레코드로 조립한다.

## 무엇이 마스터인가

**JIG 관리대장이 마스터다.** 행의 JIG ID 가 금형을 확정하고, 그 금형에 이벤트가
있어야 실재한다고 본다. 기준정보는 매핑표일 뿐이라 마스터가 아니다 —
기준정보에만 있고 관리대장에 이력이 없는 JIG 는 아직 들어온 적 없는 금형이다.

방향이 뒤집히면 "대시보드 목록에 무엇이 나오는가"의 정의가 흔들린다.

## 상태·라인·수량이 어디서 오는가

관리대장에는 상태 열도, 사용타수도, 총생산수량도 없다. 그래서 이렇게 채운다.

  status            마지막 이벤트의 **위치**(설비=가동중, 수리=수리중, 나머지=대기)
  line              마지막 사용구간이 조회한 **기준정보**의 Line명. 관리대장의
                    라인은 공장 접두사가 없어 MES 와 대조할 수 없으므로 안 쓴다.
  machine           마지막 사용구간의 설비명(사람이 알아보는 설비 이름)
  total_installs    설비 사용구간의 개수 — 그것이 곧 설치 횟수다
  total_production  MES 투입수량의 합(그 금형이 돌던 날들)
  shot_count        **미상**. 어느 문서에도 없다. 0 으로 두면 '신품'이라는
                    거짓말이 되므로 None 을 유지한다.

손실은 전부 셈해서 돌려준다. 조용히 빠지는 데이터는 화면만 봐서 알 수 없다.
"""
from pydantic import BaseModel

from app.ingest.join import (
    JoinLosses,
    attach_defect_rates,
    build_equipment_index,
    build_jig_index,
    extract_runs,
    index_mes,
    latest_locations,
)
from app.ingest.normalize import normalize_mold_no, status_from_location
from app.ingest.schemas import (
    IQC_VALUE_FIELDS,
    MoldRecord,
    Row,
    StageItemRecord,
    UsageRun,
)

# 금형 귀속에만 쓰이고 화면 항목으로는 보여줄 필요가 없는 키.
_IQC_SKIP_FIELDS = {"mold_no"}


class AssembleResult(BaseModel):
    records: list[MoldRecord] = []
    orphan_mold_nos: list[str] = []
    skipped_rows: int = 0
    iqc_matched: int = 0
    # 기준정보에서 다리 역할을 못 해 버린 행들의 사유
    dropped_master_rows: list[str] = []
    losses: JoinLosses = JoinLosses()


def _mold_records_from_ees(
    master_rows: list[Row], ees_rows: list[Row], mes_rows: list[Row]
) -> tuple[dict[str, MoldRecord], list[str], JoinLosses]:
    """관리대장 + 기준정보 + MES → {금형번호: MoldRecord}."""
    # 기준정보를 두 벌로 색인한다. JIG ID 쪽은 금형을 확정하고, 설비명 쪽은
    # 구간마다 MES 조회 키를 고른다 — 역할이 다르므로 색인도 따로 둔다.
    jig_index, dropped = build_jig_index(master_rows)
    equip_index = build_equipment_index(master_rows)
    runs, losses = extract_runs(ees_rows, jig_index, equip_index)

    mes_index, _bad = index_mes(mes_rows)
    losses.merge(attach_defect_rates(runs, mes_index))

    runs_by_mold: dict[str, list[UsageRun]] = {}
    for run in runs:
        runs_by_mold.setdefault(run.mold_no, []).append(run)

    records: dict[str, MoldRecord] = {}
    for mold_no, location in latest_locations(ees_rows).items():
        if mold_no not in jig_index:
            # extract_runs 가 이미 unknown_jig_id 로 셌다. 여기서 또 세면
            # 같은 사건이 두 번 보고된다.
            continue
        status = status_from_location(location)
        if status is None:
            continue

        mold_runs = sorted(
            runs_by_mold.get(mold_no, []), key=lambda r: r.started_at
        )
        produced = [r.produced for r in mold_runs if r.produced is not None]
        # 가장 최근 구간의 불량율. 없으면 그 앞 구간으로 거슬러 올라간다 —
        # 마지막 구간이 아직 가동 중이면 불량율이 없기 때문이다.
        latest_rate = next(
            (r.defect_rate for r in reversed(mold_runs) if r.defect_rate is not None),
            None,
        )

        # 가동 중이면 마지막 이벤트가 '설비' 이므로 그에 대응하는 구간이 반드시
        # 있다. 기준정보의 현재 설비가 아니라 **그 구간이 실제로 조회한** 설비·
        # 라인을 쓴다 — 금형이 옮겨 다녔다면 둘이 다르다.
        current = mold_runs[-1] if status == "in_use" and mold_runs else None

        records[mold_no] = MoldRecord(
            mold_no=mold_no,
            status=status,
            # 가동 중이 아니면 라인·설비를 비운다. 남겨두면 화면이 "지금 저기
            # 걸려 있다"는 거짓 정보를 보여준다(대시보드 필터도 같은 규칙이다).
            line=current.line if current else None,
            machine=current.equipment if current else None,
            shot_count=None,
            total_installs=len(mold_runs) or None,
            total_production=sum(produced) if produced else None,
            latest_defect_rate=latest_rate,
            source_file=mold_runs[0].source_file if mold_runs else "",
            runs=mold_runs,
        )
    return records, dropped, losses


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


def assemble(
    master_rows: list[Row],
    ees_rows: list[Row],
    mes_rows: list[Row],
    iqc_rows: list[Row],
) -> AssembleResult:
    records, dropped, losses = _mold_records_from_ees(
        master_rows, ees_rows, mes_rows
    )
    orphans: list[str] = []
    matched: set[str] = set()
    skipped = 0

    for row in iqc_rows:
        mold_no = normalize_mold_no(row.values.get("mold_no"))
        if mold_no is None:
            skipped += 1
            continue
        record = records.get(mold_no)
        if record is None:
            # 관리대장이 마스터이므로 넣지 않는다. 다만 조용히 버리지 않는다 —
            # 진짜 누락일 수도, 오타일 수도 있다.
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
        # 관리대장에서 JIG ID 를 못 읽은 행도 같은 성격의 손실이다("금형번호가
        # 없어 버린 행"). 별도 필드를 새로 만들면 화면에 같은 뜻의 숫자가 둘
        # 생기므로 여기에 합친다.
        skipped_rows=skipped + losses.rows_without_mold_no,
        iqc_matched=len(matched),
        dropped_master_rows=dropped,
        losses=losses,
    )
