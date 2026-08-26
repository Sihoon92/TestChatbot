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


def annotate_settling(
    events_df: pd.DataFrame,
    wet_mean: pd.DataFrame,
    std_max: float,
    window_minutes: int,
    max_wait_minutes: int,
) -> pd.DataFrame:
    """이벤트별 정착 시각을 찾고, 인과 분리가 불가능한 이벤트를 표시한다.

    오염되는 두 경우:
      overlapped — 재안정 전에 다음 조정이 왔다. 두 변경의 효과가 겹쳐
                   어느 쪽이 Wet 을 움직였는지 알 수 없다.
      no_settle  — max_wait 안에 정착을 못 찾았다. 최종 gain 을 못 읽는다.

    둘 다 버리되 이유를 남긴다. 배제 비율 자체가 현장 진단이다
    (높으면 튜닝이 급하게 이뤄지고 있다는 뜻).
    """
    out = events_df.sort_values([S.LOT, S.AT]).copy()
    next_at = out.groupby(S.LOT)[S.AT].shift(-1)

    settled, contaminated, reason = [], [], []
    for (_, row), nxt in zip(out.iterrows(), next_at):
        s = _settle_time(
            wet_mean, row[S.LOT], row[S.AT], std_max, window_minutes, max_wait_minutes
        )
        if s is None:
            settled.append(pd.NaT)
            contaminated.append(True)
            reason.append("no_settle")
        elif pd.notna(nxt) and nxt < s:
            settled.append(s)
            contaminated.append(True)
            reason.append("overlapped")
        else:
            settled.append(s)
            contaminated.append(False)
            reason.append(None)

    out[S.SETTLED_AT] = settled
    out[S.CONTAMINATED] = contaminated
    out[S.DROP_REASON] = reason
    return out.reset_index(drop=True)


def _settle_time(
    wet_mean: pd.DataFrame,
    lot_id: str,
    after,
    std_max: float,
    window_minutes: int,
    max_wait_minutes: int,
):
    """`after` 이후 이동창 표준편차가 std_max 아래로 처음 내려간 시각."""
    deadline = after + pd.Timedelta(minutes=max_wait_minutes)
    g = wet_mean[
        (wet_mean[S.LOT] == lot_id)
        & (wet_mean[S.AT] > after)
        & (wet_mean[S.AT] <= deadline)
    ].sort_values(S.AT)
    if g.empty:
        return None
    vals = g[S.WET_MEAN].to_numpy()
    times = g[S.AT].to_numpy()
    # window_minutes 는 분 단위 관측 개수와 같다고 본다(원본이 분 해상도).
    w = max(2, window_minutes)
    for i in range(w - 1, len(vals)):
        if vals[i - w + 1 : i + 1].std(ddof=0) <= std_max:
            return pd.Timestamp(times[i])
    return None
