"""샘플 테이블 생성. ★순수

두 종류를 만든다.
  절대 샘플 — 안정 구간의 (제어 상태, Wet 25). 1샷 모델용.
  델타 샘플 — 쓸 수 있는(격리된) 이벤트의 (Δgap 25, ΔWet 25). 영향행렬과 v2 보정용.

둘의 질문이 다르다. 델타는 "무엇을 바꿨더니 얼마나 변했나" 라 영향행렬이
쓰고, 절대는 "이 상태에서 얼마였나" 라 레벨 모델이 쓴다. 역산이 최종적으로
답해야 하는 것은 후자이므로 둘 다 필요하다.
"""
import numpy as np
import pandas as pd

from app.coating import pivot
from app.coating import schemas as S

GAP_DELTA_COLS = [f"dg{z}" for z in range(1, S.N_ZONES + 1)]
WET_DELTA_COLS = [f"dw{z}" for z in range(1, S.N_ZONES + 1)]


def wet_wide(deduped: pd.DataFrame) -> pd.DataFrame:
    """Wet 25 zone 을 wide 로. 0 은 '미사용/미측정' 이므로 NaN 으로 마스킹한다."""
    w = deduped[deduped[S.ITEM].isin(S.WET_ITEM_IDS)].copy()
    w[S.ZONE] = w[S.ITEM].map(lambda i: S.WET_ITEM_IDS.index(i) + 1)
    w.loc[w[S.VALUE] == 0, S.VALUE] = np.nan
    wide = w.pivot_table(
        index=[S.LOT, S.AT], columns=S.ZONE, values=S.VALUE, aggfunc="last"
    )
    wide = wide.reindex(columns=range(1, S.N_ZONES + 1))
    wide.columns = [S.zone_col(int(z)) for z in wide.columns]
    return wide.reset_index()


def valid_zones(wet: pd.DataFrame) -> list[int]:
    """한 번이라도 값이 관측된 zone. 전부 NaN 인 zone 은 유효 폭 밖이다."""
    return [z for z in range(1, S.N_ZONES + 1) if wet[S.zone_col(z)].notna().any()]


def wet_mean_series(wet: pd.DataFrame, valid: list[int]) -> pd.DataFrame:
    cols = [S.zone_col(z) for z in valid]
    out = wet[[S.LOT, S.AT]].copy()
    out[S.WET_MEAN] = wet[cols].mean(axis=1, skipna=True)
    return out


def delta_samples(
    events_df: pd.DataFrame,
    event_deltas: pd.DataFrame,
    wet: pd.DataFrame,
    valid: list[int],
    post_minutes: int,
    delta_window_minutes: int,
) -> pd.DataFrame:
    """쓸 수 있는 이벤트마다 (Δgap 25, ΔWet 25) 한 행.

    ΔWet 은 `mean[t1+post-w, t1+post] − mean[t0-w, t0)` 이다.
    t0 = 묶음의 첫 변경, t1 = 마지막 변경, w = delta_window_minutes.
    한 시점끼리 빼면 측정 노이즈가 그대로 신호에 섞인다.

    **선별은 하지 않는다.** 호출자가 격리로 거른 표를 준다. 여기서 정착 판정
    (contaminated)을 다시 보면 격리로 고른 뜻이 사라지고, 정착은 L 을 알아야
    서는데 L 을 그 뒤에서 재므로 순환이 돌아온다.
    """
    zone_cols = [S.zone_col(z) for z in valid]
    win = pd.Timedelta(minutes=delta_window_minutes)
    post = pd.Timedelta(minutes=post_minutes)

    rows = []
    for _, e in events_df.iterrows():
        t0 = e[S.AT]
        t1 = e[S.LAST_AT] if S.LAST_AT in events_df.columns else t0
        before = _window_mean(wet, e[S.LOT], t0 - win, t0, zone_cols, end_open=True)
        after = _window_mean(
            wet, e[S.LOT], t1 + post - win, t1 + post, zone_cols
        )
        if before is None or after is None:
            continue
        row = {S.EVENT: e[S.EVENT], S.LOT: e[S.LOT], S.AT: t0}
        for z in range(1, S.N_ZONES + 1):
            col = S.zone_col(z)
            row[WET_DELTA_COLS[z - 1]] = (
                after.get(col, np.nan) - before.get(col, np.nan)
            )
            row[GAP_DELTA_COLS[z - 1]] = 0.0
        rows.append(row)

    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(
            columns=[S.EVENT, S.LOT, S.AT] + GAP_DELTA_COLS + WET_DELTA_COLS
        )

    # Δgap 채우기 — zone 이 있는 항목만. 스칼라 제어값은 레벨 모델이 쓴다.
    #
    # (event, zone) 으로 먼저 **합친다**. 한 이벤트 안에서 같은 zone 을 두 번
    # 만지면 행이 두 개 오는데, 아래 루프는 같은 칸에 쓰므로 합치지 않으면
    # 마지막 것만 남는다. ΔWet 은 그 두 번의 결과를 다 담고 있으므로 Δgap 만
    # 작아지고, 그 비율만큼 게인이 부풀려진다.
    # min_count=1: 기본 skipna 합은 전부 NaN 인 그룹도 0.0 을 낸다. 0.0 은
    # "이 zone 을 만졌는데 변화가 없었다" 로 설계행렬에 들어가는데, 전부 NaN 은
    # "델타 자체를 못 읽었다" 는 뜻이라 다른 사실이다(parse.py 가 to_numeric
    # errors="coerce" 로 NaN 델타를 남길 수 있다). min_count=1 이면 그 그룹은
    # NaN 으로 남고, fit_kernel 의 isfinite 마스크가 그 행을 제외한다.
    zoned = (
        event_deltas[event_deltas[S.ZONE].notna()]
        .groupby([S.EVENT, S.ZONE], as_index=False)[S.DELTA]
        .sum(min_count=1)
    )
    out = out.set_index(S.EVENT)
    for _, d in zoned.iterrows():
        if d[S.EVENT] in out.index:
            out.loc[d[S.EVENT], GAP_DELTA_COLS[int(d[S.ZONE]) - 1]] = d[S.DELTA]
    return out.reset_index()


def _window_mean(wet, lot_id, start, end, zone_cols, end_open: bool = False):
    """[start, end] 구간의 zone 평균. end_open 이면 end 를 제외한다.

    기준선 창은 조정이 일어난 분 자체를 포함하면 안 된다 - 그 분의 값에는 이미
    조정의 영향이 섞여 있을 수 있다.
    """
    upper = (wet[S.AT] < end) if end_open else (wet[S.AT] <= end)
    g = wet[(wet[S.LOT] == lot_id) & (wet[S.AT] >= start) & upper]
    if g.empty:
        return None
    return g[zone_cols].mean(axis=0, skipna=True).to_dict()


def _scalar_state(changes: pd.DataFrame, lot_id, at) -> dict:
    """그 시점의 스칼라 제어값 4종. 아직 관측 안 된 항목은 NaN 으로 남는다."""
    state = pivot.state_at(changes, lot_id, at)
    return {
        name: state.get(item, np.nan)
        for item, name in S.CONTROL_SCALARS.items()
    }


def _control_change_times(changes: pd.DataFrame, lot_id) -> list:
    """그 lot 에서 제어값이 **바뀐** 시각. 시작값(prev_value NaN)은 변경이 아니다."""
    c = changes[(changes[S.LOT] == lot_id) & changes[S.ITEM].isin(S.CONTROL_ITEM_IDS)]
    if S.PREV_VALUE in c.columns:
        c = c[c[S.PREV_VALUE].notna()]
    return sorted(c[S.AT].unique())


def absolute_samples(
    changes: pd.DataFrame,
    wet_mean: pd.DataFrame,
    bounds: pd.DataFrame,
    wait_minutes: int,
    min_window_minutes: int,
) -> pd.DataFrame:
    """안정 구간마다 (제어 상태, Wet 평균) 한 행. ★순수

    레벨 모델의 학습 데이터다. delta_samples 가 "무엇을 바꿨더니 얼마나
    변했나" 를 담는다면 여기는 "이 상태에서 얼마였나" 를 담는다. 역산이
    최종적으로 답해야 하는 것이 후자라, 둘 다 필요하다.

    구간은 제어값 변경 사이다. 변경 직후는 아직 반영 전이므로 wait_minutes 를
    기다린 뒤부터 끝까지를 평균 낸다 - 조정 순간의 Wet 은 그 조정의 결과가
    아니라 직전 조정의 결과다. 기다리고 남는 창이 min_window_minutes 보다
    짧으면 버린다. 반영이 끝났다고 볼 근거가 없는 구간이다.

    Wet 이 통째로 없는 구간도 버린다. 0 으로 채우면 레벨이 왜곡된다
    (wet_wide 가 이미 0 을 NaN 으로 마스킹하는 것과 같은 이유).
    """
    cols = [S.LOT, S.AT, S.PRODUCT] + S.SCALAR_COLS + [S.WET_MEAN]
    if changes.empty or wet_mean.empty or bounds.empty:
        return pd.DataFrame(columns=cols)

    wait = pd.Timedelta(minutes=wait_minutes)
    least = pd.Timedelta(minutes=min_window_minutes)
    rows = []
    for b in bounds.itertuples(index=False):
        lot = getattr(b, S.LOT)
        edges = [b.start] + _control_change_times(changes, lot) + [b.end]
        for a, z in zip(edges[:-1], edges[1:]):
            settled = pd.Timestamp(a) + wait
            if pd.Timestamp(z) - settled < least:
                continue
            w = wet_mean[
                (wet_mean[S.LOT] == lot)
                & (wet_mean[S.AT] >= settled)
                & (wet_mean[S.AT] <= z)
            ]
            level = float(w[S.WET_MEAN].mean(skipna=True)) if len(w) else np.nan
            if not np.isfinite(level):
                continue
            rows.append({
                S.LOT: lot, S.AT: settled,
                S.PRODUCT: getattr(b, S.PRODUCT, None),
                **_scalar_state(changes, lot, settled),
                S.WET_MEAN: level,
            })
    return pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)


def lot_finals(changes: pd.DataFrame, bounds: pd.DataFrame) -> pd.DataFrame:
    """lot 마다 마지막 제어 상태 한 행. ★순수

    베이스라인 2종의 입력이다. "그 lot 이 최종적으로 안착한 조건" 이 곧
    작업자가 도달한 답이고, 다음 lot 에서 그것을 그대로 쓰는 것이 가장
    단순한 대안이다. 모델이 이것을 못 이기면 표준 조건표가 정답이다.

    시각은 lot 의 끝이다. 베이스라인이 과거만 보게 하려면 lot 사이의 순서가
    필요한데, 그 순서를 정하는 것이 이 열이다.
    """
    cols = [S.LOT, S.AT, S.PRODUCT] + S.SCALAR_COLS
    if changes.empty or bounds.empty:
        return pd.DataFrame(columns=cols)
    rows = [
        {
            S.LOT: getattr(b, S.LOT),
            S.AT: pd.Timestamp(b.end),
            S.PRODUCT: getattr(b, S.PRODUCT, None),
            **_scalar_state(changes, getattr(b, S.LOT), pd.Timestamp(b.end)),
        }
        for b in bounds.itertuples(index=False)
    ]
    return (
        pd.DataFrame(rows, columns=cols)
        .sort_values(S.AT, kind="mergesort")
        .reset_index(drop=True)
    )
