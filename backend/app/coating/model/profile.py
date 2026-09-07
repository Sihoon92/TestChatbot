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


# 가장자리 탭이 중심의 이 비율을 넘으면 퍼짐이 k 에서 잘렸다고 본다.
# 종 모양이라면 ±k 에서 이미 꼬리여야 한다.
_EDGE_TRUNCATED = 0.5
# 단조·부호 판정의 허용 오차(중심 크기 대비). 릿지와 측정 노이즈로 탭이
# 조금 출렁이는 것까지 실패로 몰면 판정이 늘 실패한다.
_WOBBLE = 0.05


def kernel_diagnostics(kernel: np.ndarray) -> dict:
    """커널이 물리적으로 말이 되는가.

    랭크 진단과 묻는 것이 다르다. rank_diagnostics 는 "풀 수 있나" 를 묻고
    여기는 "푼 답이 물리인가" 를 묻는다. 랭크가 22 여도 커널이 두 봉우리로
    나오면 그건 식별이 된 것이 아니라 노이즈를 문 것이다.

    다이 볼트 하나를 조이면 그 자리가 가장 많이 움직이고 옆으로 갈수록 덜
    움직인다. 그래서 세 가지가 참이어야 한다.

        중심이 최대   아니면 zone 대응이 밀린 것이다 - 커널이 아니라 색인 버그다
        부호가 일관   한 탭만 뒤집히는 물리는 없다
        바깥으로 감소 두 봉우리는 계단 응답이 아니다

    중심 부호로 정렬해서 본다. gap 을 키우면 Wet 이 주는 설비여도 모양은 같고,
    부호는 설비의 성질이지 커널의 흠이 아니다.

    잘림(truncated)과 비대칭(asymmetry)은 실패로 치지 않는다. 전자는 k 를
    늘리라는 말이고 후자는 흐름 방향 효과일 수 있어, 둘 다 사람이 판단할 일이다.
    """
    kernel = np.asarray(kernel, dtype=float)
    k = (len(kernel) - 1) // 2
    center = kernel[k]
    scale = abs(center)
    if not np.isfinite(scale) or scale == 0:
        return {
            "kernel": kernel.tolist(), "half_width": k, "center": float(center),
            "peak_at_center": False, "sign_consistent": False,
            "monotone_decay": False, "truncated": False,
            "edge_ratio": float("nan"), "asymmetry": float("nan"),
            "mass_ratio": float("nan"), "plausible": False,
            "reasons": ["중심 탭이 0 이거나 유한하지 않다 — 커널을 못 뽑았다."],
        }

    oriented = kernel * (1.0 if center > 0 else -1.0)
    tol = _WOBBLE * scale

    peak_at_center = bool(int(np.argmax(np.abs(kernel))) == k)
    sign_consistent = bool((oriented >= -tol).all())
    # 왼쪽은 중심까지 올라오고 오른쪽은 중심에서 내려가야 한다.
    monotone = bool(
        (np.diff(oriented[: k + 1]) >= -tol).all()
        and (np.diff(oriented[k:]) <= tol).all()
    )
    edge_ratio = float(max(abs(kernel[0]), abs(kernel[-1])) / scale)
    asymmetry = float(
        max(
            (abs(oriented[k - j] - oriented[k + j]) for j in range(1, k + 1)),
            default=0.0,
        ) / scale
    )
    total = float(np.abs(oriented).sum())

    reasons = []
    if not peak_at_center:
        reasons.append(
            f"중심이 최대가 아니다 (최대는 offset {int(np.argmax(np.abs(kernel))) - k:+d}). "
            "zone 대응이 한 칸 밀렸을 가능성을 먼저 본다."
        )
    if not sign_consistent:
        flipped = [i - k for i, v in enumerate(oriented) if v < -tol]
        reasons.append(f"부호가 뒤집힌 탭이 있다 (offset {flipped}). 노이즈를 물었을 수 있다.")
    if not monotone:
        reasons.append("중심에서 바깥으로 단조 감소하지 않는다 — 종 모양이 아니다.")

    return {
        "kernel": kernel.tolist(),
        "half_width": k,
        "center": float(center),
        "peak_at_center": peak_at_center,
        "sign_consistent": sign_consistent,
        "monotone_decay": monotone,
        "truncated": bool(edge_ratio > _EDGE_TRUNCATED),
        "edge_ratio": edge_ratio,
        "asymmetry": asymmetry,
        "mass_ratio": float(scale / total) if total else float("nan"),
        "plausible": bool(peak_at_center and sign_consistent and monotone),
        "reasons": reasons or ["중심 최대 · 부호 일관 · 바깥으로 감소."],
    }


# 두 zone 이 이만큼 상관되면 조정이 사실상 묶여 있다고 본다.
_LOCKED_CORR = 0.99


def zone_adjustment_diagnostics(delta_gap: np.ndarray) -> dict:
    """랭크가 왜 25 가 아닌지를 zone 의 말로 옮긴다.

    rank_diagnostics 는 숫자 하나를 준다. 그것만으로는 사업부에 무엇을 요구할지
    정할 수 없다 - "랭크가 22 다" 와 "1·25 번 볼트는 애초에 없고 6·7 번은 늘
    같이 돌린다" 는 같은 사실의 다른 쓸모다.

    결손을 셋으로 나눈다.
        없는 zone   항목 자체가 없거나 한 번도 안 바뀐 열. 정상적인 이유다
        묶인 쌍     항상 같이 움직여 기여를 못 가르는 열. 습관이라 요청으로 풀린다
        설명 안 됨  위 둘로 설명이 안 되는 나머지. 남으면 더 볼 것이 있다는 신호다
    """
    dg = np.nan_to_num(np.asarray(delta_gap, dtype=float))
    n_zones = dg.shape[1] if dg.ndim == 2 else 0
    if dg.size == 0:
        # 키 구성은 어느 경로로 나가든 같아야 한다. 호출자가 분기마다 다른
        # 키를 챙기게 만들면 그 분기는 반드시 한 번 빠뜨려진다.
        return {
            "never_adjusted": list(range(1, n_zones + 1)), "n_adjusted": 0,
            "locked_pairs": [], "effective_rank": 0, "reachable_rank": 0,
            "unexplained_shortfall": 0,
        }

    moved = np.abs(dg).sum(axis=0) > 0
    adjusted = [z + 1 for z in range(n_zones) if moved[z]]
    never = [z + 1 for z in range(n_zones) if not moved[z]]

    locked: list[tuple[int, int]] = []
    idx = [z - 1 for z in adjusted]
    for a in range(len(idx)):
        for b in range(a + 1, len(idx)):
            u, v = dg[:, idx[a]], dg[:, idx[b]]
            if u.std() == 0 or v.std() == 0:
                continue
            if abs(float(np.corrcoef(u, v)[0, 1])) >= _LOCKED_CORR:
                locked.append((adjusted[a], adjusted[b]))

    rank = rank_diagnostics(dg)["effective_rank"]
    # 랭크의 상한은 **이벤트 수로도** 묶인다. 이벤트가 10건이면 zone 을 23개
    # 건드렸어도 랭크는 10 을 못 넘는다 - 이것을 빼먹으면 정상적인 데이터를
    # "설명 안 되는 결손이 13" 이라고 몰아세운다.
    #
    # 묶인 쌍 하나가 열 하나만큼의 랭크를 먹는다. 셋이 서로 묶이면 두 쌍이
    # 잡히지만 잃는 랭크도 둘이라 이 셈이 그대로 맞는다.
    reachable = min(len(dg), len(adjusted) - len(locked))
    return {
        "never_adjusted": never,
        "n_adjusted": len(adjusted),
        "locked_pairs": locked,
        "effective_rank": int(rank),
        "reachable_rank": int(max(0, reachable)),
        "unexplained_shortfall": int(max(0, reachable - rank)),
    }
