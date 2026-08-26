"""lot 경계와 튜닝 종료 시점. ★순수

튜닝 종료를 두 정의로 계산한다.

- `tuning_end_last_change`: 제어값을 마지막으로 만진 시각 = 사람이 손 뗀 시점.
  객관적이고 계산이 명확해 1차 기준으로 쓴다.
- `tuning_end_band`: Wet 이 목표±band 에 연속으로 들어온 시각. 교차검증용이다.

둘이 크게 어긋나면 그 자체가 발견이다 — 스펙보다 엄한 암묵적 기준이
존재한다는 증거이고, 그게 진짜 목표값이다.
"""
import pandas as pd

from app.coating import schemas as S


def lot_bounds(deduped: pd.DataFrame) -> pd.DataFrame:
    g = deduped.groupby(S.LOT)
    out = pd.DataFrame({
        "start": g[S.AT].min(),
        "end": g[S.AT].max(),
    }).reset_index()
    if S.PRODUCT in deduped.columns:
        products = g[S.PRODUCT].first().reset_index()
        out = out.merge(products, on=S.LOT, how="left")
    return out


def tuning_end_last_change(changes: pd.DataFrame) -> pd.DataFrame:
    """제어 항목의 마지막 '변경' 시각. 시작값(prev_value 가 NaN)은 변경이 아니다."""
    ctrl = changes[changes[S.ITEM].isin(S.CONTROL_ITEM_IDS)]
    if S.PREV_VALUE in ctrl.columns:
        ctrl = ctrl[ctrl[S.PREV_VALUE].notna()]
    else:
        # prev_value 열이 없는 축약 입력: lot·item 별 첫 행을 시작값으로 본다
        ctrl = ctrl.sort_values(S.AT)
        ctrl = ctrl[ctrl.duplicated([S.LOT, S.ITEM], keep="first")]
    lots = changes[[S.LOT]].drop_duplicates()
    ends = ctrl.groupby(S.LOT)[S.AT].max().rename("tuning_end").reset_index()
    return lots.merge(ends, on=S.LOT, how="left")


def tuning_end_band(
    wet_mean: pd.DataFrame, target: float, band: float, min_consecutive: int
) -> pd.DataFrame:
    """Wet 평균이 목표±band 안에 `min_consecutive` 회 연속 들어온 구간의 **시작** 시각.

    streak 이 확정된 시각이 아니라 진입 시각을 돌려준다. 정착한 순간은
    연속이 확인되기 min_consecutive-1 관측 전이고, 튜닝이 끝난 시점으로
    쓰려면 그 진입 시각이어야 한다.
    """
    rows = []
    for lot, g in wet_mean.sort_values([S.LOT, S.AT]).groupby(S.LOT):
        inside = (g[S.WET_MEAN] - target).abs() <= band
        streak = 0
        streak_start = pd.NaT
        entry = pd.NaT
        for at, ok in zip(g[S.AT], inside):
            if ok:
                streak += 1
                if streak == 1:
                    streak_start = at
            else:
                streak = 0
                streak_start = pd.NaT
            if streak >= min_consecutive:
                entry = streak_start
                break
        rows.append({S.LOT: lot, "band_entry": entry})
    return pd.DataFrame(rows)
