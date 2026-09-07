"""Toeplitz 영향행렬 — 파라미터 625개를 5개로 줄이는 물리 가정을 코드로
못박는다. 여기가 틀리면 zone 인덱스가 밀려 이웃 영향이 엉뚱한 곳에 붙는다."""
import numpy as np

from app.coating import schemas as S
from app.coating.model import profile


def test_kernel_recovers_known_convolution():
    """알려진 커널로 만든 데이터에서 그 커널이 나와야 한다.
    이 테스트가 통과하면 zone 인덱싱과 zero-pad 가 맞다는 뜻이다."""
    rng = np.random.default_rng(0)
    k = 2
    true = np.array([0.1, 0.3, 1.0, 0.3, 0.1])
    dg = rng.normal(size=(200, 25))
    dw = np.zeros((200, 25))
    for i in range(25):
        for j, off in enumerate(range(-k, k + 1)):
            src = i + off
            if 0 <= src < 25:
                dw[:, i] += true[j] * dg[:, src]
    est = profile.fit_kernel(dg, dw, k=k, alpha=1e-6)
    assert np.allclose(est, true, atol=0.02)


def test_kernel_is_symmetric_length():
    est = profile.fit_kernel(
        np.random.default_rng(1).normal(size=(50, 25)),
        np.random.default_rng(2).normal(size=(50, 25)),
        k=3, alpha=1.0,
    )
    assert est.shape == (7,)


def test_kernel_to_matrix_is_banded_and_shift_invariant():
    """모든 zone 이 같은 커널을 갖는다는 가정이 행렬로 옳게 펼쳐지는지."""
    g = profile.kernel_to_matrix(np.array([0.1, 1.0, 0.1]), n_zones=25)
    assert g.shape == (25, 25)
    assert g[10, 10] == 1.0
    assert g[10, 9] == 0.1
    assert g[10, 11] == 0.1
    assert g[10, 12] == 0.0
    # 가장자리는 잘린다 — 이웃이 없다
    assert g[0, 0] == 1.0
    assert g[0, 1] == 0.1


def test_rank_diagnostics_detects_correlated_adjustment_patterns():
    """작업자가 늘 같은 패턴으로 조정하면 이벤트가 많아도 랭크가 낮다.
    이게 이 과제의 진짜 관문이고, 숫자 하나로 나와야 한다."""
    base = np.ones((1, 25))
    dg = np.repeat(base, 300, axis=0) * np.arange(1, 301).reshape(-1, 1)
    d = profile.rank_diagnostics(dg)
    assert d["n_events"] == 300
    assert d["effective_rank"] == 1


def test_rank_diagnostics_full_rank_for_random_patterns():
    dg = np.random.default_rng(3).normal(size=(300, 25))
    d = profile.rank_diagnostics(dg)
    assert d["effective_rank"] == 25
    assert len(d["singular_values"]) == 25


# ── 커널 물리 정합성 ────────────────────────────────────────────────
# 랭크가 충분하다는 것과 커널이 말이 된다는 것은 다른 사실이다. 랭크는
# "풀 수 있나" 를 묻고 여기는 "푼 답이 물리인가" 를 묻는다.


def test_kernel_diagnostics_accepts_bell_shape():
    """중심이 최대이고 부호가 같고 바깥으로 감소하면 통과다."""
    d = profile.kernel_diagnostics(np.array([0.05, 0.20, 0.50, 0.20, 0.05]))
    assert d["plausible"] is True
    assert d["peak_at_center"] is True
    assert d["sign_consistent"] is True
    assert d["monotone_decay"] is True


def test_kernel_diagnostics_accepts_negative_center():
    """gap 을 키우면 Wet 이 준다는 설비여도 모양은 같다. 중심 부호로 정렬한다."""
    d = profile.kernel_diagnostics(np.array([-0.05, -0.20, -0.50, -0.20, -0.05]))
    assert d["plausible"] is True


def test_kernel_diagnostics_flags_off_center_peak():
    """이웃이 중심보다 크면 zone 대응이 밀렸다는 뜻이다 — 커널이 아니라 색인 버그다."""
    d = profile.kernel_diagnostics(np.array([0.05, 0.50, 0.20, 0.05, 0.02]))
    assert d["plausible"] is False
    assert d["peak_at_center"] is False


def test_kernel_diagnostics_flags_sign_flip():
    """한 탭만 부호가 뒤집히면 물리가 아니라 노이즈를 문 것이다."""
    d = profile.kernel_diagnostics(np.array([0.05, -0.30, 0.50, 0.20, 0.05]))
    assert d["plausible"] is False
    assert d["sign_consistent"] is False


def test_kernel_diagnostics_flags_truncated_spread():
    """가장자리 탭이 아직 크면 k 가 좁아 퍼짐이 잘린 것이다. 실패가 아니라 경고."""
    d = profile.kernel_diagnostics(np.array([0.40, 0.45, 0.50, 0.45, 0.40]))
    assert d["truncated"] is True
    assert d["edge_ratio"] > 0.5


def test_kernel_diagnostics_reports_asymmetry():
    """좌우가 다르면 흐름 방향 효과이거나 정렬이 틀린 것이다. 병기만 한다."""
    d = profile.kernel_diagnostics(np.array([0.02, 0.10, 0.50, 0.30, 0.15]))
    assert d["asymmetry"] > 0.1


def test_zone_diagnostics_finds_never_adjusted_zones():
    """한 번도 안 건드린 zone 은 랭크 결손의 정상적인 이유다."""
    dg = np.zeros((10, S.N_ZONES))
    rng = np.random.default_rng(0)
    for z in range(1, S.N_ZONES - 1):        # zone 2..24 만 움직인다
        dg[:, z] = rng.normal(size=10)
    d = profile.zone_adjustment_diagnostics(dg)
    assert d["never_adjusted"] == [1, 25]
    assert d["n_adjusted"] == 23


def test_zone_diagnostics_finds_locked_pairs():
    """항상 같이 움직인 두 zone 은 기여를 영영 못 가른다."""
    rng = np.random.default_rng(1)
    dg = rng.normal(size=(20, S.N_ZONES))
    dg[:, 6] = dg[:, 5]                       # zone 6·7 을 세트로 조정
    d = profile.zone_adjustment_diagnostics(dg)
    assert (6, 7) in d["locked_pairs"]


def test_zone_diagnostics_explains_rank_shortfall():
    """설명된 결손과 설명 안 된 결손을 나눈다 — 뒤가 남으면 더 볼 것이 있다는 신호다."""
    rng = np.random.default_rng(2)
    dg = np.zeros((40, S.N_ZONES))
    for z in range(1, S.N_ZONES - 1):        # zone 2..24 만 움직인다
        dg[:, z] = rng.normal(size=40)
    d = profile.zone_adjustment_diagnostics(dg)
    assert d["never_adjusted"] == [1, 25]
    assert d["effective_rank"] == d["n_adjusted"] == 23
    assert d["unexplained_shortfall"] == 0


def test_zone_diagnostics_rank_is_capped_by_event_count():
    """이벤트 10건이면 zone 을 23개 건드려도 랭크는 10 이다. 이것을 결손으로
    세면 정상적인 데이터가 부당하게 '설명 안 됨' 으로 찍힌다."""
    rng = np.random.default_rng(3)
    dg = np.zeros((10, S.N_ZONES))
    for z in range(1, S.N_ZONES - 1):
        dg[:, z] = rng.normal(size=10)
    d = profile.zone_adjustment_diagnostics(dg)
    assert d["effective_rank"] == 10
    assert d["reachable_rank"] == 10
    assert d["unexplained_shortfall"] == 0


# ── 판정할 힘이 있는가 ──────────────────────────────────────────────
# "종 모양이 아니다" 에는 커널이 정말 그런 경우와 표본이 모자란 경우가 섞여
# 있다. 둘의 다음 행동이 정반대라 반드시 갈라야 한다.


def _conv_samples(n_events, step, noise, seed, kernel=(0.0004, 0.0015, 0.0031, 0.0015, 0.0004)):
    rng = np.random.default_rng(seed)
    dg = np.zeros((n_events, S.N_ZONES))
    for e in range(n_events):
        for z in rng.choice(np.arange(1, 24), size=3, replace=False):
            dg[e, z] = rng.choice([-1, 1]) * step
    dw = (profile.build_design(dg, 2) @ np.array(kernel)).reshape(n_events, S.N_ZONES)
    dw += rng.normal(0, noise, (n_events, S.N_ZONES)) + rng.normal(0, 0.3, (n_events, 1))
    dw[:, [0, 17, 24]] = np.nan                       # zone 1·18·25 Wet 결측
    return dg, dw


def test_standard_errors_shrink_with_more_events():
    """SE 는 √n 으로 줄어야 한다. 안 그러면 표본이 늘어도 판정이 안 열린다."""
    few = profile.kernel_standard_errors(*_conv_samples(40, 1, 0.0095, 0), k=2, alpha=1.0)
    many = profile.kernel_standard_errors(*_conv_samples(400, 1, 0.0095, 0), k=2, alpha=1.0)
    assert many[2] < few[2] / 2


def test_standard_errors_shrink_with_bigger_moves():
    """조정 폭이 커도 SE 가 줄어야 한다 - 이쪽이 표본을 늘리는 것보다 싸다는
    권고의 근거다."""
    small = profile.kernel_standard_errors(*_conv_samples(40, 1, 0.0095, 1), k=2, alpha=1.0)
    big = profile.kernel_standard_errors(*_conv_samples(40, 5, 0.0095, 1), k=2, alpha=1.0)
    assert big[2] < small[2] / 3


def test_shape_is_not_resolvable_at_the_measured_condition():
    """실측 조건(조정 폭 1단위·30건)에서는 모양을 판정할 수 없다.
    이 저장소가 이 조건에서 낸 '물리 아님' 은 커널이 틀렸다는 뜻이 아니다."""
    dg, dw = _conv_samples(30, 1, 0.0095, 0)
    r = profile.shape_is_resolvable(
        profile.fit_kernel(dg, dw, 2, 1.0),
        profile.kernel_standard_errors(dg, dw, 2, 1.0),
    )
    assert r["resolvable"] is False


def test_shape_becomes_resolvable_when_moves_get_bigger():
    """같은 30건이라도 조정 폭이 5배면 열린다."""
    dg, dw = _conv_samples(30, 5, 0.0095, 0)
    r = profile.shape_is_resolvable(
        profile.fit_kernel(dg, dw, 2, 1.0),
        profile.kernel_standard_errors(dg, dw, 2, 1.0),
    )
    assert r["resolvable"] is True


def test_shape_resolvability_needs_both_gates():
    """중심이 0 과 구별돼도 이웃과 안 갈리면 '감소한다' 를 말할 수 없다."""
    flat = np.array([0.30, 0.30, 0.31, 0.30, 0.30])
    se = np.full(5, 0.01)
    r = profile.shape_is_resolvable(flat, se)
    assert r["center_snr"] > 2
    assert r["resolvable"] is False
    assert "이웃" in r["reason"]


def test_standard_errors_are_nan_when_samples_are_too_few():
    """파라미터보다 관측이 적으면 SE 를 낼 수 없다 - 0 을 주면 판정이 열린다."""
    dg = np.zeros((1, S.N_ZONES))
    dg[0, 5] = 1.0
    dw = np.full((1, S.N_ZONES), np.nan)
    dw[0, 5] = 0.1
    se = profile.kernel_standard_errors(dg, dw, 2, 1.0)
    assert np.isnan(se).all()
    assert profile.shape_is_resolvable(np.zeros(5), se)["resolvable"] is False
