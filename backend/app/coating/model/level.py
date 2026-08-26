"""레벨 모델 — zone 평균 Wet 을 예측한다.

릿지를 베이스라인으로 둔다. 이 문제는 물리적으로 단조롭고(토출량↑ ⇒ 로딩↑)
변수가 몇 개 안 된다. 트리 앙상블을 먼저 쓰면 구간별로 계단처럼 튀는 예측이
나오고, 그게 역산 최적화에서 이상한 해를 만든다. HistGradientBoosting 이
유의미하게 못 이기면 릿지를 채택한다(비교는 evaluate 모듈이 한다).
"""
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

LEVEL_FEATURES = ["bp_open_rate", "pump_rpm", "os_gap", "ds_gap"]
PRODUCT_COL = "product"
TARGET_COL = "wet_mean"


def available_features(df: pd.DataFrame) -> list[str]:
    """실제로 값이 있는 피처만 남긴다.

    RPM·BP open rate 가 로깅되지 않는 경우가 설계상 가장 위험한 가정이라,
    그때도 파이프라인이 죽지 않고 '남은 변수로 최선' 을 하도록 만든다.
    빠진 피처는 리포트에 반드시 명시한다.
    """
    return [c for c in LEVEL_FEATURES if c in df.columns and df[c].notna().any()]


def build_level_model(alpha: float, numeric: list[str]) -> Pipeline:
    pre = ColumnTransformer([
        ("num", make_pipeline(SimpleImputer(strategy="median"), StandardScaler()), numeric),
        ("prod", OneHotEncoder(handle_unknown="ignore"), [PRODUCT_COL]),
    ])
    return Pipeline([("pre", pre), ("ridge", Ridge(alpha=alpha))])


def fit_level(df: pd.DataFrame, alpha: float) -> tuple[Pipeline, list[str]]:
    feats = available_features(df)
    model = build_level_model(alpha, feats)
    model.fit(df[feats + [PRODUCT_COL]], df[TARGET_COL])
    return model, feats
