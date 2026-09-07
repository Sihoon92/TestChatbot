"""조정 이벤트 — 묶음이 틀리면 하나의 Wet 변화가 여러 이벤트에 중복
귀속되어 각 zone 의 영향이 부풀려진다."""
import pandas as pd

from app.coating import events
from app.coating import schemas as S


def _changes(rows):
    return pd.DataFrame(
        [
            {S.LOT: r[0], S.ITEM: r[1], S.AT: pd.Timestamp(r[2]), S.VALUE: r[3], S.PREV_VALUE: r[4]}
            for r in rows
        ]
    )


def test_simultaneous_changes_become_one_event():
    """작업자는 여러 zone 을 한 번에 만진다. 항목별로 쪼개면 안 된다."""
    c = _changes([
        ("L1", "30030838", "2026-01-31 19:00", 305.0, 300.0),
        ("L1", "30030839", "2026-01-31 19:01", 402.0, 400.0),
    ])
    ev, dl = events.build_events(c, merge_minutes=2)
    assert len(ev) == 1
    assert ev.iloc[0]["n_items"] == 2
    assert set(dl[S.DELTA]) == {5.0, 2.0}


def test_changes_beyond_window_become_separate_events():
    c = _changes([
        ("L1", "30030838", "2026-01-31 19:00", 305.0, 300.0),
        ("L1", "30030838", "2026-01-31 19:10", 310.0, 305.0),
    ])
    ev, _ = events.build_events(c, merge_minutes=2)
    assert len(ev) == 2


def test_initial_values_are_not_events():
    """lot 시작값은 '사람이 바꾼 것' 이 아니다. 이걸 이벤트로 세면
    모든 lot 이 25개짜리 가짜 이벤트를 하나씩 갖게 된다."""
    c = _changes([("L1", "30030838", "2026-01-31 18:55", 300.0, float("nan"))])
    ev, dl = events.build_events(c, merge_minutes=2)
    assert len(ev) == 0
    assert len(dl) == 0


def test_output_items_are_not_events():
    """Wet 은 측정값이다. 측정 변화를 조정으로 세면 인과가 뒤집힌다."""
    c = _changes([("L1", "90030611", "2026-01-31 19:00", 18.3, 18.2)])
    ev, _ = events.build_events(c, merge_minutes=2)
    assert len(ev) == 0


def test_events_do_not_span_lots():
    c = _changes([
        ("L1", "30030838", "2026-01-31 19:00", 305.0, 300.0),
        ("L2", "30030838", "2026-01-31 19:01", 305.0, 300.0),
    ])
    ev, _ = events.build_events(c, merge_minutes=5)
    assert len(ev) == 2


def test_zone_is_attached_to_gap_deltas():
    """영향행렬은 zone 인덱스로 조립된다. zone 이 없으면 조립 불가."""
    c = _changes([("L1", "30030843", "2026-01-31 19:00", 316.0, 315.0)])
    _, dl = events.build_events(c, merge_minutes=2)
    assert dl.iloc[0][S.ZONE] == 6


# ── 제어 구간 격리 ──────────────────────────────────────────────────
# "홀로 선 이벤트" 를 이벤트 시각만으로 가른다. Wet 을 안 보므로 L 을 몰라도
# 쓸 수 있고, 그래서 L 을 재는 데 쓸 수 있다 - annotate_settling 의 순환을
# 끊는 것이 이 함수의 존재 이유다.

import pandas as pd  # noqa: E402

from app.coating import events as ev_mod  # noqa: E402
from app.coating import schemas as SS  # noqa: E402


def _ev(offsets, lot="L1"):
    base = pd.Timestamp("2026-02-02 06:00")
    return pd.DataFrame([
        {SS.LOT: lot, SS.EVENT: f"{lot}#{i+1}",
         SS.AT: base + pd.Timedelta(minutes=m), "n_items": 1}
        for i, m in enumerate(offsets)
    ])


def _bounds(lot, start_min, end_min):
    base = pd.Timestamp("2026-02-02 06:00")
    return pd.DataFrame([{
        SS.LOT: lot,
        "start": base + pd.Timedelta(minutes=start_min),
        "end": base + pd.Timedelta(minutes=end_min),
    }])


def test_isolation_measures_gaps_to_the_neighbours():
    out = ev_mod.isolation(_ev([0, 40, 100]), pre_minutes=10, post_minutes=10)
    assert list(out["gap_before"].fillna(-1)) == [-1, 40, 60]
    assert list(out["gap_after"].fillna(-1)) == [40, 60, -1]


def test_isolation_rejects_events_that_are_too_close():
    """조정 2분 뒤에 또 조정이 오면 앞 것의 반응을 못 본다."""
    out = ev_mod.isolation(_ev([0, 2, 90]), pre_minutes=30, post_minutes=60)
    assert list(out["isolated"]) == [False, False, True]


def test_isolation_looks_both_ways():
    """앞도 본다. annotate_settling 이 다음 이벤트만 보다가 B 를 놓쳤던 자리다."""
    out = ev_mod.isolation(_ev([0, 2, 200]), pre_minutes=30, post_minutes=60)
    # 2분짜리 이벤트는 앞이 2분밖에 안 떨어져 있어 탈락해야 한다
    assert out.loc[1, "isolated"] is False or not out.loc[1, "isolated"]


def test_lone_event_in_a_lot_is_isolated():
    """이웃이 아예 없으면 격리된 것이다 - 없는 이웃을 이유로 버리면 안 된다."""
    out = ev_mod.isolation(_ev([50]), pre_minutes=30, post_minutes=60)
    assert bool(out["isolated"].iloc[0]) is True


def test_lot_edges_count_as_walls_when_bounds_given():
    """lot 시작 3분 뒤 이벤트는 기준선을 잴 앞 구간이 없다. 이웃이 없어도 탈락."""
    ev = _ev([3])
    out = ev_mod.isolation(ev, pre_minutes=30, post_minutes=60,
                           bounds=_bounds("L1", 0, 200))
    assert bool(out["isolated"].iloc[0]) is False
    out2 = ev_mod.isolation(ev, pre_minutes=1, post_minutes=60,
                            bounds=_bounds("L1", 0, 200))
    assert bool(out2["isolated"].iloc[0]) is True


def test_lot_end_is_a_wall_too():
    """반응이 끝나기 전에 lot 이 끝나면 최종값을 못 읽는다."""
    out = ev_mod.isolation(_ev([180]), pre_minutes=30, post_minutes=60,
                           bounds=_bounds("L1", 0, 200))
    assert bool(out["isolated"].iloc[0]) is False


def test_isolation_does_not_cross_lots():
    """다른 lot 의 이벤트는 이웃이 아니다 - 다른 물건이다."""
    ev = pd.concat([_ev([0], "L1"), _ev([1], "L2")], ignore_index=True)
    out = ev_mod.isolation(ev, pre_minutes=30, post_minutes=60)
    assert list(out["isolated"]) == [True, True]


def test_isolation_table_shows_the_tradeoff():
    """창을 넓힐수록 이벤트가 준다. 그 곡선이 곧 '무엇을 요구할지' 의 근거다."""
    t = ev_mod.isolation_table(_ev([0, 20, 45, 120, 300]), windows=(10, 30, 60))
    assert list(t["post_minutes"]) == [10, 30, 60]
    assert t["n_isolated"].is_monotonic_decreasing
    assert t["n_isolated"].iloc[0] >= t["n_isolated"].iloc[-1]


def test_isolation_table_reports_total_for_context():
    """몇 건 중 몇 건인지가 없으면 숫자를 못 읽는다."""
    t = ev_mod.isolation_table(_ev([0, 20, 45]), windows=(10,))
    assert t["n_events"].iloc[0] == 3


# ── 병합창 자체를 의심하기 ──────────────────────────────────────────
# "이벤트 35건" 은 관측이 아니라 merge_minutes=2 가 만든 숫자다. 그 값이
# 맞는지는 데이터가 답해야 하고, 답하려면 바꿔가며 재 봐야 한다.


def _chg(offsets, lot="L1"):
    base = pd.Timestamp("2026-02-02 06:00")
    return pd.DataFrame([
        {SS.LOT: lot, SS.ITEM: SS.GAP_ITEM_IDS[i % 25],
         SS.AT: base + pd.Timedelta(minutes=m),
         SS.VALUE: 41.0, SS.PREV_VALUE: 40.0}
        for i, m in enumerate(offsets)
    ])


def test_change_gaps_are_intervals_between_consecutive_changes():
    g = ev_mod.change_gaps(_chg([0, 5, 10, 300]))
    assert list(g.dropna()) == [5.0, 5.0, 290.0]


def test_change_gaps_do_not_cross_lots():
    ch = pd.concat([_chg([0, 5], "L1"), _chg([0, 3], "L2")], ignore_index=True)
    assert sorted(ev_mod.change_gaps(ch).dropna()) == [3.0, 5.0]


def test_gap_histogram_shows_where_the_valley_is():
    """세션 안 간격(5분)과 세션 사이 간격(300분)이 갈리면 골이 보인다.
    그 골이 곧 병합창으로 쓸 값이다."""
    h = ev_mod.gap_histogram(_chg([0, 5, 10, 300, 305, 600]))
    counts = dict(zip(h["bucket"], h["n"]))
    assert counts["3~5분"] + counts["5~10분"] == 3       # 세션 안
    assert counts["60분+"] == 2                          # 세션 사이
    assert counts["1~2분"] == 0                          # 골


def test_merge_sensitivity_shows_data_lost_to_a_narrow_window():
    """병합창이 좁으면 한 번의 튜닝이 쪼개지고, 쪼개진 것들이 서로를 탈락시킨다."""
    ch = _chg([0, 5, 10, 300, 303, 600])
    t = ev_mod.merge_sensitivity(ch, merge_windows=(2, 6), pre_minutes=30,
                                 post_minutes=60).set_index("merge_minutes")
    assert t.loc[2, "n_clusters"] == 6
    assert t.loc[6, "n_clusters"] == 3
    # 넓히면 쓸 수 있는 것이 늘어난다 - 이것이 이 표의 요점이다
    assert t.loc[6, "n_isolated"] > t.loc[2, "n_isolated"]
    assert t.loc[6, "n_items"] > t.loc[2, "n_items"]


def test_merge_sensitivity_counts_items_not_just_events():
    """이벤트 수보다 Δgap 항목 수가 중요하다 - 커널이 먹는 것은 항목이다."""
    ch = _chg([0, 1, 2])                      # 2분 창에서 한 묶음, 3항목
    t = ev_mod.merge_sensitivity(ch, merge_windows=(2,), pre_minutes=1,
                                 post_minutes=1)
    assert t["n_clusters"].iloc[0] == 1
    assert t["n_items"].iloc[0] == 3


def test_merge_sensitivity_is_zero_when_nothing_was_adjusted():
    """제어값이 한 번도 안 바뀐 구간. 행이 없는 것이 아니라 0 이 나와야 한다 -
    표가 통째로 비면 '안 쟀다' 와 '재서 0 이다' 를 구별할 수 없다."""
    empty = _chg([0]).iloc[0:0]                 # 열은 있고 행만 없다
    t = ev_mod.merge_sensitivity(empty, merge_windows=(2, 6),
                                 pre_minutes=1, post_minutes=1)
    assert list(t["n_clusters"]) == [0, 0]
    assert list(t["n_items"]) == [0, 0]
