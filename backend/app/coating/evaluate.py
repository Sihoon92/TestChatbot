"""분할과 지표.

'예측 정확도' 와 '제안 품질' 을 분리해서 잰다. 전자는 surrogate 가 Wet 을
맞히는가, 후자는 그 surrogate 위에서 뽑은 제안이 스펙에 드는가다.
둘을 하나의 점수로 뭉치면 어느 쪽이 문제인지 영영 알 수 없다.
"""
import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold

from app.coating import schemas as S
from app.coating.model import level


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


def walk_forward_lots(samples: pd.DataFrame, min_train_lots: int) -> list:
    """lot 을 시간순으로 세워 하나씩 예측한다. ★순수

    GroupKFold 가 아니라 walk-forward 인 이유: 이 과제의 배포 상황이 그렇다.
    다음 lot 의 초기조건을 정할 때 쓸 수 있는 것은 지난 lot 들뿐이다. 무작위
    분할은 미래 lot 으로 과거를 예측하는 폴드를 만들어 성능을 부풀린다.

    "직전 lot 을 그대로 쓴다" 는 베이스라인도 시간 순서가 있어야 정의된다.
    """
    order = (
        samples.groupby(S.LOT)[S.AT].min().sort_values().index.tolist()
        if len(samples) else []
    )
    return [
        (order[:i], order[i])
        for i in range(len(order))
        if i >= min_train_lots
    ]


def compare_level_models(
    samples: pd.DataFrame, alpha: float, min_train_lots: int = 5
) -> dict:
    """레벨 모델이 나이브 예측을 이기는가.

    이 단계의 산출물은 "정확한 모델" 이 아니라 **"모델이 필요한가" 에 대한
    답**이다. 못 이기면 정답은 제품별 표준 조건표이고, 그것도 유효한 결론이며
    훨씬 싸게 로스를 줄인다(baseline.py 참고).

    견주는 상대 둘.
        전역 중앙값   과거 전체의 중앙값. 제어값을 아예 안 본다
        직전 lot      바로 앞 lot 의 평균 두께. 작업자가 실제로 하는 일에 가깝다

    둘 다 과거만 본다. 미래 lot 이 들어가면 베이스라인이 부당하게 좋아지거나
    나빠져서 비교 자체가 무의미해진다.
    """
    out = {
        "n_samples": int(len(samples)),
        "n_lots": int(samples[S.LOT].nunique()) if len(samples) else 0,
        "n_eval_lots": 0, "features": [], "varying_features": [],
        "mae_model": None, "mae_global": None, "mae_prev_lot": None,
        "coefficients": {}, "verdict": "insufficient",
        "reason": "절대 샘플이 없다.",
    }
    folds = walk_forward_lots(samples, min_train_lots)
    if not folds:
        out["reason"] = (
            f"lot 이 {out['n_lots']}개다. 앞의 {min_train_lots}개는 훈련에만 쓰므로 "
            "평가할 lot 이 남지 않는다. 판정하지 않는다 - 여기서 숫자를 내면 "
            "표본 몇 건짜리 평균을 사람이 그대로 믿는다."
        )
        return out

    feats = level.available_features(samples)
    # "값이 있다" 와 "값이 변한다" 는 다르다. lot 마다 RPM 이 늘 같으면 그
    # 열은 두께 차이를 설명할 수 없는데, available_features 는 그것을 남긴다
    # (모델에 넣어도 해롭진 않다 - 스케일러가 0 으로 만든다). 다만 리포트가
    # 그것을 '쓴 피처' 로 적으면 읽는 사람이 설명력을 오해한다.
    varying = [c for c in feats if samples[c].nunique(dropna=True) > 1]
    out["features"], out["varying_features"] = feats, varying
    if not varying:
        out["reason"] = (
            f"제어 피처 {feats} 가 전부 상수다. 값이 안 변하면 두께 차이를 "
            "설명할 수 없다 - 조건이 다른 구간의 데이터가 필요하다."
        )
        return out

    err_model, err_global, err_prev = [], [], []
    for train_lots, test_lot in folds:
        tr = samples[samples[S.LOT].isin(train_lots)]
        te = samples[samples[S.LOT] == test_lot]
        y = te[S.WET_MEAN].to_numpy(dtype=float)

        model, used = level.fit_level(tr, alpha)
        err_model += list(np.abs(y - model.predict(te[used + [S.PRODUCT]])))
        err_global += list(np.abs(y - float(tr[S.WET_MEAN].median())))
        prev = tr[tr[S.LOT] == train_lots[-1]][S.WET_MEAN].mean()
        err_prev += list(np.abs(y - float(prev)))

    out.update(
        n_eval_lots=len(folds),
        mae_model=float(np.nanmean(err_model)),
        mae_global=float(np.nanmean(err_global)),
        mae_prev_lot=float(np.nanmean(err_prev)),
        coefficients=level.coefficients(*level.fit_level(samples, alpha)),
    )
    out.update(_level_verdict(out))
    return out


def _level_verdict(f: dict) -> dict:
    """이긴다 / 못 이긴다. 둘의 다음 행동이 다르므로 문장도 다르다."""
    best_naive = min(f["mae_global"], f["mae_prev_lot"])
    who = "전역 중앙값" if f["mae_global"] <= f["mae_prev_lot"] else "직전 lot"
    if f["mae_model"] < best_naive:
        gain = 1 - f["mae_model"] / best_naive if best_naive else 0.0
        return {
            "verdict": "model_helps",
            "reason": (
                f"모델 MAE {f['mae_model']:.4f} < 최선의 나이브({who}) "
                f"{best_naive:.4f} — {gain:.0%} 개선. 제어값이 두께를 설명한다."
            ),
        }
    return {
        "verdict": "standard_table",
        "reason": (
            f"모델 MAE {f['mae_model']:.4f} 가 최선의 나이브({who}) "
            f"{best_naive:.4f} 를 못 이긴다. 이 데이터에서 정답은 모델이 아니라 "
            "**제품별 표준 조건표**다 - 훨씬 싸고 현장에서 바로 쓴다."
        ),
    }
