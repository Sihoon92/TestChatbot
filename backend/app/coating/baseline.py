"""나이브 베이스라인 2종.

모델이 이 둘을 못 이기면 이 과제의 정답은 모델이 아니라 '제품별 최종조건
표준 테이블' 이다. 그것도 유효한 결론이고 훨씬 싸게 로스를 줄인다.
이 베이스라인 없이 모델 MAE 만 보고하면 '좋아 보이는데 실제로는 아무것도
개선 못 하는' 결과를 알아채지 못한다.

둘 다 **과거만** 본다. 미래 lot 이 과거 예측에 들어가면 베이스라인이
부당하게 좋아지거나 나빠져서 비교 자체가 무의미해진다.
"""
import pandas as pd

LOT = "lot_id"
PRODUCT = "product"
AT = "worked_at"


def prev_lot_baseline(finals: pd.DataFrame, control_cols: list[str]) -> pd.DataFrame:
    """직전 동일 제품 lot 의 최종 조건을 그대로 쓴다."""
    f = finals.sort_values([PRODUCT, AT]).copy()
    shifted = f.groupby(PRODUCT)[control_cols].shift(1)
    out = pd.concat([f[[LOT]], shifted], axis=1)
    return out.dropna(subset=control_cols).reset_index(drop=True)


def median_baseline(finals: pd.DataFrame, control_cols: list[str]) -> pd.DataFrame:
    """동일 제품 과거 lot 최종 조건의 중앙값. expanding 이라 미래를 안 본다."""
    f = finals.sort_values([PRODUCT, AT]).copy()
    med = (
        f.groupby(PRODUCT)[control_cols]
        .apply(lambda g: g.shift(1).expanding().median())
        .reset_index(level=0, drop=True)
    )
    out = pd.concat([f[[LOT]], med], axis=1)
    return out.dropna(subset=control_cols).reset_index(drop=True)
