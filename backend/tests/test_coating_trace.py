"""전처리 로그 — 사람이 눈으로 검증하려면 각 줄이 손검증 가능해야 한다.

실데이터는 사내 PC 에만 있고 여기서는 볼 수 없다(CLAUDE.md). 그래서 이 표들의
계약을 합성으로 못박아 둔다 - 열이 조용히 바뀌면 사용자가 읽는 법을 잃는다.
"""
import pandas as pd
import pytest

from app.coating import events as ev_mod
from app.coating import schemas as S
from app.coating import trace

BASE = pd.Timestamp("2026-02-02 06:00")


def _changes(rows, lot="L1"):
    """(분, zone인덱스, prev, value) 목록으로 제어값 변경 표를 만든다."""
    return pd.DataFrame([
        {S.LOT: lot, S.ITEM: S.GAP_ITEM_IDS[zi], S.AT: BASE + pd.Timedelta(minutes=m),
         S.VALUE: v, S.PREV_VALUE: p}
        for m, zi, p, v in rows
    ])


def _bounds(lot="L1", start_min=-30, end_min=300):
    return pd.DataFrame([{
        S.LOT: lot,
        "start": BASE + pd.Timedelta(minutes=start_min),
        "end": BASE + pd.Timedelta(minutes=end_min),
    }])


def _pipeline(changes, pre=10, post=10, merge=2):
    ev, dl = ev_mod.build_events(changes, merge)
    iso = ev_mod.isolation(ev, pre, post, _bounds())
    return ev, dl, iso


def test_ledger_has_one_row_per_event_with_raw_timestamps():
    """원시 시각이 있어야 01_changes_control.csv 와 손으로 대조할 수 있다."""
    ch = _changes([(0, 0, 40.0, 41.0), (1, 1, 40.0, 41.0), (100, 2, 40.0, 41.0)])
    ev, dl, iso = _pipeline(ch)
    led = trace.event_ledger(iso, dl)
    assert len(led) == 2
    assert led.iloc[0][S.AT] == BASE
    assert led.iloc[0][S.LAST_AT] == BASE + pd.Timedelta(minutes=1)
    assert led.iloc[0]["zones"] == "z1,z2"


def test_ledger_blanks_run_id_when_the_run_has_one_event():
    """눈에 띄어야 할 것은 쪼개진 것뿐이다."""
    ch = _changes([(0, 0, 40.0, 41.0), (100, 1, 40.0, 41.0)])
    _, dl, iso = _pipeline(ch)
    led = trace.event_ledger(iso, dl)
    assert list(led[S.RUN]) == ["", ""]


def test_ledger_keeps_run_id_when_a_run_was_split():
    # 0→1.5→3 은 직전 변경과 매번 2분 이내(= 한 연쇄)지만 전체 폭이 3분이라
    # 앵커는 둘로 쪼갠다. run_id 가 그 둘을 다시 묶어 보여야 한다.
    ch = _changes([(0, 0, 40.0, 41.0), (1.5, 1, 40.0, 41.0), (3, 2, 40.0, 41.0),
                   (100, 3, 40.0, 41.0)])
    _, dl, iso = _pipeline(ch)
    led = trace.event_ledger(iso, dl).set_index(S.EVENT)
    split = led[led[S.RUN] != ""]
    assert len(split) == 2
    assert split[S.RUN].nunique() == 1


def test_ledger_flags_a_zone_touched_twice_in_one_event():
    """Δgap 이 눌릴 수 있는 자리다. 표시가 없으면 사람이 못 찾는다."""
    ch = _changes([(0, 0, 40.0, 41.0), (1, 0, 41.0, 42.0)])
    _, dl, iso = _pipeline(ch)
    led = trace.event_ledger(iso, dl)
    assert led.iloc[0]["n_dup_zones"] == 1


def test_ledger_carries_the_isolation_verdict_and_reason():
    ch = _changes([(0, 0, 40.0, 41.0), (5, 1, 40.0, 41.0), (100, 2, 40.0, 41.0)])
    _, dl, iso = _pipeline(ch)
    led = trace.event_ledger(iso, dl)
    assert list(led["isolated"]) == [False, False, True]
    assert led.iloc[0][S.ISO_REASON] == "뒤 5.0<10"
