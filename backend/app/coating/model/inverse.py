"""역산 — surrogate 위의 제약 최적화.

직접 역회귀를 하지 않는 이유: 같은 L/L 을 만드는 조합이 무수히 많아 역이
유일하지 않고, 직접 회귀하면 여러 정답의 '평균' 이 나오는데 그 평균 조합은
어느 것도 아닌 값일 수 있다.

목적함수 = 목표오차 + λ_move·이동거리² + λ_smooth·인접 단차²

이동거리 항이 실무적으로 결정적이다. 수학적으로 동등한 해가 여럿일 때
작업자가 지금 값에서 가장 적게 움직여 도달할 수 있는 해를 골라줘야 쓴다.
"""
import numpy as np
from scipy.optimize import minimize


def recommend_gap(
    g: np.ndarray,
    current_gap: np.ndarray,
    current_profile: np.ndarray,
    target_profile: np.ndarray,
    bounds: tuple[float, float],
    max_step: float,
    lam_move: float,
    lam_smooth: float,
) -> np.ndarray:
    """목표 프로파일에 가장 가깝게 만드는 gap 벡터.

    bounds 는 zone 별 상하한, max_step 은 현재값 대비 1회 이동 한계다.
    둘의 교집합이 실제 탐색 범위가 된다.
    """
    n = len(current_gap)
    want = np.nan_to_num(target_profile - current_profile)

    def cost(delta):
        err = g @ delta - want
        step = np.diff(delta)
        return (
            float(err @ err)
            + lam_move * float(delta @ delta)
            + lam_smooth * float(step @ step)
        )

    def grad(delta):
        err = g @ delta - want
        d2 = np.zeros(n)
        step = np.diff(delta)
        d2[:-1] -= step
        d2[1:] += step
        return 2 * (g.T @ err + lam_move * delta + lam_smooth * d2)

    lo = np.maximum(bounds[0] - current_gap, -max_step)
    hi = np.minimum(bounds[1] - current_gap, max_step)
    res = minimize(
        cost, np.zeros(n), jac=grad, method="L-BFGS-B",
        bounds=list(zip(lo, hi)),
    )
    return current_gap + res.x
