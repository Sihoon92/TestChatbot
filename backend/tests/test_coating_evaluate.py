

# ── 레벨 모델이 필요한가 ────────────────────────────────────────────
# 이 단계의 산출물은 "정확한 모델" 이 아니라 "모델이 필요한가" 에 대한 답이다.
# 나이브 예측을 못 이기면 정답은 제품별 표준 조건표이고, 그게 훨씬 싸다.

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app.coating import evaluate  # noqa: E402
from app.coating import schemas as SS  # noqa: E402


def _samples(n_lots, rows_per_lot, wet_of, seed=0, rpm_of=None):
    """lot 마다 제어값이 있고 그에 따라 wet 이 정해지는 절대 샘플."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n_lots):
        rpm = (rpm_of(i) if rpm_of else 100.0 + 5.0 * i)
        for j in range(rows_per_lot):
            out.append({
                SS.LOT: f"L{i:02d}",
                SS.AT: pd.Timestamp("2026-01-01") + pd.Timedelta(hours=6 * i + j),
                SS.PRODUCT: "BNB48X1",
                "bp_open_rate": 50.0, "pump_rpm": rpm,
                "os_gap": 40.0, "ds_gap": 40.0,
                SS.WET_MEAN: wet_of(rpm) + rng.normal(0, 0.02),
            })
    return pd.DataFrame(out)


def test_walk_forward_never_trains_on_the_future():
    """lot i 를 예측할 때 훈련 집합에 lot i 이후가 들어가면 누수다."""
    s = _samples(10, 3, lambda rpm: 0.05 * rpm)
    folds = evaluate.walk_forward_lots(s, min_train_lots=4)
    for train_lots, test_lot in folds:
        assert all(t < test_lot for t in train_lots)
    assert len(folds) == 6


def test_walk_forward_needs_enough_history():
    """훈련 lot 이 모자라면 폴드를 만들지 않는다 - 2개로 4변수를 적합하면
    무엇을 재도 뜻이 없다."""
    s = _samples(3, 3, lambda rpm: 0.05 * rpm)
    assert evaluate.walk_forward_lots(s, min_train_lots=5) == []


def test_level_model_wins_when_control_actually_drives_wet():
    """RPM 이 실제로 두께를 정하는 데이터면 모델이 나이브를 이겨야 한다."""
    s = _samples(20, 3, lambda rpm: 0.05 * rpm)
    r = evaluate.compare_level_models(s, alpha=1.0, min_train_lots=5)
    assert r["mae_model"] < r["mae_prev_lot"]
    assert r["mae_model"] < r["mae_global"]
    assert r["verdict"] == "model_helps"


def test_standard_table_wins_when_wet_ignores_the_controls():
    """제어값이 두께를 못 설명하면 '직전 lot 을 그대로 쓴다' 를 못 이긴다.
    그때의 정답은 모델이 아니라 표준 조건표이고, 그 결론도 산출물이다."""
    s = _samples(20, 3, lambda rpm: 18.2)          # wet 이 rpm 과 무관
    r = evaluate.compare_level_models(s, alpha=1.0, min_train_lots=5)
    assert r["verdict"] == "standard_table"
    assert "표준 조건표" in r["reason"]


def test_comparison_reports_what_it_measured_on():
    """몇 개로 한 말인지가 숫자 옆에 없으면 표본 3건짜리 평균을 믿게 된다."""
    s = _samples(20, 3, lambda rpm: 0.05 * rpm)
    r = evaluate.compare_level_models(s, alpha=1.0, min_train_lots=5)
    assert r["n_lots"] == 20
    assert r["n_eval_lots"] == 15
    # 넷 다 값은 있지만 실제로 변하는 것은 RPM 뿐이다. 둘을 갈라 적어야
    # 읽는 사람이 설명력을 오해하지 않는다.
    assert r["features"] == ["bp_open_rate", "pump_rpm", "os_gap", "ds_gap"]
    assert r["varying_features"] == ["pump_rpm"]


def test_comparison_refuses_when_history_is_too_short():
    """폴드가 하나도 안 나오면 판정하지 않는다 - 숫자를 지어내지 않는다."""
    s = _samples(4, 3, lambda rpm: 0.05 * rpm)
    r = evaluate.compare_level_models(s, alpha=1.0, min_train_lots=6)
    assert r["verdict"] == "insufficient"
    assert r["mae_model"] is None


def test_coefficients_carry_physical_sign():
    """토출량↑ ⇒ 로딩↑. 부호가 뒤집히면 데이터가 물리와 어긋난다는 신호다."""
    s = _samples(20, 3, lambda rpm: 0.05 * rpm)
    r = evaluate.compare_level_models(s, alpha=1.0, min_train_lots=5)
    assert r["coefficients"]["pump_rpm"] > 0


def test_comparison_refuses_when_every_control_is_constant():
    """제어값이 안 변하면 두께 차이를 설명할 수 없다. 모델을 적합해봐야
    무의미하므로 판정하지 않는다."""
    s = _samples(20, 3, lambda rpm: 18.2, rpm_of=lambda i: 100.0)
    r = evaluate.compare_level_models(s, alpha=1.0, min_train_lots=5)
    assert r["verdict"] == "insufficient"
    assert "전부 상수다" in r["reason"]
