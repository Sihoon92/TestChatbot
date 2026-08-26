"""베이스라인과 지표 — 여기서 미래 정보가 새면 모델이 베이스라인을
'이기는 것처럼' 보인다."""
import numpy as np
import pandas as pd

from app.coating import baseline, evaluate

CTRL = ["os_gap", "ds_gap"]


def _finals():
    return pd.DataFrame({
        "lot_id": ["L1", "L2", "L3", "L4"],
        "product": ["P1", "P1", "P2", "P1"],
        "worked_at": pd.to_datetime([
            "2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04",
        ]),
        "os_gap": [160.0, 162.0, 200.0, 164.0],
        "ds_gap": [161.0, 163.0, 201.0, 165.0],
    })


def test_prev_lot_baseline_uses_same_product_only():
    """P2 의 조건이 P1 에 새어 들어가면 베이스라인이 비현실적으로 나빠지고,
    모델이 쉽게 이기는 것처럼 보인다."""
    out = baseline.prev_lot_baseline(_finals(), CTRL).set_index("lot_id")
    assert out.loc["L2", "os_gap"] == 160.0
    assert out.loc["L4", "os_gap"] == 162.0
    assert "L3" not in out.index  # P2 의 첫 lot — 직전이 없다
    assert "L1" not in out.index


def test_median_baseline_uses_past_only():
    """전체 중앙값을 쓰면 미래 lot 이 과거 예측에 들어간다 = 누수."""
    out = baseline.median_baseline(_finals(), CTRL).set_index("lot_id")
    assert out.loc["L2", "os_gap"] == 160.0
    assert out.loc["L4", "os_gap"] == 161.0  # median(160, 162)


def test_hit_rate_requires_all_zones_in_spec():
    """평균만 보면 한쪽이 두껍고 반대쪽이 얇은 최악의 프로파일이 통과한다."""
    inside = np.array([[18.2, 18.3, 18.25]])
    one_out = np.array([[18.2, 18.9, 18.25]])
    assert evaluate.hit_rate(inside, target=18.23, tol=0.4) == 1.0
    assert evaluate.hit_rate(one_out, target=18.23, tol=0.4) == 0.0


def test_hit_rate_ignores_nan_zones():
    """미사용 zone 이 판정을 떨어뜨리면 안 된다."""
    wet = np.array([[18.2, np.nan, 18.25]])
    assert evaluate.hit_rate(wet, target=18.23, tol=0.4) == 1.0


def test_cross_width_sigma_ignores_nan():
    wet = np.array([[18.0, 18.4, np.nan]])
    assert np.isclose(evaluate.cross_width_sigma(wet)[0], 0.2)
