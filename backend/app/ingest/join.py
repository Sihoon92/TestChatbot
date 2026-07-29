"""EES 사용구간 추출과 MES 불량율 조인 — 순수 계산만 한다.

Excel 도 LLM 도 DB 도 모른다. Row 리터럴만 있으면 결과가 재현되므로
"왜 이 금형에 이 불량율이 붙었지" 를 나중에 반드시 되짚을 수 있다.
normalize.py·layout.py 와 같은 성격의 모듈이다.

## 왜 세 소스가 필요한가

관리대장은 **금형번호를 모르고**, MES 도 **금형번호를 모른다**. 기준정보가
설비명으로 둘을 이어 준다:

    관리대장.설비명 ─→ 기준정보 ─→ 금형번호 + 설비코드 + Line명 ─→ MES

그래서 기준정보가 낡으면 부분 실패가 아니라 전면 실패다. 그 사실이 화면에
드러나야 사람이 표를 고친다.

## 라인 문자열로 조인하지 않는 이유

관리대장의 라인은 "Pouch #10(S)", 기준정보의 Line명은 "톈진 Pouch #10(S)" 다.
공장 접두사가 붙고 안 붙고의 차이라 문자열 비교는 조용히 어긋난다.
조인은 **설비명으로만** 하고, 라인은 기준정보 것을 신뢰한다.
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

    # 관리대장에 있는데 기준정보에 없는 설비명(중복 제거)
    unknown_equipment: list[str] = []
    # 이벤트 시각을 못 읽어 버린 행 수
    bad_event_times: int = 0
    # 사용구간이 덮는 날인데 MES 파일이 없는 날짜(중복 제거)
    missing_mes_days: list[str] = []
    # MES 에서 단 하루도 못 찾은 구간 수(가동 중인 구간은 제외)
    unmatched_runs: int = 0
    # 아직 설비에 있어 종료가 없는 구간 수. 손실이 아니라 상태다.
    open_runs: int = 0

    def merge(self, other: "JoinLosses") -> None:
        for equip in other.unknown_equipment:
            if equip not in self.unknown_equipment:
                self.unknown_equipment.append(equip)
        for day in other.missing_mes_days:
            if day not in self.missing_mes_days:
                self.missing_mes_days.append(day)
        self.bad_event_times += other.bad_event_times
        self.unmatched_runs += other.unmatched_runs
        self.open_runs += other.open_runs


def build_jig_index(rows: list[Row]) -> tuple[dict[str, JigInfo], list[str]]:
    """기준정보 행 → {설비명: JigInfo}. 두 번째 값은 버린 행의 사유다.

    설비명이나 금형번호가 없는 행은 다리 역할을 못 하므로 버리되, 조용히
    버리면 "왜 이 금형이 안 나오지"를 추적할 수 없어 사유를 함께 돌려준다.
    같은 설비명이 여러 번 나오면 뒤에 나온 것이 최신이다.
    """
    index: dict[str, JigInfo] = {}
    dropped: list[str] = []

    for row in rows:
        equipment = cell_to_text(row.values.get("equipment"))
        mold_no = normalize_mold_no(row.values.get("mold_no"))
        if not equipment or not mold_no:
            dropped.append(
                f"{row.source_file} {row.row_no}행: "
                f"설비명={equipment or '(빈 값)'} 금형번호={mold_no or '(빈 값)'}"
            )
            continue
        index[equipment] = JigInfo(
            mold_no=mold_no,
            equipment=equipment,
            equipment_code=cell_to_text(row.values.get("equipment_code")),
            line=cell_to_text(row.values.get("line")),
            jig_name=cell_to_text(row.values.get("jig_name")),
        )
    return index, dropped


def extract_runs(
    rows: list[Row], jig_index: dict[str, JigInfo]
) -> tuple[list[UsageRun], JoinLosses]:
    """관리대장 이벤트 → 설비 사용구간.

    위치가 "설비" 인 이벤트가 투입이고, **바로 다음 이벤트**의 시각이 종료다
    (그 이벤트의 위치가 무엇이든 설비를 떠났다는 사실만 중요하다). 마지막
    이벤트가 설비면 아직 가동 중이라 종료가 없다.

    시트 하나가 금형 하나이므로 시트별로 따로 짝짓는다 — 섞으면 A 금형의
    설비 진입이 B 금형의 이벤트로 닫힌다.
    """
    losses = JoinLosses()

    # (파일, 시트) 로 묶는다. 파일이 여럿일 때 같은 시트 이름이 겹칠 수 있다.
    by_sheet: dict[tuple[str, str], list[tuple[datetime, Row]]] = {}
    for row in rows:
        when = to_datetime(row.values.get("event_at"))
        if when is None:
            losses.bad_event_times += 1
            continue
        by_sheet.setdefault((row.source_file, row.sheet), []).append((when, row))

    runs: list[UsageRun] = []
    for (source_file, sheet), events in by_sheet.items():
        # 엑셀 행 순서가 시간순이라는 보장이 없다. 정렬하지 않으면 '다음
        # 이벤트'가 엉뚱한 행이 되어 사용구간이 음수 길이가 된다.
        events.sort(key=lambda pair: pair[0])

        for i, (when, row) in enumerate(events):
            location = cell_to_text(row.values.get("location")) or ""
            if location.strip() != _IN_USE_LOCATION:
                continue

            equipment = cell_to_text(row.values.get("equipment"))
            info = jig_index.get(equipment) if equipment else None
            if info is None:
                # 금형번호를 얻을 수 없으면 이 구간은 어디에도 못 붙는다.
                if equipment and equipment not in losses.unknown_equipment:
                    losses.unknown_equipment.append(equipment)
                continue

            ended = events[i + 1][0] if i + 1 < len(events) else None
            if ended is None:
                losses.open_runs += 1

            runs.append(UsageRun(
                mold_no=info.mold_no,
                equipment=equipment,
                equipment_code=info.equipment_code,
                # 라인은 기준정보 것을 쓴다 — 관리대장 라인은 공장 접두사가
                # 없어 MES 와 직접 비교할 수 없다.
                line=info.line,
                started_at=when.isoformat(),
                ended_at=ended.isoformat() if ended else None,
                source_file=source_file,
                source_sheet=sheet,
            ))

    runs.sort(key=lambda r: (r.mold_no, r.started_at))
    return runs, losses


def latest_locations(rows: list[Row]) -> dict[str, str]:
    """설비명 → 가장 마지막 이벤트의 위치.

    관리대장에 상태 열이 따로 없다. 금형이 **지금 어디에 있는가**가 곧 상태다.
    설비 사용구간(extract_runs)과 달리 여기서는 '설비' 가 아닌 위치도 필요하다 —
    수리실에 있는 금형도 화면에 나와야 하기 때문이다.
    """
    latest: dict[str, tuple[datetime, str]] = {}
    for row in rows:
        when = to_datetime(row.values.get("event_at"))
        equipment = cell_to_text(row.values.get("equipment"))
        location = cell_to_text(row.values.get("location"))
        if when is None or not equipment or not location:
            continue
        prev = latest.get(equipment)
        if prev is None or when >= prev[0]:
            latest[equipment] = (when, location)
    return {equip: loc for equip, (_when, loc) in latest.items()}


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
