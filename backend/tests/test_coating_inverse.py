"""역산 — 이동거리 항이 없으면 25개 볼트를 전부 새 값으로 바꾸라는
제안이 나오고, 현장에서 그건 곧 '안 씀' 이다."""
import numpy as np

from app.coating.model import inverse, profile

G = profile.kernel_to_matrix(np.array([0.1, 1.0, 0.1]), n_zones=25)


def test_recommendation_moves_profile_toward_target():
    cur_gap = np.full(25, 300.0)
    cur_prof = np.zeros(25)
    cur_prof[10] = -0.3  # 10번 zone 이 얇다
    target = np.zeros(25)
    out = inverse.recommend_gap(
        G, cur_gap, cur_prof, target,
        bounds=(100.0, 500.0), max_step=50.0, lam_move=1e-4, lam_smooth=1e-3,
    )
    achieved = cur_prof + G @ (out - cur_gap)
    assert np.abs(achieved).max() < np.abs(cur_prof).max()


def test_recommendation_respects_bounds():
    out = inverse.recommend_gap(
        G, np.full(25, 490.0), np.full(25, -5.0), np.zeros(25),
        bounds=(100.0, 500.0), max_step=100.0, lam_move=1e-6, lam_smooth=1e-6,
    )
    assert out.max() <= 500.0 + 1e-6
    assert out.min() >= 100.0 - 1e-6


def test_recommendation_respects_max_step():
    """한 번에 크게 움직이라는 제안은 설비 안전상 받아들여지지 않는다."""
    out = inverse.recommend_gap(
        G, np.full(25, 300.0), np.full(25, -5.0), np.zeros(25),
        bounds=(100.0, 500.0), max_step=10.0, lam_move=1e-6, lam_smooth=1e-6,
    )
    assert np.abs(out - 300.0).max() <= 10.0 + 1e-6


def test_zero_error_means_zero_movement():
    """이미 목표면 움직이지 않는다. 이동거리 항이 실제로 작동하는지의 확인."""
    cur_gap = np.full(25, 300.0)
    out = inverse.recommend_gap(
        G, cur_gap, np.zeros(25), np.zeros(25),
        bounds=(100.0, 500.0), max_step=50.0, lam_move=1e-2, lam_smooth=1e-2,
    )
    assert np.allclose(out, cur_gap, atol=1e-3)
