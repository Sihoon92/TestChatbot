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


def test_funnel_accounts_for_every_drop():
    """단계마다 이름이 붙고, 증감이 앞 단계와 이어져야 한다."""
    ch = _changes([(0, 0, 40.0, 41.0), (5, 1, 40.0, 41.0), (100, 2, 40.0, 41.0)])
    ev, dl, iso = _pipeline(ch)
    readings = pd.DataFrame({S.LOT: ["L1"] * 50})
    deduped = pd.DataFrame({S.LOT: ["L1"] * 20})
    f = trace.funnel(readings, deduped, ch, iso)
    stages = list(f["stage"])
    assert stages[0] == "원본 행"
    assert f.iloc[0]["n"] == 50
    assert f.iloc[1]["n"] == 20
    assert f.iloc[1]["delta"] == -30
    # 마지막은 격리 통과
    assert stages[-1] == "격리 통과"
    assert f.iloc[-1]["n"] == 1


def test_funnel_names_the_rejection_breakdown():
    """탈락을 앞/뒤/양쪽으로 갈라야 창을 어느 쪽으로 움직일지 정해진다.

    "앞"·"뒤" 는 note 템플릿의 라벨이라 값이 0 이어도, 자리가 뒤바뀌어도
    항상 등장한다 - 글자 존재만 보면 숫자가 틀려도 통과한다. 그래서 값 자체를
    박는다. 0→5(뒤가 짧음)→100(앞이 짧음) 이므로 앞 1·뒤 1·양쪽 0 이어야 한다.
    """
    ch = _changes([(0, 0, 40.0, 41.0), (5, 1, 40.0, 41.0), (100, 2, 40.0, 41.0)])
    _, _, iso = _pipeline(ch)
    f = trace.funnel(pd.DataFrame({S.LOT: []}), pd.DataFrame({S.LOT: []}), ch, iso)
    note = f[f["stage"] == "격리 통과"].iloc[0]["note"]
    assert note == "탈락 앞 1 · 뒤 1 · 양쪽 0"


def test_funnel_rejection_buckets_partition_the_rejected_events():
    """세 갈래(앞/뒤/양쪽)의 합이 탈락 건수와 같아야 한다.

    사유 문자열이 세 접두어 중 어느 것과도 안 맞으면 그 이벤트는 note 집계에서
    조용히 사라진다 - 개수가 안 맞는데도 예외 없이 넘어간다. 이 등식이 그
    누락을 잡는 유일한 자리다. 0→3→8 이 연달아 짧아 뒤/양쪽/앞 세 사유를
    한 이벤트씩 만들고, 200 은 앞뒤 모두 넉넉해 격리를 통과한다:
      t=0  : gap_after =3  <10           -> "뒤 3.0<10"
      t=3  : gap_before=3, gap_after=5<10 -> "앞뒤 3.0/5.0<10/10"
      t=8  : gap_before=5  <10           -> "앞 5.0<10"
      t=200: 앞뒤 모두 충분               -> 격리
    """
    ch = _changes([(0, 0, 40.0, 41.0), (3, 1, 40.0, 41.0),
                   (8, 2, 40.0, 41.0), (200, 3, 40.0, 41.0)])
    _, _, iso = _pipeline(ch)

    ok = iso["isolated"].astype(bool)
    reasons = iso.loc[~ok, S.ISO_REASON]
    n_before = int(reasons.str.startswith("앞 ").sum())
    n_after = int(reasons.str.startswith("뒤 ").sum())
    n_both = int(reasons.str.startswith("앞뒤").sum())
    assert (n_before, n_after, n_both) == (1, 1, 1)
    assert n_before + n_after + n_both == len(iso) - int(ok.sum())

    f = trace.funnel(pd.DataFrame({S.LOT: []}), pd.DataFrame({S.LOT: []}), ch, iso)
    note = f[f["stage"] == "격리 통과"].iloc[0]["note"]
    assert note == "탈락 앞 1 · 뒤 1 · 양쪽 1"


def test_funnel_counts_fragments_of_continuous_runs():
    # 0→1.5→3 은 한 연쇄인데 앵커가 둘로 쪼갠다. 그 둘이 "조각" 이다.
    ch = _changes([(0, 0, 40.0, 41.0), (1.5, 1, 40.0, 41.0), (3, 2, 40.0, 41.0),
                   (100, 3, 40.0, 41.0)])
    _, _, iso = _pipeline(ch)
    f = trace.funnel(pd.DataFrame({S.LOT: []}), pd.DataFrame({S.LOT: []}), ch, iso)
    row = f[f["stage"] == "연속 조작 구간 조각"].iloc[0]
    assert row["n"] == 2


def test_legacy_events_fold_fragments_back_into_runs():
    """구 규칙의 묶음 = 연쇄 = run. 접으면 그대로 나온다."""
    ch = _changes([(0, 0, 40.0, 41.0), (1.5, 1, 40.0, 41.0), (3, 2, 40.0, 41.0),
                   (100, 3, 40.0, 41.0)])
    _, dl, iso = _pipeline(ch)
    old = trace.legacy_events(iso)
    assert len(old) == 2                      # 앞 셋이 한 연쇄
    assert S.LAST_AT not in old.columns       # 시작 기준으로 재게 한다


def test_rule_comparison_finds_events_only_the_old_rule_kept():
    """연쇄가 삼켰던 긴 구간이 여기서 사례로 나온다."""
    ch = _changes([(0, 0, 40.0, 41.0), (1.5, 1, 40.0, 41.0), (3, 2, 40.0, 41.0),
                   (4.5, 3, 40.0, 41.0), (200, 4, 40.0, 41.0)])
    _, dl, iso = _pipeline(ch)
    cmp = trace.rule_comparison(iso, dl, _bounds(), 30, 60, 10, 10)
    # 연쇄로 보면 0~4.5 가 한 덩어리 + 200 = 2. 앵커로 보면 {0,1.5} {3,4.5} {200} = 3.
    assert cmp["old"]["n_clusters"] == 2
    assert cmp["new"]["n_clusters"] == 3
    assert cmp["only_old"] >= 1
    assert cmp["only_old_examples"]
    ex = cmp["only_old_examples"][0]
    assert ex["n_fragments"] >= 2


def test_rule_comparison_counts_overlap_at_the_run_level():
    """구는 run, 신은 이벤트라 단위가 다르다. 겹침은 run 으로 센다."""
    ch = _changes([(0, 0, 40.0, 41.0), (200, 1, 40.0, 41.0)])
    _, dl, iso = _pipeline(ch, pre=10, post=10)
    cmp = trace.rule_comparison(iso, dl, _bounds(), 10, 10, 10, 10)
    assert cmp["both"] + cmp["only_new"] + cmp["only_old"] >= 1
