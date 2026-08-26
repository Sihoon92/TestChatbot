"""분할과 지표.

'예측 정확도' 와 '제안 품질' 을 분리해서 잰다. 전자는 surrogate 가 Wet 을
맞히는가, 후자는 그 surrogate 위에서 뽑은 제안이 스펙에 드는가다.
둘을 하나의 점수로 뭉치면 어느 쪽이 문제인지 영영 알 수 없다.
"""
import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold


def hit_rate(pred_wet: np.ndarray, target: float, tol: float) -> float:
    """모든 유효 zone 이 각각 스펙 내인 샘플의 비율 (AND 판정).

    NaN zone(미사용/미측정)은 판정에서 뺀다 — 0 으로 두면 전부 불합격이 된다.
    """
    dev = np.abs(pred_wet - target)
    ok = np.where(np.isnan(dev), True, dev <= tol)
    return float(ok.all(axis=1).mean())


def level_mae(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.nanmean(np.abs(np.asarray(y_true) - np.asarray(y_pred))))


def profile_rmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """레벨 성분을 뺀 뒤의 오차. 레벨이 틀린 것과 모양이 틀린 것은 다른 문제다."""
    t = np.asarray(y_true, dtype=float)
    p = np.asarray(y_pred, dtype=float)
    t = t - np.nanmean(t, axis=1, keepdims=True)
    p = p - np.nanmean(p, axis=1, keepdims=True)
    return float(np.sqrt(np.nanmean((t - p) ** 2)))


def cross_width_sigma(wet: np.ndarray) -> np.ndarray:
    """샘플별 폭방향 표준편차. 합격 판정에는 안 쓰고 제2 지표로 병기한다."""
    return np.nanstd(np.asarray(wet, dtype=float), axis=1)


def lot_group_splits(lots: pd.Series, n_splits: int):
    """lot 단위 분할. 같은 lot 의 행이 train/test 로 쪼개지면 누수다."""
    return GroupKFold(n_splits=n_splits).split(np.zeros(len(lots)), groups=lots)


def time_holdout(frame: pd.DataFrame, at_col: str, frac: float):
    """마지막 frac 비율을 test 로. 실제 배포 시 성능을 보여준다."""
    f = frame.sort_values(at_col)
    cut = int(len(f) * (1 - frac))
    return f.iloc[:cut], f.iloc[cut:]
