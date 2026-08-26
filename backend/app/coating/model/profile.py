"""프로파일 모델 — Toeplitz 영향행렬. ★순수

Δwet_i = Σ_{j=-k..k} g_j · Δgap_{i+j}

다이 볼트 하나를 조이면 주변으로 종 모양으로 퍼지고, 그 퍼짐 모양은 zone
위치와 거의 무관하다. 그 물리를 Toeplitz(공간 불변) 가정으로 못박으면
파라미터가 625개에서 2k+1개로 줄어든다.

가장자리는 zero-pad 한다 — 1번 zone 의 왼쪽 이웃은 존재하지 않으므로
기여가 0 이다.
"""
import numpy as np

from app.coating import schemas as S


def build_design(delta_gap: np.ndarray, k: int) -> np.ndarray:
    """(n_events, 25) → (n_events*25, 2k+1).

    행 하나가 '이벤트 e 의 zone i' 에 대한 방정식이다. 이벤트 하나가
    25개 방정식을 준다 — 이것이 소수 이벤트로도 커널을 식별할 수 있는 이유다.
    """
    n_events, n_zones = delta_gap.shape
    width = 2 * k + 1
    padded = np.zeros((n_events, n_zones + 2 * k))
    padded[:, k : k + n_zones] = delta_gap
    rows = np.empty((n_events * n_zones, width))
    for i in range(n_zones):
        rows[i::n_zones] = padded[:, i : i + width]
    return rows


def fit_kernel(
    delta_gap: np.ndarray, delta_wet: np.ndarray, k: int, alpha: float
) -> np.ndarray:
    """릿지 정규방정식으로 커널을 푼다.

    delta_wet 은 프로파일 성분(이벤트별 평균 제거)만 쓴다. 레벨 변화는
    레벨 모델의 몫이고, 여기 섞이면 커널이 상수항을 흡수한다.

    **설계행렬에도 같은 평균 제거를 적용한다.** 한쪽만 빼면 추정이 편향된다 —
    y 에서 뺀 이벤트 평균 m_e = Σ_j g_j·mean_i(Δgap_{i+j}) 자체가 Δgap 과
    상관돼 있어서, 설계행렬을 그대로 두면 그 상관만큼 커널이 통째로 눌린다
    (실측: 참값 대비 전 성분 약 -0.07). 양쪽에 같은 within 변환을 걸면
    y = (X - rowmean X)·g 가 정확히 성립해 무편향으로 돌아온다.
    """
    n_events, n_zones = delta_gap.shape
    width = 2 * k + 1

    y = delta_wet - np.nanmean(delta_wet, axis=1, keepdims=True)
    y = y.reshape(-1)

    x = build_design(delta_gap, k).reshape(n_events, n_zones, width)
    x = x - np.nanmean(x, axis=1, keepdims=True)
    x = x.reshape(-1, width)

    ok = np.isfinite(y) & np.isfinite(x).all(axis=1)
    x, y = x[ok], y[ok]
    return np.linalg.solve(x.T @ x + alpha * np.eye(width), x.T @ y)


def kernel_to_matrix(kernel: np.ndarray, n_zones: int = S.N_ZONES) -> np.ndarray:
    """커널을 25×25 영향행렬로 펼친다. 가장자리는 잘린다."""
    k = (len(kernel) - 1) // 2
    g = np.zeros((n_zones, n_zones))
    for i in range(n_zones):
        for j, off in enumerate(range(-k, k + 1)):
            src = i + off
            if 0 <= src < n_zones:
                g[i, src] = kernel[j]
    return g


def rank_diagnostics(delta_gap: np.ndarray) -> dict:
    """Δgap 행렬의 유효 랭크.

    이벤트 수가 아니라 이 값이 진짜 제약이다. 작업자가 늘 비슷한 패턴으로
    조정하면 이벤트가 수백 개여도 랭크가 3~4 에 그치고, 그러면 밴드 이상의
    구조는 식별할 수 없다.
    """
    sv = np.linalg.svd(np.nan_to_num(delta_gap), compute_uv=False)
    tol = sv.max() * max(delta_gap.shape) * np.finfo(float).eps if sv.size else 0.0
    return {
        "singular_values": sv,
        "effective_rank": int((sv > tol).sum()),
        "n_events": int(delta_gap.shape[0]),
    }
