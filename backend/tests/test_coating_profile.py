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
