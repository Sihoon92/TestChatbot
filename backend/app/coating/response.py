"""이벤트 응답과 동특성 통계. ★순수 — 파일·설정을 만지지 않는다.

답하려는 질문: "t 시점에 gap 을 Δ 만큼 바꿨을 때, Wet 은 언제부터 얼마나 움직이나."

이 모듈의 첫 산출물은 숫자가 아니라 **판정**이다. 지연을 말할 수 있으려면 노이즈
대비 반응이 충분히 커야 하고 이벤트가 충분히 많아야 하는데, 둘 다 데이터가 정한다.
그래서 순서가 이렇다.

    1. σ 를 잰다                    조정 없는 구간만 있으면 된다. 이벤트 0건이어도 나온다
    2. 필요한 이벤트 수 n* 를 구한다   σ 와 관측된 반응 크기로
    3. 실제 n 과 비교해 판정한다
    4. 되면 L·τ·T_s 를 내고, 안 되면 "몇 건 더" 를 낸다

report.py 가 세워둔 원칙("모델이 얼마나 정확한가보다 이 데이터로 학습이 가능한가가
먼저다")의 시간 축 판이다. 숫자를 먼저 내면 표본이 3건인 평균을 사람이 믿어버린다.

순수 지연은 물리량이다 — gap 을 바꾼 지점이 측정기까지 이송되는 시간이라 zone 마다
다를 이유가 없다. 그래서 zone 을 따로 추정하지 않고 전부 합쳐 하나의 L 을 본다.
표본이 배로 늘어 소수 이벤트로도 식별될 수 있다(profile.build_design 과 같은 수법).
"""
import numpy as np
import pandas as pd

from app.coating import schemas as S

LAG = "lag_min"
RESPONSE = "response"      # 부호 정렬된 ΔWet(절대 단위)
D_GAP = "d_gap"

# 반응이 "시작됐다" 고 볼 기준. 평균의 표준오차 배수다 — 한 관측의 σ 가 아니라
# 평균을 검정하는 것이므로 SE 를 쓴다.
_SIGMA_K = 2.0

# 임계를 **연속으로** 몇 lag 넘어야 반응으로 볼 것인가.
#
# 이게 없으면 추정기가 반드시 틀린다. lag 을 70개 넘게 훑으면서 각각 2σ 검정을
# 하면 우연한 초과가 3~4번 나오는 것이 정상이고(다중비교), '첫 초과'를 집으면
# 그 노이즈를 집는다. 실제로 지연 8분을 심은 데이터에서 lag 2 의 단발 초과를
# 물어 L=2 로 답했다.
#
# 물리적으로도 이쪽이 맞다. 계단 응답은 한 번 올라가면 내려오지 않는다 - 넘었다
# 말았다 하는 것은 반응이 아니다.
_MIN_RUN = 3

# 평균의 표준오차를 낼 수 있는 최소 표본. 1개면 sem 이 NaN 이라 검정 자체가 안 된다.
_MIN_PAIRS = 3
# 1차 지연계에서 최종값의 63.2% 도달 시점이 시정수 τ 다.
_TAU_FRACTION = 0.632
# 정착 판정 밴드(최종값 대비).
_SETTLE_BAND = 0.05


def align_events(
    panel: pd.DataFrame,
    events: pd.DataFrame,
    event_deltas: pd.DataFrame,
    pre: int,
    post: int,
    baseline_minutes: int,
) -> pd.DataFrame:
    """깨끗한 이벤트를 lag=0 으로 정렬한 long 표.

    반환: event_id · lot_id · lag_min · zone · d_gap · response

    **실제로 조정된 zone 만** 담는다. 나머지 zone 의 움직임은 이웃에서 넘어온
    결합 효과라 원인 시각이 같지 않고, 그걸 섞으면 지연이 흐려진다(결합의 크기는
    이미 profile.fit_kernel 의 Toeplitz 커널이 맡는다).

    response 는 **부호 정렬**한다(ΔWet × sign(Δgap)). 안 하면 올린 이벤트와 내린
    이벤트가 평균에서 상쇄돼 반응이 없는 것처럼 보인다.

    기준선은 한 시점이 아니라 직전 구간의 평균이다. 한 점과 빼면 그 점의 측정
    노이즈가 모든 lag 에 그대로 실린다(features.delta_samples 와 같은 이유).
    """
    empty = pd.DataFrame(columns=[S.EVENT, S.LOT, LAG, S.ZONE, D_GAP, RESPONSE])
    if panel.empty or events.empty:
        return empty

    clean = events[~events[S.CONTAMINATED].astype(bool)] if S.CONTAMINATED in events else events
    zoned = event_deltas[event_deltas[S.ZONE].notna()]
    if clean.empty or zoned.empty:
        return empty

    by_lot = {lot: g.set_index(S.AT) for lot, g in panel.groupby(S.LOT)}
    rows = []
    for _, e in clean.iterrows():
        g = by_lot.get(e[S.LOT])
        if g is None:
            continue
        t0 = e[S.AT]
        base = g.loc[
            (g.index >= t0 - pd.Timedelta(minutes=baseline_minutes)) & (g.index < t0),
            S.ZONE_COLS,
        ].mean()
        if base.isna().all():
            continue

        win = g.loc[
            (g.index >= t0 - pd.Timedelta(minutes=pre))
            & (g.index <= t0 + pd.Timedelta(minutes=post)),
            S.ZONE_COLS,
        ]
        rel = (win - base).reset_index()
        rel[LAG] = ((rel[S.AT] - t0).dt.total_seconds() // 60).astype(int)

        long = rel.melt(id_vars=[LAG], value_vars=S.ZONE_COLS,
                        var_name="_col", value_name="_rel")
        long[S.ZONE] = long["_col"].str.removeprefix("z").astype(int)

        deltas = zoned[zoned[S.EVENT] == e[S.EVENT]][[S.ZONE, S.DELTA]]
        deltas = deltas[deltas[S.DELTA] != 0]
        if deltas.empty:
            continue
        deltas = deltas.astype({S.ZONE: int})

        m = long.merge(deltas, on=S.ZONE, how="inner")
        m[S.EVENT], m[S.LOT] = e[S.EVENT], e[S.LOT]
        m[D_GAP] = m[S.DELTA]
        m[RESPONSE] = m["_rel"] * np.sign(m[S.DELTA])
        rows.append(m[[S.EVENT, S.LOT, LAG, S.ZONE, D_GAP, RESPONSE]])

    return pd.concat(rows, ignore_index=True) if rows else empty


def noise_floor(
    delta: pd.DataFrame, events: pd.DataFrame, guard_minutes: int
) -> tuple[float, int]:
    """조정 근처를 뺀 구간의 분당 ΔWet 표준편차. (σ, 쓴 분 수)

    **모든** 이벤트 근처를 뺀다 — 오염이든 아니든 조정은 Wet 을 흔든다. 안 빼면
    σ 가 반응을 포함해 부풀고, 그러면 진짜 반응이 노이즈 아래로 숨어 "지연 없음"
    으로 잘못 판정한다.
    """
    if delta.empty:
        return float("nan"), 0
    quiet = delta
    if events is not None and not events.empty:
        guard = pd.Timedelta(minutes=guard_minutes)
        mask = pd.Series(True, index=delta.index)
        for _, e in events.iterrows():
            near = (
                (delta[S.LOT] == e[S.LOT])
                & (delta[S.AT] >= e[S.AT] - guard)
                & (delta[S.AT] <= e[S.AT] + guard)
            )
            mask &= ~near
        quiet = delta[mask]

    vals = quiet[S.ZONE_COLS].to_numpy(dtype=float).ravel()
    vals = vals[np.isfinite(vals)]
    if vals.size < 2:
        return float("nan"), 0
    minutes = int(quiet[S.ZONE_COLS].notna().any(axis=1).sum())
    return float(vals.std(ddof=1)), minutes


def required_pairs(sigma: float, response_size: float, tau_minutes: float) -> int:
    """lag 를 1분 단위로 가르는 데 필요한 (이벤트×zone) 표본 수.

    상승 구간의 분당 증가분은 대략 response_size/τ 다. 이웃한 두 lag 의 평균을
    가르려면 그 증가분이 두 평균 차의 오차(σ√2/√m)의 _SIGMA_K 배를 넘어야 한다:

        response_size/τ > k·√2·σ/√m   =>   m > (k·√2·σ·τ / response_size)²

    1차 지연계와 lag 간 독립을 가정한 어림이다. zone 은 서로 완전히 독립이 아니라
    (Toeplitz 결합) 실제 유효 표본은 이보다 작다 — 그래서 이 값은 **하한**이다.
    """
    if not np.isfinite(sigma) or not np.isfinite(response_size) or response_size == 0:
        return -1
    if sigma <= 0:
        return 1
    m = (_SIGMA_K * np.sqrt(2) * sigma * tau_minutes / abs(response_size)) ** 2
    return int(np.ceil(m))


def response_curve(aligned: pd.DataFrame) -> pd.DataFrame:
    """lag 별 평균 응답과 표준오차. 표본 수를 함께 낸다.

    n 을 같이 내는 이유: 몇 건으로 하는 말인지가 숫자 옆에 없으면, 표본 3건짜리
    평균을 사람이 그대로 믿는다.
    """
    if aligned.empty:
        return pd.DataFrame(columns=[LAG, "mean", "sem", "n"])
    g = aligned.groupby(LAG)[RESPONSE]
    out = pd.DataFrame({"mean": g.mean(), "sem": g.sem(), "n": g.count()})
    return out.reset_index().sort_values(LAG).reset_index(drop=True)


def dynamics(curve: pd.DataFrame, sigma: float) -> dict:
    """응답 곡선에서 L·τ·T_s 와 최종 변화량을 뽑는다. 못 뽑으면 None.

    임계는 2×SE 다. 검정하는 대상이 한 관측이 아니라 **평균**이므로 σ 가 아니라
    평균의 표준오차를 쓴다.
    """
    out = {"dead_time": None, "tau": None, "settle": None,
           "final": None, "peak_sem": None, "plateau_reached": None}
    if curve.empty:
        return out
    post = curve[curve[LAG] >= 0]
    if post.empty:
        return out

    # 최종 변화량 = 뒤쪽 1/4 구간의 평균. 한 점을 쓰면 그 점의 노이즈가 다 실린다.
    q = max(1, len(post) // 4)
    tail = post.tail(q)
    final = float(tail["mean"].mean())
    out["final"] = final

    # 관측 창이 끝날 때까지 아직 오르고 있으면 final 이 과소평가되고, τ 도 같이
    # 작게 나온다(실측: τ=20 을 심고 post=60 분으로 보면 15 로 회수된다). 숫자를
    # 지우지는 않되 "하한" 이라는 사실을 함께 낸다 - post 를 늘리면 해결된다.
    prev = post.tail(2 * q).head(q)
    if len(prev) and np.isfinite(final) and final != 0:
        rise = abs(float(tail["mean"].mean() - prev["mean"].mean()))
        out["plateau_reached"] = bool(rise <= _SETTLE_BAND * abs(final))
    out["peak_sem"] = float(post["sem"].max())
    if not np.isfinite(final) or final == 0:
        return out

    start = _first_sustained(post["mean"] >= _SIGMA_K * post["sem"])
    if start is None:
        return out
    out["dead_time"] = int(post[LAG].iloc[start])

    reached = post[post["mean"] >= _TAU_FRACTION * final]
    if not reached.empty:
        out["tau"] = int(reached[LAG].iloc[0]) - out["dead_time"]

    band = abs(final) * _SETTLE_BAND
    inside = (post["mean"] - final).abs() <= band
    # 마지막까지 계속 밴드 안이었던 구간의 시작
    if inside.iloc[-1]:
        last_out = np.where(~inside.to_numpy())[0]
        idx = last_out[-1] + 1 if len(last_out) else 0
        out["settle"] = int(post[LAG].iloc[idx])
    return out


def _first_sustained(flags: pd.Series) -> int | None:
    """True 가 _MIN_RUN 회 연속되는 첫 위치. 없으면 None.

    단발 초과를 반응으로 읽지 않기 위한 것이다(_MIN_RUN 주석 참고).
    """
    run = 0
    for i, ok in enumerate(flags.to_numpy()):
        run = run + 1 if ok else 0
        if run >= _MIN_RUN:
            return i - _MIN_RUN + 1
    return None


def verdict(
    sigma: float, n_events: int, n_pairs: int, dyn: dict, tau_guess: float
) -> dict:
    """지연을 말할 수 있는가. 못 하면 무엇이 더 필요한지까지.

    이 함수의 존재 이유가 이 모듈의 존재 이유다. 표본이 모자란 채로 낸 L 은
    숫자처럼 보여서 더 위험하다 - 없는 것보다 나쁘다.
    """
    base = {
        "sigma": sigma,
        "n_events": n_events,
        "n_pairs": n_pairs,
        "identifiable": False,
        "reason": None,
        "required_events": -1,
        "shortfall_events": -1,
        # 지금 표본으로 0 과 구별할 수 있는 가장 작은 평균 반응.
        "detectable": detectable_response(sigma, n_pairs),
    }
    if n_events <= 0 or n_pairs < _MIN_PAIRS:
        return {**base, "reason": "no_events"}

    # 반응이 안 보이는 것과 표본이 모자란 것은 다른 진단이다. 뭉치면 "반응 없음"
    # 에 대고 "이벤트 150만 건이 필요하다" 는 말이 나온다 - 공식상 맞지만 쓸모가
    # 없고 고장난 것처럼 보인다. 없으면 "이 크기 이하는 못 본다" 가 맞는 답이다.
    final = dyn.get("final")
    if dyn.get("dead_time") is None or not final or not np.isfinite(final):
        return {**base, "reason": "no_response"}

    need_pairs = required_pairs(sigma, final, tau_guess)
    per_event = n_pairs / n_events
    need_events = int(np.ceil(need_pairs / per_event)) if need_pairs > 0 else -1
    if need_pairs > 0 and n_pairs >= need_pairs:
        return {**base, "identifiable": True, "required_events": need_events}
    return {
        **base,
        "reason": "underpowered",
        "required_events": need_events,
        "shortfall_events": max(0, need_events - n_events) if need_events > 0 else -1,
    }


def detectable_response(sigma: float, n_pairs: int) -> float:
    """지금 표본으로 0 과 구별할 수 있는 가장 작은 평균 반응(Wet 절대 단위).

    반응이 안 보일 때 "몇 건 더" 대신 내놓을 숫자다. "이 크기 이하의 반응은 지금
    데이터로는 못 본다" 는 말이 현장에 훨씬 쓸모 있다 - 조정 폭을 얼마나 키워야
    하는지가 여기서 바로 나온다.
    """
    if not np.isfinite(sigma) or n_pairs < 1:
        return float("nan")
    return float(_SIGMA_K * sigma / np.sqrt(n_pairs))
