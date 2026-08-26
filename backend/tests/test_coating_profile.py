"""Toeplitz 영향행렬 — 파라미터 625개를 5개로 줄이는 물리 가정을 코드로
못박는다. 여기가 틀리면 zone 인덱스가 밀려 이웃 영향이 엉뚱한 곳에 붙는다."""
import numpy as np

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
