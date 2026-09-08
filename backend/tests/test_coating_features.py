"""샘플 테이블 — Wet=0 을 0 으로 두면 레벨이 통째로 왜곡된다."""
import numpy as np
import pytest
import pandas as pd

from app.coating import features
from app.coating import schemas as S


def _deduped(rows):
    return pd.DataFrame(
        [
            {S.LOT: r[0], S.AT: pd.Timestamp(r[1]), S.ITEM: r[2], S.VALUE: r[3],
             S.IO: S.IO_OUTPUT if r[2].startswith("9") else S.IO_INPUT}
            for r in rows
        ]
    )


def test_wet_zero_becomes_nan_not_zero():
    """0 은 '로딩 0' 이 아니라 '미사용/미측정' 이다. 평균에 0 이 섞이면
    25 zone 평균이 실제보다 크게 낮아진다."""
    d = _deduped([
        ("L1", "2026-01-31 18:55", "90030611", 18.2),
        ("L1", "2026-01-31 18:55", "90030628", 0.0),
    ])
    w = features.wet_wide(d)
    assert w.iloc[0]["z1"] == 18.2
    assert np.isnan(w.iloc[0]["z18"])


def test_valid_zones_excludes_always_missing():
    d = _deduped([
        ("L1", "2026-01-31 18:55", "90030611", 18.2),
        ("L1", "2026-01-31 18:56", "90030611", 18.3),
        ("L1", "2026-01-31 18:55", "90030628", 0.0),
    ])
    v = features.valid_zones(features.wet_wide(d))
    assert 1 in v
    assert 18 not in v


def test_wet_mean_ignores_invalid_zones():
    d = _deduped([
        ("L1", "2026-01-31 18:55", "90030611", 18.0),
        ("L1", "2026-01-31 18:55", "90030612", 18.4),
        ("L1", "2026-01-31 18:55", "90030628", 0.0),
    ])
    w = features.wet_wide(d)
    wm = features.wet_mean_series(w, valid=[1, 2])
    assert wm.iloc[0][S.WET_MEAN] == 18.2


def test_delta_samples_only_use_clean_events():
    """오염 이벤트는 델타 샘플에 들어가면 안 된다."""
    ev = pd.DataFrame({
        S.LOT: ["L1", "L1"],
        S.EVENT: ["L1#1", "L1#2"],
        S.AT: pd.to_datetime(["2026-01-31 19:00", "2026-01-31 19:20"]),
        S.SETTLED_AT: pd.to_datetime(["2026-01-31 19:05", "2026-01-31 19:25"]),
        S.CONTAMINATED: [False, True],
        S.DROP_REASON: [None, "overlapped"],
    })
    dl = pd.DataFrame({
        S.EVENT: ["L1#1", "L1#2"],
        S.ITEM: ["30030838", "30030838"],
        S.ZONE: [1.0, 1.0],
        S.DELTA: [5.0, 7.0],
    })
    times = pd.to_datetime([f"2026-01-31 19:{m:02d}" for m in range(0, 30)])
    w = pd.DataFrame({S.LOT: ["L1"] * 30, S.AT: times})
    for z in range(1, 26):
        w[f"z{z}"] = [18.2] * 15 + [18.5] * 15
    out = features.delta_samples(ev, dl, w, valid=list(range(1, 26)), window_minutes=3)
    assert list(out[S.EVENT]) == ["L1#1"]
    assert out.iloc[0]["dg1"] == 5.0


def test_delta_samples_sums_repeated_adjustments_of_one_zone():
    """한 이벤트 안에서 같은 zone 을 두 번 만졌으면 Δgap 은 합이다.

    덮어쓰면 마지막 것만 남아 Δgap 이 작아지는데, ΔWet 은 전체 변화를 담으므로
    게인이 그 비율만큼 부풀려진다.
    """
    ev = pd.DataFrame({
        S.LOT: ["L1"],
        S.EVENT: ["L1#1"],
        S.AT: pd.to_datetime(["2026-01-31 19:00"]),
        S.SETTLED_AT: pd.to_datetime(["2026-01-31 19:05"]),
        S.CONTAMINATED: [False],
        S.DROP_REASON: [None],
    })
    # 같은 zone1 을 +1 씩 세 번 = 총 +3
    dl = pd.DataFrame({
        S.EVENT: ["L1#1", "L1#1", "L1#1"],
        S.ITEM: ["30030838"] * 3,
        S.ZONE: [1.0, 1.0, 1.0],
        S.DELTA: [1.0, 1.0, 1.0],
    })
    times = pd.to_datetime([f"2026-01-31 19:{m:02d}" for m in range(0, 30)])
    w = pd.DataFrame({S.LOT: ["L1"] * 30, S.AT: times})
    for z in range(1, 26):
        w[f"z{z}"] = [18.2] * 15 + [18.5] * 15
    out = features.delta_samples(ev, dl, w, valid=list(range(1, 26)), window_minutes=3)
    assert out.iloc[0]["dg1"] == 3.0


def test_delta_columns_are_25_each():
    assert len(features.GAP_DELTA_COLS) == 25
    assert len(features.WET_DELTA_COLS) == 25
    assert features.GAP_DELTA_COLS[0] == "dg1"
    assert features.WET_DELTA_COLS[24] == "dw25"


# ── 절대 샘플 · lot 최종 조건 ───────────────────────────────────────
# 레벨 모델과 베이스라인의 입력이다. 델타 샘플(이벤트별 변화량)과 달리
# "이 제어 상태에서 평균 두께가 얼마였나" 를 한 줄로 만든다.

import pandas as pd  # noqa: E402

from app.coating import schemas as SS  # noqa: E402


def _changes(rows) -> pd.DataFrame:
    """(lot, item, 시각, 값, 직전값) 튜플로 changes 표를 만든다."""
    return pd.DataFrame(
        [{SS.LOT: l, SS.ITEM: i, SS.AT: pd.Timestamp(t), SS.VALUE: v,
          SS.PREV_VALUE: p} for l, i, t, v, p in rows]
    )


def _bounds(rows) -> pd.DataFrame:
    return pd.DataFrame(
        [{SS.LOT: l, "start": pd.Timestamp(s), "end": pd.Timestamp(e),
          SS.PRODUCT: pr} for l, s, e, pr in rows]
    )


def _wet_mean(lot, start, minutes, value) -> pd.DataFrame:
    at = pd.date_range(pd.Timestamp(start), periods=minutes, freq="min")
    return pd.DataFrame({SS.LOT: lot, SS.AT: at, SS.WET_MEAN: value})


def test_absolute_samples_makes_one_row_per_stable_window():
    """제어값이 안 바뀐 구간마다 한 줄. 여기서는 변경이 한 번 있으므로 두 줄."""
    ch = _changes([
        ("L1", "50030111", "2026-01-01 00:00", 100.0, None),
        ("L1", "10030009", "2026-01-01 00:00", 50.0, None),
        ("L1", "50030111", "2026-01-01 01:00", 120.0, 100.0),
    ])
    bounds = _bounds([("L1", "2026-01-01 00:00", "2026-01-01 02:00", "BNB48X1")])
    wm = _wet_mean("L1", "2026-01-01 00:00", 121, 18.0)
    out = features.absolute_samples(ch, wm, bounds, wait_minutes=10, min_window_minutes=20)
    assert len(out) == 2
    assert list(out[SS.PRODUCT]) == ["BNB48X1", "BNB48X1"]


def test_absolute_samples_carries_the_control_state_of_each_window():
    """두 번째 구간은 바뀐 RPM 을 들고 있어야 한다. 안 그러면 입력과 출력이
    어긋난 채로 학습된다."""
    ch = _changes([
        ("L1", "50030111", "2026-01-01 00:00", 100.0, None),
        ("L1", "10030009", "2026-01-01 00:00", 50.0, None),
        ("L1", "50030111", "2026-01-01 01:00", 120.0, 100.0),
    ])
    bounds = _bounds([("L1", "2026-01-01 00:00", "2026-01-01 02:00", "BNB48X1")])
    wm = _wet_mean("L1", "2026-01-01 00:00", 121, 18.0)
    out = features.absolute_samples(ch, wm, bounds, 10, 20).sort_values(SS.AT)
    assert list(out["pump_rpm"]) == [100.0, 120.0]
    assert list(out["bp_open_rate"]) == [50.0, 50.0]   # 안 바뀐 값은 유지된다


def test_absolute_samples_waits_before_measuring():
    """조정 직후는 아직 반영 전이다. wait 만큼 지난 뒤부터 평균 낸다."""
    ch = _changes([("L1", "50030111", "2026-01-01 00:00", 100.0, None)])
    bounds = _bounds([("L1", "2026-01-01 00:00", "2026-01-01 01:00", "P")])
    wm = _wet_mean("L1", "2026-01-01 00:00", 61, 18.0)
    wm.loc[wm[SS.AT] < pd.Timestamp("2026-01-01 00:30"), SS.WET_MEAN] = 99.0
    out = features.absolute_samples(ch, wm, bounds, wait_minutes=30, min_window_minutes=20)
    assert len(out) == 1
    assert out[SS.WET_MEAN].iloc[0] == pytest.approx(18.0)


def test_absolute_samples_drops_windows_too_short_to_settle():
    """짧은 구간은 반영이 끝나기 전이라 그 Wet 은 이 제어값의 결과가 아니다."""
    ch = _changes([
        ("L1", "50030111", "2026-01-01 00:00", 100.0, None),
        ("L1", "50030111", "2026-01-01 00:05", 120.0, 100.0),
        ("L1", "50030111", "2026-01-01 00:10", 130.0, 120.0),
    ])
    bounds = _bounds([("L1", "2026-01-01 00:00", "2026-01-01 00:15", "P")])
    wm = _wet_mean("L1", "2026-01-01 00:00", 16, 18.0)
    assert features.absolute_samples(ch, wm, bounds, 10, 20).empty


def test_absolute_samples_skips_windows_without_wet():
    """Wet 이 없는 구간은 버린다 - 0 으로 채우면 레벨이 통째로 왜곡된다."""
    ch = _changes([("L1", "50030111", "2026-01-01 00:00", 100.0, None)])
    bounds = _bounds([("L1", "2026-01-01 00:00", "2026-01-01 02:00", "P")])
    wm = _wet_mean("L1", "2026-01-01 00:00", 121, 18.0)
    wm[SS.WET_MEAN] = np.nan
    assert features.absolute_samples(ch, wm, bounds, 10, 20).empty


def test_lot_finals_takes_the_last_control_state_of_each_lot():
    """베이스라인이 쓰는 '그 lot 이 최종적으로 안착한 조건'."""
    ch = _changes([
        ("L1", "50030111", "2026-01-01 00:00", 100.0, None),
        ("L1", "50030111", "2026-01-01 01:00", 120.0, 100.0),
        ("L2", "50030111", "2026-01-02 00:00", 90.0, None),
    ])
    bounds = _bounds([
        ("L1", "2026-01-01 00:00", "2026-01-01 02:00", "BNB48X1"),
        ("L2", "2026-01-02 00:00", "2026-01-02 02:00", "BNB48X1"),
    ])
    out = features.lot_finals(ch, bounds).set_index(SS.LOT)
    assert out.loc["L1", "pump_rpm"] == 120.0
    assert out.loc["L2", "pump_rpm"] == 90.0
    assert out.loc["L1", SS.PRODUCT] == "BNB48X1"


def test_lot_finals_orders_by_lot_end_so_baselines_never_see_the_future():
    """베이스라인은 과거만 본다. 그 '과거' 를 정하는 시각이 여기서 나온다."""
    ch = _changes([
        ("L2", "50030111", "2026-01-02 00:00", 90.0, None),
        ("L1", "50030111", "2026-01-01 00:00", 100.0, None),
    ])
    bounds = _bounds([
        ("L1", "2026-01-01 00:00", "2026-01-01 02:00", "P"),
        ("L2", "2026-01-02 00:00", "2026-01-02 02:00", "P"),
    ])
    out = features.lot_finals(ch, bounds)
    assert list(out[SS.LOT]) == ["L1", "L2"]
    assert out[SS.AT].is_monotonic_increasing
