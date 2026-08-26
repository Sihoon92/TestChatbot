"""샘플 테이블 — Wet=0 을 0 으로 두면 레벨이 통째로 왜곡된다."""
import numpy as np
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


def test_delta_columns_are_25_each():
    assert len(features.GAP_DELTA_COLS) == 25
    assert len(features.WET_DELTA_COLS) == 25
    assert features.GAP_DELTA_COLS[0] == "dg1"
    assert features.WET_DELTA_COLS[24] == "dw25"
