"""레벨 모델 — RPM·BP 가 데이터에 없을 수 있다는 가정을 코드가 견뎌야 한다."""
import numpy as np
import pandas as pd

from app.coating.model import level


def test_available_features_drops_all_nan_columns():
    """RPM·BP 가 로깅되지 않는 경우가 설계상 가장 위험한 가정이다.
    전부 NaN 인 피처를 그대로 넣으면 학습이 예외로 죽는다."""
    df = pd.DataFrame({
        "bp_open_rate": [np.nan, np.nan],
        "pump_rpm": [np.nan, np.nan],
        "os_gap": [162.0, 163.0],
        "ds_gap": [162.0, 163.0],
        "product": ["P1", "P1"],
        "wet_mean": [18.2, 18.3],
    })
    assert level.available_features(df) == ["os_gap", "ds_gap"]


def test_fit_level_learns_monotone_relation():
    """토출량이 늘면 로딩이 는다. 부호가 뒤집히면 물리 정합성 게이트에서
    걸려야 한다 — 그 게이트가 의미를 가지려면 여기서 부호가 나와야 한다."""
    rng = np.random.default_rng(0)
    rpm = rng.uniform(100, 200, 300)
    df = pd.DataFrame({
        "bp_open_rate": rng.uniform(40, 60, 300),
        "pump_rpm": rpm,
        "os_gap": rng.uniform(160, 170, 300),
        "ds_gap": rng.uniform(160, 170, 300),
        "product": ["P1"] * 300,
        "wet_mean": 10.0 + 0.05 * rpm + rng.normal(0, 0.01, 300),
    })
    model, feats = level.fit_level(df, alpha=1.0)
    pred_low = model.predict(df.iloc[[0]].assign(pump_rpm=110))[0]
    pred_high = model.predict(df.iloc[[0]].assign(pump_rpm=190))[0]
    assert pred_high > pred_low


def test_product_is_a_fixed_effect_not_a_number():
    """제품 코드를 숫자로 인코딩하면 모델이 제품 사이에 순서를 만든다."""
    df = pd.DataFrame({
        "os_gap": [160.0, 161.0, 160.0, 161.0],
        "ds_gap": [160.0, 161.0, 160.0, 161.0],
        "bp_open_rate": [np.nan] * 4,
        "pump_rpm": [np.nan] * 4,
        "product": ["P1", "P1", "P2", "P2"],
        "wet_mean": [18.0, 18.1, 20.0, 20.1],
    })
    model, _ = level.fit_level(df, alpha=0.01)
    p1 = model.predict(df.iloc[[0]])[0]
    p2 = model.predict(df.iloc[[2]])[0]
    assert p2 - p1 > 1.0
