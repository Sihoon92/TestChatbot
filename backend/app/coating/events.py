"""조정 이벤트 추출. ★순수

값이 계단형이므로 변경 시점 하나하나가 계단 응답 실험의 시작이다.
T분 안에 일어난 변경들은 한 번의 조작으로 보고 하나의 Δgap 벡터로 묶는다.
"""
import pandas as pd

from app.coating import schemas as S


def build_events(
    changes: pd.DataFrame, merge_minutes: int
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """제어 항목의 '변경'을 merge_minutes 이내로 묶어 이벤트를 만든다.

    시작값(prev_value 가 NaN)은 사람이 바꾼 것이 아니므로 이벤트가 아니다.
    출력(Wet) 변화도 이벤트가 아니다 — 그건 결과다.
    """
    ctrl = changes[
        changes[S.ITEM].isin(S.CONTROL_ITEM_IDS) & changes[S.PREV_VALUE].notna()
    ].sort_values([S.LOT, S.AT]).copy()

    if ctrl.empty:
        return (
            pd.DataFrame(columns=[S.LOT, S.EVENT, S.AT, "n_items"]),
            pd.DataFrame(columns=[S.EVENT, S.ITEM, S.ZONE, S.DELTA]),
        )

    gap = ctrl.groupby(S.LOT)[S.AT].diff()
    # 첫 변경이거나 직전 변경과 merge_minutes 를 넘겨 떨어져 있으면 새 이벤트
    new_group = gap.isna() | (gap > pd.Timedelta(minutes=merge_minutes))
    ctrl["_grp"] = new_group.groupby(ctrl[S.LOT]).cumsum()
    ctrl[S.EVENT] = ctrl[S.LOT] + "#" + ctrl["_grp"].astype(int).astype(str)
    ctrl[S.DELTA] = ctrl[S.VALUE] - ctrl[S.PREV_VALUE]
    ctrl[S.ZONE] = ctrl[S.ITEM].map(_zone_of)

    ev = (
        ctrl.groupby([S.LOT, S.EVENT], as_index=False)
        .agg(**{S.AT: (S.AT, "min"), "n_items": (S.ITEM, "nunique")})
        .sort_values([S.LOT, S.AT])
        .reset_index(drop=True)
    )
    dl = ctrl[[S.EVENT, S.ITEM, S.ZONE, S.DELTA]].reset_index(drop=True)
    return ev, dl


def _zone_of(item_id: str) -> float:
    """zone 이 있는 항목만 1..25 를 준다. 스칼라 제어값은 NaN."""
    if item_id in S.GAP_ITEM_IDS:
        return float(S.GAP_ITEM_IDS.index(item_id) + 1)
    return float("nan")
