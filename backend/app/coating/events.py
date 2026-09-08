"""조정 이벤트 추출. ★순수

값이 계단형이므로 변경 시점 하나하나가 계단 응답 실험의 시작이다.
T분 안에 일어난 변경들은 한 번의 조작으로 보고 하나의 Δgap 벡터로 묶는다.
"""
import numpy as np
import pandas as pd

from app.coating import schemas as S


def _anchor_groups(times: pd.Series, merge_minutes: int) -> np.ndarray:
    """정렬된 시각들에 **앵커** 묶음 번호를 매긴다. ★순수

    앵커 t0 에서 merge_minutes 안이면 같은 묶음이고, 넘으면 그 시각이 새 앵커다.
    직전 값이 아니라 앵커와 재기 때문에 묶음 span 이 merge_minutes 를 넘지 못한다 -
    "한 번의 제어 조작은 merge_minutes 안에 끝난다" 는 가정을 코드가 강제하는 자리다.
    """
    w = pd.Timedelta(minutes=merge_minutes)
    out = np.empty(len(times), dtype=int)
    g, anchor = 0, None
    for i, t in enumerate(times):
        if anchor is None or t - anchor > w:
            g += 1
            anchor = t
        out[i] = g
    return out


def _run_groups(times: pd.Series, merge_minutes: int) -> np.ndarray:
    """정렬된 시각들에 **연쇄** 묶음 번호를 매긴다. ★순수

    직전 변경과의 간격으로 잇는다. 이것이 구 규칙이고, 지금은 배제가 아니라
    "이 조각들이 원래 한 덩어리였다" 를 보이는 용도로만 쓴다(schemas.RUN).
    """
    gap = times.diff()
    return (gap.isna() | (gap > pd.Timedelta(minutes=merge_minutes))).cumsum().to_numpy()


EVENT_COLS = [S.LOT, S.EVENT, S.AT, S.LAST_AT, S.RUN, S.SPAN, "n_items"]


def build_events(
    changes: pd.DataFrame, merge_minutes: int
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """제어 항목의 '변경'을 앵커 기준 merge_minutes 안으로 묶어 이벤트를 만든다.

    시작값(prev_value 가 NaN)은 사람이 바꾼 것이 아니므로 이벤트가 아니다.
    출력(Wet) 변화도 이벤트가 아니다 — 그건 결과다.

    묶음은 **앵커**에서 잰다. 직전 변경에서 재면(구 규칙) 2분씩 이어질 때 묶음이
    무한히 이어붙어, 8분에 걸친 여섯 번의 손질이 "t0 의 한 번의 계단 입력" 이 된다.
    그 상태로 격리를 통과하면 순수 지연 L 이 그 span 만큼 오염된다.
    """
    ctrl = changes[
        changes[S.ITEM].isin(S.CONTROL_ITEM_IDS) & changes[S.PREV_VALUE].notna()
    ].sort_values([S.LOT, S.AT]).copy()

    if ctrl.empty:
        return (
            pd.DataFrame(columns=EVENT_COLS),
            pd.DataFrame(columns=[S.EVENT, S.ITEM, S.ZONE, S.DELTA]),
        )

    parts = []
    for _, g in ctrl.groupby(S.LOT, sort=False):
        g = g.sort_values(S.AT).copy()
        g["_g"] = _anchor_groups(g[S.AT], merge_minutes)
        g["_r"] = _run_groups(g[S.AT], merge_minutes)
        parts.append(g)
    ctrl = pd.concat(parts, ignore_index=True)

    ctrl[S.EVENT] = ctrl[S.LOT] + "#" + ctrl["_g"].astype(int).astype(str)
    ctrl[S.RUN] = ctrl[S.LOT] + "@" + ctrl["_r"].astype(int).astype(str)
    ctrl[S.DELTA] = ctrl[S.VALUE] - ctrl[S.PREV_VALUE]
    ctrl[S.ZONE] = ctrl[S.ITEM].map(_zone_of)

    ev = (
        ctrl.groupby([S.LOT, S.EVENT], as_index=False)
        .agg(**{
            S.AT: (S.AT, "min"),
            S.LAST_AT: (S.AT, "max"),
            # 앵커 묶음은 연쇄 묶음의 세분이므로 한 이벤트는 한 run 안에만 있다.
            S.RUN: (S.RUN, "first"),
            "n_items": (S.ITEM, "nunique"),
        })
        .sort_values([S.LOT, S.AT])
        .reset_index(drop=True)
    )
    ev[S.SPAN] = (ev[S.LAST_AT] - ev[S.AT]) / pd.Timedelta(minutes=1)
    ev = ev[EVENT_COLS]
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


# 간격 분포를 나눌 칸. 세션 안(몇 분)과 세션 사이(시간 단위)가 갈리는 자리를
# 사람이 눈으로 찾을 수 있을 만큼만 잘게 쪼갠다.
_GAP_EDGES = [0, 1, 2, 3, 5, 10, 20, 60, float("inf")]
_GAP_LABELS = ["0~1분", "1~2분", "2~3분", "3~5분", "5~10분",
               "10~20분", "20~60분", "60분+"]


def change_gaps(changes: pd.DataFrame) -> pd.Series:
    """제어값이 바뀐 시각들 사이의 간격(분). lot 안에서만 잰다. ★순수

    build_events 의 merge_minutes 가 무엇이어야 하는지는 이 분포가 답한다.
    한 번의 튜닝 안에서 볼트를 옮겨 잡는 간격과, 튜닝과 튜닝 사이의 간격은
    자릿수가 다를 것이다. 그 사이의 골이 병합창으로 쓸 값이다.
    """
    if changes.empty:
        return pd.Series(dtype="float64")
    ctrl = changes[changes[S.ITEM].isin(S.CONTROL_ITEM_IDS)]
    if S.PREV_VALUE in ctrl.columns:
        ctrl = ctrl[ctrl[S.PREV_VALUE].notna()]
    if ctrl.empty:
        return pd.Series(dtype="float64")
    # 같은 시각에 여러 항목이 바뀐 것은 간격 0 이 아니라 '한 순간' 이다.
    at = ctrl[[S.LOT, S.AT]].drop_duplicates().sort_values([S.LOT, S.AT])
    return at.groupby(S.LOT)[S.AT].diff() / pd.Timedelta(minutes=1)


def gap_histogram(changes: pd.DataFrame) -> pd.DataFrame:
    """간격 분포를 칸으로 나눠 센다. ★순수

    두 봉우리 사이의 빈 칸이 곧 "여기부터는 다른 조정" 이라는 경계다. 골이
    없으면 작업자가 쉼 없이 조정한다는 뜻이고, 그때는 병합창을 어떻게 잡아도
    깨끗한 실험이 안 나온다 - 그것도 결론이다.
    """
    g = change_gaps(changes).dropna()
    cut = pd.cut(g, bins=_GAP_EDGES, labels=_GAP_LABELS, right=False)
    counts = cut.value_counts().reindex(_GAP_LABELS, fill_value=0)
    total = int(counts.sum())
    return pd.DataFrame({
        "bucket": _GAP_LABELS,
        "n": counts.to_numpy(dtype=int),
        "ratio": (counts.to_numpy(dtype=float) / total) if total else 0.0,
    })


def merge_sensitivity(
    changes: pd.DataFrame,
    merge_windows=(1, 2, 3, 5, 10, 15, 20),
    pre_minutes: int = 30,
    post_minutes: int = 60,
    bounds: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """병합창을 바꿔가며 '쓸 수 있는 것' 이 얼마나 되는지. ★순수

    "조정 이벤트 35건" 은 관측이 아니다. merge_minutes=2 가 만든 숫자이고, 그
    2 는 검증된 적이 없다. 창이 좁으면 한 번의 튜닝이 여러 묶음으로 쪼개지는데,
    쪼개진 묶음들은 서로가 서로의 이웃이 되어 **격리에서 다 같이 탈락한다**.
    데이터가 사라지는 것이 아니라 우리가 버리는 것이다.

    세는 것은 이벤트 수가 아니라 **Δgap 항목 수**다. 커널이 먹는 것은 이벤트가
    아니라 (이벤트 × 조정된 zone) 이라, 이벤트 3건으로 쪼개진 것보다 6항목을
    담은 1건이 낫다.
    """
    rows = []
    for mw in merge_windows:
        ev, dl = build_events(changes, mw)
        if ev.empty:
            rows.append({"merge_minutes": mw, "n_clusters": 0,
                         "n_isolated": 0, "n_items": 0})
            continue
        iso = isolation(ev, pre_minutes, post_minutes, bounds)
        keep = set(iso.loc[iso["isolated"], S.EVENT])
        rows.append({
            "merge_minutes": mw,
            "n_clusters": int(len(ev)),
            "n_isolated": int(len(keep)),
            "n_items": int(dl[dl[S.EVENT].isin(keep)].shape[0]),
        })
    return pd.DataFrame(rows)


def _iso_reason(gb, ga, pre, post) -> str | None:
    """무엇이 얼마나 모자랐나. ★순수

    "탈락" 만 적으면 창을 몇으로 바꿔야 살아나는지 알 수 없다. 부족분을 숫자로
    남겨야 그 줄 하나로 다음 설정이 정해진다.
    """
    short_before = pd.notna(gb) and gb < pre
    short_after = pd.notna(ga) and ga < post
    if short_before and short_after:
        return f"앞뒤 {gb:.1f}/{ga:.1f}<{pre}/{post}"
    if short_before:
        return f"앞 {gb:.1f}<{pre}"
    if short_after:
        return f"뒤 {ga:.1f}<{post}"
    return None


def isolation(
    events_df: pd.DataFrame,
    pre_minutes: int,
    post_minutes: int,
    bounds: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """이벤트 시각만으로 '홀로 선 이벤트' 를 가른다. ★순수

    annotate_settling 과 묻는 것이 다르다. 저쪽은 Wet 이 정착했는지를 보고,
    여기는 **다른 조정이 있었는지**를 본다. 이 차이가 결정적이다.

    정착으로 가르면 순환에 빠진다 - 정착 시각을 제대로 잡으려면 순수 지연 L 을
    알아야 하는데, L 을 재려면 깨끗한 이벤트가 먼저 있어야 한다. 반면 "앞뒤로
    다른 조정이 없었다" 는 **Wet 을 한 번도 보지 않고** 판정된다. 그래서 이
    함수의 결과로 L 을 잴 수 있고, 잰 L 로 다시 이 함수의 창을 정할 수 있다.

    출력으로 입력의 청결도를 판정하려던 것이 애초에 뒤집힌 구조였다. 인과의
    원인 쪽은 우리가 직접 관측하는데 결과 쪽에서 추론할 이유가 없다.

    lot 경계도 벽으로 친다(bounds 를 주면). lot 시작 3분 뒤 이벤트는 이웃이
    없어도 기준선을 잴 앞 구간이 없고, 끝 무렵 이벤트는 반응이 끝나기 전에
    lot 이 끝나 최종값을 못 읽는다.

    간격은 **끝**에서 잰다. 시작에서 재면 묶음이 span 을 가질 때 조용한 시간을
    그만큼 길게 잡는다. `last_at` 이 없는 표는 시작 기준으로 돌아가는데, 그것이
    구 규칙을 그대로 재현하는 방법이다(trace.rule_comparison).
    """
    cols = ["gap_before", "gap_after", "isolated", S.ISO_REASON]
    if events_df.empty:
        out = events_df.copy()
        for c in cols:
            out[c] = pd.Series(dtype="bool" if c == "isolated" else (
                "object" if c == S.ISO_REASON else "float64"
            ))
        return out

    out = events_df.sort_values([S.LOT, S.AT]).reset_index(drop=True).copy()
    end = out[S.LAST_AT] if S.LAST_AT in out.columns else out[S.AT]
    prev_end = end.groupby(out[S.LOT]).shift(1)
    next_at = out.groupby(S.LOT)[S.AT].shift(-1)

    if bounds is not None and not bounds.empty:
        b = bounds.set_index(S.LOT)
        # 이웃이 없는 쪽은 lot 경계가 대신 벽이 된다.
        prev_end = prev_end.fillna(out[S.LOT].map(b["start"]))
        next_at = next_at.fillna(out[S.LOT].map(b["end"]))

    minute = pd.Timedelta(minutes=1)
    out["gap_before"] = (out[S.AT] - prev_end) / minute
    out["gap_after"] = (next_at - end) / minute
    # 이웃도 경계도 없으면 제약이 없는 것이다. 없는 이웃을 이유로 버리지 않는다.
    out["isolated"] = (
        out["gap_before"].fillna(np.inf) >= pre_minutes
    ) & (out["gap_after"].fillna(np.inf) >= post_minutes)
    # dtype=object 를 명시한다 - pandas 의 문자열 dtype 추론에 맡기면 None 이
    # NaN 으로 바뀐다. "격리됐다" 를 NaN 으로 표현하면 "값이 없다" 와 구분이 안 된다.
    out[S.ISO_REASON] = pd.Series([
        _iso_reason(gb, ga, pre_minutes, post_minutes)
        for gb, ga in zip(out["gap_before"], out["gap_after"])
    ], index=out.index, dtype=object)
    return out


def isolation_table(
    events_df: pd.DataFrame,
    windows=(10, 20, 30, 45, 60),
    pre_ratio: float = 0.5,
    bounds: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """격리 창을 넓혀가며 몇 건이 살아남는지. ★순수

    창을 얼마로 잡을지는 데이터가 답할 일이다. 넓히면 깨끗해지지만 표본이 줄고,
    좁히면 반대다. 그 곡선을 보여줘야 사람이 고를 수 있고, 사업부에 무엇을
    요구할지도 여기서 나온다 - "60분 격리가 3건뿐" 은 그 자체로 요구서다.

    앞쪽 창은 뒤쪽의 pre_ratio 배로 둔다. 앞은 기준선만 지키면 되고 뒤는 반응
    전체를 담아야 해서, 같은 값을 쓸 이유가 없다(noise_floor 의 가드와 같은 논리).
    """
    rows = []
    n_total = int(len(events_df))
    for w in windows:
        pre = max(1, int(round(w * pre_ratio)))
        iso = isolation(events_df, pre, w, bounds)
        n = int(iso["isolated"].sum()) if len(iso) else 0
        rows.append({
            "post_minutes": w, "pre_minutes": pre,
            "n_events": n_total, "n_isolated": n,
            "ratio": (n / n_total) if n_total else 0.0,
        })
    return pd.DataFrame(rows)
