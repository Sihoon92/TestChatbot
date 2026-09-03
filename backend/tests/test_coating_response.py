"""동특성 추정 — 가장 중요한 것은 '심은 값을 되찾는가' 다.

추정기가 동작한다는 증거는 그것뿐이다. 실데이터에는 정답이 없으므로, 지연과
시정수를 아는 데이터를 만들어 회수되는지 본다. 반대로 반응이 없는 데이터에서
지연을 '찾아내면' 그건 노이즈를 읽은 것이다 — 그쪽도 함께 건다.
"""
import numpy as np
import pandas as pd
import pytest

from app.coating import events as ev_mod
from app.coating import features, panel, parse, pivot, response
from app.coating import schemas as S

TOL = 1  # 회수 허용 오차(분)


def plant(true_l=8, true_tau=10, gain=0.006, sigma=0.004, n_lots=6, seed=3):
    """지연·시정수를 아는 1차 지연계 데이터.

    gap 을 Δ 만큼 바꾸면 true_l 분 뒤부터 Wet 이 gain·Δ 를 향해 지수적으로
    올라간다. 올림과 내림을 섞어 부호 정렬이 실제로 필요하게 만든다.
    """
    rng = np.random.default_rng(seed)
    gaps, wets = S.GAP_ITEM_IDS[:5], S.WET_ITEM_IDS[:5]
    rows = []
    for li in range(n_lots):
        lot = f"L{li:02d}"
        t0 = pd.Timestamp("2026-03-01") + pd.Timedelta(hours=6 * li)
        gap, base = np.full(5, 300.0), np.full(5, 18.20)
        adj = {m: ((k + li) % 5, 20.0 if (k + li) % 2 == 0 else -20.0)
               for k, m in enumerate((90, 180, 270))}
        for m in range(360):
            at = t0 + pd.Timedelta(minutes=m)
            if m in adj:
                z, d = adj[m]
                gap[z] += d
            wet = base.copy()
            for am, (z, d) in adj.items():
                if m >= am + true_l:
                    wet[z] += gain * d * (1 - np.exp(-(m - am - true_l) / true_tau))
            for z in range(5):
                rows.append((lot, at, "P1", gaps[z], gap[z]))
                rows.append((lot, at, "P1", wets[z],
                             round(wet[z] + rng.normal(0, sigma), 4)))
    df = pd.DataFrame(rows, columns=[S.LOT, S.AT, S.PRODUCT, S.ITEM, S.VALUE])
    df[S.ROW_NO] = range(len(df))
    return df.merge(parse.load_item_dictionary(), on=S.ITEM, how="left")


def run(readings, post=60):
    """readings → (panel, delta, events, event_deltas, aligned, curve, sigma)."""
    d = pivot.dedupe_minute(readings)
    ev, dl = ev_mod.build_events(pivot.compress_runs(d), 2)
    wet = features.wet_wide(d)
    wm = features.wet_mean_series(wet, features.valid_zones(wet))
    if not ev.empty:
        ev = ev_mod.annotate_settling(ev, wm, 0.02, 5, 30)
    p = panel.build_panel(d, 30)
    dlt = panel.build_delta(p)
    sigma, _ = response.noise_floor(dlt, ev, 45)
    aligned = response.align_events(p, ev, dl, 15, post, 5)
    return p, dlt, ev, dl, aligned, response.response_curve(aligned), sigma


# ── 회수 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("true_l,true_tau", [(8, 10), (3, 5), (12, 8)])
def test_planted_dead_time_and_tau_are_recovered(true_l, true_tau):
    """추정기가 동작한다는 유일한 증거.

    회수값이 심은 L 보다 1 큰 것이 정상이다 — 지연 L 시점의 반응은 정확히
    0 이므로(1-e⁰) 처음 **측정되는** lag 은 L+1 이다. 우리가 내는 L 은 "이때부터
    보면 된다" 는 운영상의 값이라 그쪽이 맞다.
    """
    *_, curve, sigma = run(plant(true_l=true_l, true_tau=true_tau))
    dyn = response.dynamics(curve, sigma)
    assert abs(dyn["dead_time"] - (true_l + 1)) <= TOL
    assert abs(dyn["tau"] - true_tau) <= TOL + 1


def test_noise_floor_matches_what_was_planted():
    """σ 는 분당 차분의 표준편차라, 관측 노이즈 s 를 심으면 s·√2 가 나와야 한다."""
    s = 0.004
    _, dlt, ev, *_ = run(plant(sigma=s))
    sigma, minutes = response.noise_floor(dlt, ev, 45)
    assert abs(sigma - s * np.sqrt(2)) < 0.001
    assert minutes > 0


def test_final_change_matches_the_planted_gain():
    *_, curve, sigma = run(plant(gain=0.006))
    assert abs(response.dynamics(curve, sigma)["final"] - 0.006 * 20) < 0.01


# ── 거짓 양성 ───────────────────────────────────────────────────────────

def test_no_response_data_yields_no_dead_time():
    """반응이 없는데 지연을 찾아내면 노이즈를 읽은 것이다."""
    *_, curve, sigma = run(plant(gain=0.0))
    assert response.dynamics(curve, sigma)["dead_time"] is None


def test_a_single_threshold_crossing_is_not_a_response():
    """lag 을 70개 넘게 훑으며 2σ 검정을 하면 우연한 초과가 3~4번 나온다.
    '첫 초과' 를 집으면 그 노이즈를 문다 — 실제로 L=8 데이터에서 L=2 로 답했다.
    연속 초과를 요구해야 한다."""
    curve = pd.DataFrame({
        response.LAG: range(0, 40),
        "mean": [0.0] * 5 + [1.0] + [0.0] * 14 + [1.0] * 20,   # lag 5 는 단발
        "sem": [0.1] * 40,
        "n": [10] * 40,
    })
    assert response.dynamics(curve, 0.1)["dead_time"] == 20


def test_noise_floor_excludes_the_neighbourhood_of_events():
    """조정 근처를 안 빼면 σ 가 반응을 포함해 부풀고, 진짜 반응이 노이즈 아래로
    숨어 '지연 없음' 으로 잘못 판정한다."""
    _, dlt, ev, *_ = run(plant())
    wide = response.noise_floor(dlt, ev, guard_minutes=45)[0]
    none = response.noise_floor(dlt, ev, guard_minutes=0)[0]
    assert wide < none


# ── 정렬의 규칙 ─────────────────────────────────────────────────────────

def test_up_and_down_events_do_not_cancel():
    """부호 정렬이 없으면 올림과 내림이 평균에서 상쇄돼 반응이 없어 보인다."""
    *_, aligned, _, _ = run(plant())
    assert (aligned[response.D_GAP] > 0).any() and (aligned[response.D_GAP] < 0).any()
    late = aligned[aligned[response.LAG] > 30][response.RESPONSE]
    assert late.mean() > 0


def test_only_adjusted_zones_are_aligned():
    """조정 안 된 zone 의 움직임은 이웃에서 넘어온 결합이라 원인 시각이 다르다.
    섞으면 지연이 흐려진다(결합 크기는 Toeplitz 커널이 따로 맡는다)."""
    *_, aligned, _, _ = run(plant())
    assert aligned.groupby([S.EVENT, S.ZONE]).ngroups == aligned[S.EVENT].nunique()


def test_contaminated_events_are_excluded():
    """오염 이벤트는 다음 조정의 효과가 섞여 지연을 왜곡한다
    (features.delta_samples 와 같은 기준)."""
    p, _, ev, dl, *_ = run(plant())
    assert len(ev) >= 2

    dirty = ev.copy()
    dirty[S.CONTAMINATED] = [i % 2 == 1 for i in range(len(dirty))]
    aligned = response.align_events(p, dirty, dl, 15, 60, 5)

    kept = set(aligned[S.EVENT])
    assert kept == set(dirty.loc[~dirty[S.CONTAMINATED], S.EVENT])
    assert kept.isdisjoint(set(dirty.loc[dirty[S.CONTAMINATED], S.EVENT]))


def test_baseline_is_a_window_mean_not_one_point():
    """한 점과 빼면 그 점의 측정 노이즈가 모든 lag 에 그대로 실린다."""
    *_, aligned, _, _ = run(plant())
    pre = aligned[aligned[response.LAG] < -2][response.RESPONSE]
    assert abs(pre.mean()) < 0.01      # 조정 전이므로 0 근처


# ── 판정 ────────────────────────────────────────────────────────────────

def test_verdict_separates_no_response_from_underpowered():
    """둘은 다음 행동이 다르다 — 앞은 조정 폭을, 뒤는 이벤트 수를 늘려야 한다.
    뭉치면 '반응 없음' 에 대고 '이벤트 150만 건 필요' 라는 말이 나온다."""
    *_, aligned, curve, sigma = run(plant(gain=0.0))
    dyn = response.dynamics(curve, sigma)
    v = response.verdict(sigma, 18, aligned.groupby([S.EVENT, S.ZONE]).ngroups, dyn, 10)
    assert v["reason"] == "no_response"
    assert not v["identifiable"]
    assert np.isfinite(v["detectable"])


def test_verdict_reports_no_events_when_there_are_none():
    v = response.verdict(0.005, 0, 0, {"final": None}, 10)
    assert v["reason"] == "no_events" and not v["identifiable"]


def test_verdict_passes_when_the_response_dwarfs_the_noise():
    *_, aligned, curve, sigma = run(plant())
    dyn = response.dynamics(curve, sigma)
    v = response.verdict(sigma, 18, aligned.groupby([S.EVENT, S.ZONE]).ngroups, dyn, 10)
    assert v["identifiable"] and v["required_events"] > 0


def test_required_pairs_grows_with_noise():
    """σ 가 커지면 같은 반응을 가르는 데 더 많은 표본이 필요하다."""
    assert response.required_pairs(0.02, 0.1, 10) > response.required_pairs(0.005, 0.1, 10)


def test_detectable_response_shrinks_with_more_samples():
    assert response.detectable_response(0.01, 100) < response.detectable_response(0.01, 4)


# ── 관측 창 ─────────────────────────────────────────────────────────────

def test_short_window_is_flagged_as_not_plateaued():
    """post 가 τ 보다 충분히 길지 않으면 최종값이 과소평가되고 τ 도 작게 나온다.
    숫자를 지우지는 않되 '하한' 이라는 사실을 함께 내야 한다."""
    *_, short, sigma = run(plant(true_l=15, true_tau=20), post=60)
    *_, long, sigma2 = run(plant(true_l=15, true_tau=20), post=120)
    assert response.dynamics(short, sigma)["plateau_reached"] is False
    assert response.dynamics(long, sigma2)["plateau_reached"] is True
    # 창을 늘리면 τ 가 심은 값에 가까워진다
    assert abs(response.dynamics(long, sigma2)["tau"] - 20) <= TOL + 1


def test_empty_inputs_are_not_errors():
    empty = pd.DataFrame(columns=[response.LAG, "mean", "sem", "n"])
    assert response.dynamics(empty, 0.01)["dead_time"] is None
    assert response.response_curve(
        pd.DataFrame(columns=[S.EVENT, S.LOT, response.LAG, S.ZONE,
                              response.D_GAP, response.RESPONSE])
    ).empty


# ── 라인 속도 환산 ───────────────────────────────────────────────────────

def test_implied_distance_converts_lag_by_line_speed():
    """L 을 거리로 환산해야 사람이 상식으로 기각할 수 있다.

    L 자체에는 참값이 없어 맞는지 확인할 방법이 없다. 라인 속도가 설비 고정값
    (전 제품 공통)이므로 L×속도 = 다이~측정기 거리가 되고, 그 거리가 설비에서
    말이 되는 크기인지는 현장이 즉시 안다. 노이즈를 문 L 을 걸러내는 유일한
    외부 기준이다.
    """
    assert response.implied_distance_m(8, 35.0) == pytest.approx(280.0)


def test_implied_distance_is_none_without_dead_time():
    """L 을 못 냈으면 거리도 없다. 0 을 내면 '거리 0m' 라는 없는 사실이 생긴다."""
    assert response.implied_distance_m(None, 35.0) is None
