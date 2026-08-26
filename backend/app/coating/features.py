"""샘플 테이블 생성. ★순수

두 종류를 만든다.
  절대 샘플 — 안정 구간의 (제어 상태, Wet 25). 1샷 모델용.
  델타 샘플 — 깨끗한 이벤트의 (Δgap 25, ΔWet 25). 영향행렬과 v2 보정용.

v1 은 델타 샘플만 모델에 쓰지만 절대 샘플도 지금 만들어 둔다. 나중에
파이프라인을 다시 헤집는 비용이 훨씬 크다.
"""
import numpy as np
import pandas as pd

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
    window_minutes: int,
) -> pd.DataFrame:
    """깨끗한 이벤트마다 (Δgap 25, ΔWet 25) 한 행.

    ΔWet 은 '조정 직전 안정 구간 평균' 대비 '정착 후 구간 평균' 이다.
    한 시점끼리 빼면 측정 노이즈가 그대로 신호에 섞인다.
    """
    clean = events_df[~events_df[S.CONTAMINATED].astype(bool)]
    zone_cols = [S.zone_col(z) for z in valid]
    win = pd.Timedelta(minutes=window_minutes)

    rows = []
    for _, e in clean.iterrows():
        before = _window_mean(wet, e[S.LOT], e[S.AT] - win, e[S.AT], zone_cols)
        after = _window_mean(
            wet, e[S.LOT], e[S.SETTLED_AT], e[S.SETTLED_AT] + win, zone_cols
        )
        if before is None or after is None:
            continue
        row = {S.EVENT: e[S.EVENT], S.LOT: e[S.LOT], S.AT: e[S.AT]}
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
    zoned = event_deltas[event_deltas[S.ZONE].notna()]
    out = out.set_index(S.EVENT)
    for _, d in zoned.iterrows():
        if d[S.EVENT] in out.index:
            out.loc[d[S.EVENT], GAP_DELTA_COLS[int(d[S.ZONE]) - 1]] = d[S.DELTA]
    return out.reset_index()


def _window_mean(wet, lot_id, start, end, zone_cols):
    g = wet[(wet[S.LOT] == lot_id) & (wet[S.AT] >= start) & (wet[S.AT] <= end)]
    if g.empty:
        return None
    return g[zone_cols].mean(axis=0, skipna=True).to_dict()
