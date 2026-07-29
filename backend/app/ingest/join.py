"""EES 사용구간 추출과 MES 불량율 조인 — 순수 계산만 한다.

Excel 도 LLM 도 DB 도 모른다. Row 리터럴만 있으면 결과가 재현되므로
"왜 이 금형에 이 불량율이 붙었지" 를 나중에 반드시 되짚을 수 있다.
normalize.py·layout.py 와 같은 성격의 모듈이다.

## 왜 세 소스가 필요한가

관리대장은 **JIG ID 열**로 어느 금형인지 알려주지만 그 금형이 얼마나 불량을
냈는지는 모른다. MES 는 실적을 알지만 **금형번호를 모른다**. 기준정보가 둘
사이에서 MES 조회 키(설비코드·Line명)를 내준다:

    관리대장.JIG ID ─────────────────→ 금형 확정
    관리대장.설비명 ─→ 기준정보[설비명] ─┐
                        (없으면 폴백)     ├─→ 설비코드 + Line명 ─→ MES
    관리대장.JIG ID ─→ 기준정보[JIG ID] ─┘

## 식별과 조회를 왜 나누는가

금형을 확정하는 것은 **JIG ID 하나**다. 이미 알고 있는 값이므로 다른 문서를
거쳐 알아낼 필요가 없다. 설비명은 "이 구간에 어느 설비의 실적을 붙일까" 만
정한다. 둘을 나눠 두면:

  - 금형이 설비를 옮겨 다녀도 각 구간이 **그때 그 설비**의 실적에 붙는다
    (기준정보는 JIG ID 당 현재 설비 하나만 알아서 과거 구간을 잘못 귀속시킨다)
  - 설비명이 기준정보에 없어도 금형은 사라지지 않는다 — JIG ID 행으로 폴백한다

## 라인 문자열로 조인하지 않는 이유

관리대장의 라인은 "Pouch #10(S)", 기준정보의 Line명은 "톈진 Pouch #10(S)" 다.
공장 접두사가 붙고 안 붙고의 차이라 문자열 비교는 조용히 어긋난다. 라인은
기준정보 것을 신뢰하고, MES 조회는 설비코드로만 한다.
"""
import math
import re
from datetime import date, datetime, timedelta

from pydantic import BaseModel

from app.ingest.normalize import (
    cell_to_text,
    normalize_mold_no,
    to_datetime,
    to_int,
)
from app.ingest.schemas import DailyDefect, JigInfo, Row, UsageRun

# 이 위치에 있는 동안이 곧 가동 구간이다. 나머지 위치(보관함·Jig Room·
# 내부 수리 등)는 전부 '설비 아님' 하나로 묶인다 — 어휘가 늘어도 여기만 안다.
_IN_USE_LOCATION = "설비"

# 하루는 자정이 아니라 **투입 시각 기준 24시간**이다. 자정으로 끊으면
# 22:00 에 투입해 다음날 03:00 에 끝난 5시간짜리가 이틀로 세어진다.
_WINDOW = timedelta(hours=24)

# 기간 라벨('2026.07.08-2026.07.08')에서 첫 날짜를 집는다.
_DATE_IN_LABEL = re.compile(r"(\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2})")


class JoinLosses(BaseModel):
    """조인에서 빠진 것들. 비어 있지 않으면 사람이 원인을 찾아야 한다."""

    # 관리대장에 있는데 기준정보에 없는 JIG ID(중복 제거). 그 금형은 MES
    # 조회 키를 못 얻어 통째로 빠진다 — 치명적인 쪽이다.
    unknown_jig_id: list[str] = []
    # 관리대장에 있는데 기준정보에 없는 설비명(중복 제거). 금형은 나오되
    # 실적을 '현재 등록된 설비' 기준으로 읽었다는 경고다.
    unknown_equipment: list[str] = []
    # JIG ID 를 못 읽어 어느 금형에도 못 붙인 행 수
    rows_without_mold_no: int = 0
    # 이벤트 시각을 못 읽어 버린 행 수
    bad_event_times: int = 0
    # 사용구간이 덮는 날인데 MES 파일이 없는 날짜(중복 제거)
    missing_mes_days: list[str] = []
    # MES 에서 단 하루도 못 찾은 구간 수(가동 중인 구간은 제외)
    unmatched_runs: int = 0
    # 아직 설비에 있어 종료가 없는 구간 수. 손실이 아니라 상태다.
    open_runs: int = 0

    def merge(self, other: "JoinLosses") -> None:
        for jig_id in other.unknown_jig_id:
            if jig_id not in self.unknown_jig_id:
                self.unknown_jig_id.append(jig_id)
        for equip in other.unknown_equipment:
            if equip not in self.unknown_equipment:
                self.unknown_equipment.append(equip)
        for day in other.missing_mes_days:
            if day not in self.missing_mes_days:
                self.missing_mes_days.append(day)
        self.rows_without_mold_no += other.rows_without_mold_no
        self.bad_event_times += other.bad_event_times
        self.unmatched_runs += other.unmatched_runs
        self.open_runs += other.open_runs


def _jig_info(mold_no: str, row: Row) -> JigInfo:
    return JigInfo(
        mold_no=mold_no,
        equipment=cell_to_text(row.values.get("equipment")),
        equipment_code=cell_to_text(row.values.get("equipment_code")),
        line=cell_to_text(row.values.get("line")),
        jig_name=cell_to_text(row.values.get("jig_name")),
    )


def build_jig_index(rows: list[Row]) -> tuple[dict[str, JigInfo], list[str]]:
    """기준정보 행 → {JIG ID: JigInfo}. 두 번째 값은 버린 행의 사유다.

    관리대장이 아는 JIG ID 로 바로 조회하는 색인이다. 필수 키는 금형번호
    하나다 — 설비명이 비어 있어도 설비코드·라인은 얻을 수 있다. 금형번호가
    없는 행을 조용히 버리면 "왜 이 금형이 안 나오지"를 추적할 수 없어 사유를
    함께 돌려준다. 같은 JIG ID 가 여러 번 나오면 뒤에 나온 것이 최신이다.
    """
    index: dict[str, JigInfo] = {}
    dropped: list[str] = []

    for row in rows:
        mold_no = normalize_mold_no(row.values.get("mold_no"))
        if not mold_no:
            raw = cell_to_text(row.values.get("mold_no"))
            dropped.append(
                f"{row.source_file} {row.row_no}행: JIG ID={raw or '(빈 값)'}"
            )
            continue
        index[mold_no] = _jig_info(mold_no, row)
    return index, dropped


def build_equipment_index(rows: list[Row]) -> dict[str, JigInfo]:
    """기준정보 행 → {설비명: JigInfo}. MES 조회 키를 고르는 데만 쓴다.

    여기서 나오는 `JigInfo.mold_no` 는 **그 설비에 등록된 금형**이지 조회 중인
    금형이 아니다. 읽어야 할 것은 `equipment_code` 와 `line` 뿐이다 — mold_no
    까지 가져다 쓰면 금형이 설비를 옮긴 순간 남의 번호가 붙는다.

    설비명이 없는 행은 이 색인에 못 들어간다. 사유를 남기지 않는 이유는
    build_jig_index 가 같은 행을 이미 봤기 때문이다 — 거기서는 유효한 행이라
    여기서 또 세면 멀쩡한 행이 손실로 보고된다.
    """
    index: dict[str, JigInfo] = {}
    for row in rows:
        equipment = cell_to_text(row.values.get("equipment"))
        mold_no = normalize_mold_no(row.values.get("mold_no"))
        if not equipment or not mold_no:
            continue
        index[equipment] = _jig_info(mold_no, row)
    return index


def extract_runs(
    rows: list[Row],
    jig_index: dict[str, JigInfo],
    equip_index: dict[str, JigInfo],
) -> tuple[list[UsageRun], JoinLosses]:
    """관리대장 이벤트 → 설비 사용구간.

    위치가 "설비" 인 이벤트가 투입이고, **바로 다음 이벤트**의 시각이 종료다
    (그 이벤트의 위치가 무엇이든 설비를 떠났다는 사실만 중요하다). 마지막
    이벤트가 설비면 아직 가동 중이라 종료가 없다.

    **JIG ID 로 묶어서 짝짓는다.** 한 시트에 여러 금형의 이벤트가 시간순으로
    섞여 있으므로, 시트로 묶으면 A 금형의 설비 진입이 B 금형의 다음 이벤트로
    닫힌다. 파일 경계도 넘는다 — 금형 이력은 파일이 아니라 금형을 따라
    이어지기 때문이다. 대신 관리대장이 겹쳐 올라올 때를 대비해 완전히 같은
    이벤트는 하나로 접는다(안 접으면 길이 0 짜리 유령 구간이 생긴다).

    구간마다 MES 조회 키는 **그 이벤트의 설비명**으로 고른다. 기준정보에 없는
    설비명이면 JIG ID 행으로 폴백한다 — 금형을 잃는 것보다 낫지만, 그 구간의
    실적이 '현재 등록된 설비' 기준이라는 사실은 손실로 남긴다.
    """
    losses = JoinLosses()

    by_mold: dict[str, list[tuple[datetime, Row]]] = {}
    seen: set[tuple[str, datetime, str, str]] = set()
    for row in rows:
        mold_no = normalize_mold_no(row.values.get("mold_no"))
        if mold_no is None:
            losses.rows_without_mold_no += 1
            continue
        when = to_datetime(row.values.get("event_at"))
        if when is None:
            losses.bad_event_times += 1
            continue
        key = (
            mold_no,
            when,
            cell_to_text(row.values.get("location")) or "",
            cell_to_text(row.values.get("equipment")) or "",
        )
        if key in seen:
            continue
        seen.add(key)
        by_mold.setdefault(mold_no, []).append((when, row))

    runs: list[UsageRun] = []
    for mold_no, events in by_mold.items():
        jig_info = jig_index.get(mold_no)
        if jig_info is None:
            # 설비코드를 어디서도 얻을 수 없으므로 이 금형은 통째로 빠진다.
            if mold_no not in losses.unknown_jig_id:
                losses.unknown_jig_id.append(mold_no)
            continue

        # 엑셀 행 순서가 시간순이라는 보장이 없다. 정렬하지 않으면 '다음
        # 이벤트'가 엉뚱한 행이 되어 사용구간이 음수 길이가 된다.
        events.sort(key=lambda pair: pair[0])

        for i, (when, row) in enumerate(events):
            location = cell_to_text(row.values.get("location")) or ""
            if location.strip() != _IN_USE_LOCATION:
                continue

            equipment = cell_to_text(row.values.get("equipment"))
            info = equip_index.get(equipment) if equipment else None
            if info is None:
                if equipment and equipment not in losses.unknown_equipment:
                    losses.unknown_equipment.append(equipment)
                info = jig_info

            ended = events[i + 1][0] if i + 1 < len(events) else None
            if ended is None:
                losses.open_runs += 1

            runs.append(UsageRun(
                # 금형번호는 언제나 **행의 JIG ID** 다. info.mold_no 는 그
                # 설비에 등록된 금형이라, 금형이 설비를 옮긴 순간 남의 번호가
                # 붙는다.
                mold_no=mold_no,
                equipment=equipment,
                equipment_code=info.equipment_code,
                # 라인은 기준정보 것을 쓴다 — 관리대장 라인은 공장 접두사가
                # 없어 MES 와 직접 비교할 수 없다.
                line=info.line,
                started_at=when.isoformat(),
                ended_at=ended.isoformat() if ended else None,
                source_file=row.source_file,
                source_sheet=row.sheet,
            ))

    runs.sort(key=lambda r: (r.mold_no, r.started_at))
    return runs, losses


def latest_locations(rows: list[Row]) -> dict[str, str]:
    """JIG ID → 가장 마지막 이벤트의 위치.

    관리대장에 상태 열이 따로 없다. 금형이 **지금 어디에 있는가**가 곧 상태다.
    설비 사용구간(extract_runs)과 달리 여기서는 '설비' 가 아닌 위치도 필요하다 —
    수리실에 있는 금형도 화면에 나와야 하기 때문이다.

    설비명은 보지 않는다. 보관함에 있는 이벤트는 설비명이 비어 있을 수 있는데,
    그 행을 버리면 그 금형이 목록에서 통째로 사라진다.
    """
    latest: dict[str, tuple[datetime, str]] = {}
    for row in rows:
        mold_no = normalize_mold_no(row.values.get("mold_no"))
        when = to_datetime(row.values.get("event_at"))
        location = cell_to_text(row.values.get("location"))
        if mold_no is None or when is None or not location:
            continue
        prev = latest.get(mold_no)
        if prev is None or when >= prev[0]:
            latest[mold_no] = (when, location)
    return {mold: loc for mold, (_when, loc) in latest.items()}


def covered_dates(started_at: str, ended_at: str | None) -> list[date]:
    """사용구간이 덮는 날짜들. 투입 시각부터 24시간씩 끊는다.

    올림(사용시간 ÷ 24h) 일수만큼 투입일부터 센다. 96시간은 정확히 4일이고
    5일째는 들어가지 않는다. 종료가 없으면(가동 중) 구간이 확정되지 않았으므로
    빈 목록이다.
    """
    start = to_datetime(started_at)
    end = to_datetime(ended_at) if ended_at else None
    if start is None or end is None or end <= start:
        return []
    windows = max(1, math.ceil((end - start) / _WINDOW))
    return [(start + timedelta(days=k)).date() for k in range(windows)]


def index_mes(rows: list[Row]) -> tuple[dict[tuple[date, str], tuple[int, int]], list[str]]:
    """MES 행 → {(날짜, 설비코드): (투입수량, 불량수량)}.

    설비코드가 없는 행(TOTAL 등)은 라인이 아니므로 자연히 빠진다.
    두 번째 값은 읽지 못한 행의 사유다.
    """
    index: dict[tuple[date, str], tuple[int, int]] = {}
    dropped: list[str] = []

    for row in rows:
        code = cell_to_text(row.values.get("equipment_code"))
        day = _parse_run_date(row.values.get("run_date"))
        produced = to_int(row.values.get("produced"))
        defects = to_int(row.values.get("defects"))
        if not code or day is None or produced is None or defects is None:
            if code:  # TOTAL 처럼 설비코드가 없는 행은 사유로 남기지 않는다
                dropped.append(
                    f"{row.source_file} {row.row_no}행: "
                    f"날짜={day} 투입={produced} 불량={defects}"
                )
            continue
        index[(day, code)] = (produced, defects)
    return index, dropped


def _parse_run_date(raw: object) -> date | None:
    """'2026.07.08-2026.07.08' 같은 기간 라벨에서 시작 날짜를 뽑는다.

    실물 MES 는 하루 한 파일이라 시작과 끝이 같지만 라벨이 기간 형태다.
    하이픈으로 자르지 않는 이유: ISO 표기('2026-07-08-2026-07-08')면 날짜
    자체에 하이픈이 있어 조각이 난다. 첫 번째 날짜 꼴을 찾아 쓴다.
    """
    text = cell_to_text(raw)
    if text is None:
        return None
    match = _DATE_IN_LABEL.search(text)
    parsed = to_datetime(match.group(1) if match else text.strip())
    return parsed.date() if parsed else None


def attach_defect_rates(
    runs: list[UsageRun], mes_index: dict[tuple[date, str], tuple[int, int]]
) -> JoinLosses:
    """사용구간마다 MES 를 조회해 불량율을 채운다(runs 를 제자리에서 수정).

    **비율의 평균이 아니라 raw 수량을 합쳐 다시 계산한다.** 생산량이 다른 날을
    같은 무게로 세면 틀린 값이 나온다 — 1000개 만든 날의 3%와 10개 만든 날의
    1%는 같은 무게일 수 없다.
    """
    losses = JoinLosses()

    for run in runs:
        days = covered_dates(run.started_at, run.ended_at)
        if not days:
            # 가동 중인 구간이다. 조인 실패가 아니므로 세지 않는다.
            continue
        if not run.equipment_code:
            losses.unmatched_runs += 1
            continue

        produced = defects = 0
        daily: list[DailyDefect] = []
        for day in days:
            hit = mes_index.get((day, run.equipment_code))
            if hit is None:
                text = day.isoformat()
                if text not in losses.missing_mes_days:
                    losses.missing_mes_days.append(text)
                continue
            produced += hit[0]
            defects += hit[1]
            daily.append(DailyDefect(date=day.isoformat(), produced=hit[0],
                                     defects=hit[1]))

        if not daily:
            losses.unmatched_runs += 1
            continue

        run.produced = produced
        run.defects = defects
        run.daily = daily
        # 투입이 0 이면 나눌 수 없다. 0.0 으로 두면 '불량 없음'이라는 거짓말이 된다.
        run.defect_rate = (defects / produced) if produced else None

    losses.missing_mes_days.sort()
    return losses
