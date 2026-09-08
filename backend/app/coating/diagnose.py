"""덤프를 되읽어 커널이 왜 그 모양인지 따진다. ★계산은 순수, 읽기는 가장자리.

리포트는 판정을 낸다. "물리로 보기 어렵다" 를 받은 사람의 다음 질문은 셋이고,
셋 다 답이 다른 행동으로 이어진다.

    1. 애초에 신호가 있나        기대 중심 탭과 실제 중심 탭의 크기를 댄다
    2. k 가 좁아 잘린 것인가      k 를 바꿔가며 모양이 수렴하는지 본다
    3. 랭크 결손은 어디서 왔나    영공간 벡터를 zone 의 말로 옮긴다

셋 다 덤프 CSV 만으로 답한다. 원본이 없어도 되고 사내 PC 밖으로 아무것도
안 나간다 - 이 저장소의 데이터가 그럴 수밖에 없다(CLAUDE.md).

1번이 이 도구의 핵심이다. 커널 중심이 0.003 일 때 그것이 "신호가 없다" 인지
"gain 이 원래 그만큼 작다" 인지는 커널만 봐서는 알 수 없다. 같은 데이터에서
동특성이 이미 답을 갖고 있다 - 최종 변화량을 그때의 |Δgap| 으로 나누면 중심
탭이 있어야 할 자리가 나온다. 둘이 자릿수로 맞으면 크기는 맞은 것이고 남은
문제는 모양뿐이다.
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from app.coating import console
from app.coating import features
from app.coating import response as resp
from app.coating import schemas as S
from app.coating.model import profile

# 영공간으로 볼 특이값 문턱(최대 특이값 대비). rank_diagnostics 의 기계 정밀도
# 문턱보다 느슨하다 - 여기서 찾는 것은 "정확히 0" 이 아니라 "사실상 묶여 있다" 다.
_NULL_RATIO = 1e-6
# 영공간 벡터에서 이 비중을 넘는 zone 만 사람에게 보인다. 나머지는 반올림 먼지다.
_ZONE_WEIGHT = 0.15
# k 스윕 기본 범위. 25 zone 에서 k=6 이면 13탭이라 이보다 넓힐 이유가 없다.
DEFAULT_WIDTHS = (1, 2, 3, 4, 5, 6)
# 줄바꿈. 이 모듈의 출력은 전부 줄 목록을 이어 붙여 만든다.
LF = "\n"


def expected_center_tap(aligned: pd.DataFrame, curve: pd.DataFrame) -> dict:
    """동특성이 이미 아는 것으로 중심 탭의 기대 크기를 낸다. ★순수

    최종 변화량은 |Δgap| 1회분이 만든 ΔWet 이다. 그것을 그때의 |Δgap| 으로
    나누면 gap 1단위당 몫, 곧 커널 중심 탭이 있어야 할 크기가 된다.

    이웃으로 새는 몫이 있으므로 이 값은 **상한**이다. 중심이 이보다 크면 그게
    이상한 것이고, 자릿수 아래로 작으면 프로파일 경로에서 신호를 흘린 것이다.

    Δgap 은 (이벤트, zone) 마다 한 번씩만 센다. aligned 는 lag 축으로 같은
    Δgap 을 수십 번 반복해 담고 있어서, 그대로 중앙값을 내면 관측 창 길이가
    통계에 섞인다.
    """
    out = {"final": None, "median_abs_dgap": None, "expected": None,
           "n_pairs": 0, "tail_sem": None, "significant": False}
    if aligned.empty or curve.empty:
        return out
    final = resp.dynamics(curve, sigma=0.0).get("final")
    uniq = aligned.groupby([S.EVENT, S.ZONE])[resp.D_GAP].first()
    med = float(np.nanmedian(np.abs(uniq.to_numpy(dtype=float)))) if len(uniq) else None
    out.update(final=final, median_abs_dgap=med, n_pairs=int(len(uniq)))

    # final 은 뒤쪽 1/4 의 평균일 뿐 유의성 검정을 통과한 값이 아니다. 노이즈
    # 수준의 final 로 기대 탭을 만들면, 노이즈를 노이즈에 견주고 "크기가 맞다"
    # 는 결론이 나온다 - 실측에서 실제로 그렇게 나왔다.
    post = curve[curve[resp.LAG] >= 0]
    if len(post):
        q = max(1, len(post) // 4)
        sem = float(post.tail(q)["sem"].mean())
        out["tail_sem"] = sem
        if final is not None and np.isfinite(final) and sem > 0:
            out["significant"] = bool(abs(final) >= 2.0 * sem)
    if final is not None and med and np.isfinite(final) and med > 0:
        out["expected"] = float(abs(final) / med)
    return out


def sweep_kernels(
    delta_gap: np.ndarray,
    delta_wet: np.ndarray,
    alpha: float,
    widths=DEFAULT_WIDTHS,
) -> list[dict]:
    """k 를 바꿔가며 커널을 다시 뽑는다. ★순수

    잘림이면 k 를 늘릴수록 가장자리 비가 떨어지고 판정이 뒤집힌다. 노이즈면
    k 와 무관하게 모양이 계속 흔들린다 - 이 둘을 눈으로 가르려는 것이다.
    """
    rows = []
    for k in widths:
        if 2 * k + 1 > delta_gap.shape[1]:
            continue
        try:
            kernel = profile.fit_kernel(delta_gap, delta_wet, k, alpha)
        except (np.linalg.LinAlgError, ValueError):
            continue
        d = profile.kernel_diagnostics(kernel)
        se = profile.kernel_standard_errors(delta_gap, delta_wet, k, alpha)
        res = profile.shape_is_resolvable(kernel, se)
        rows.append({
            "k": k,
            "se": se.tolist(),
            "se_center": float(se[k]),
            "resolvable": res["resolvable"],
            "center_snr": res["center_snr"],
            "gap_snr": res["neighbour_gap_snr"],
            "resolve_reason": res["reason"],
            # 봉우리가 어느 offset 에 섰나. k 를 넓혀도 같은 자리에 계속 서면
            # 그것은 노이즈가 아니라 zone 대응이 그만큼 밀렸다는 뜻이다.
            "peak_offset": int(np.argmax(np.abs(kernel))) - k,
            "center": d["center"],
            "peak": float(kernel[int(np.argmax(np.abs(kernel)))]),
            "plausible": d["plausible"],
            "edge_ratio": d["edge_ratio"],
            "asymmetry": d["asymmetry"],
            "mass_ratio": d["mass_ratio"],
            "kernel": d["kernel"],
        })
    return rows


def peak_offset_consensus(rows: list[dict], min_k: int = 3) -> int | None:
    """넓은 k 들이 같은 offset 을 가리키면 그 값을 준다. ★순수

    k=2 는 창이 좁아 봉우리가 밖에 있어도 안 보인다. 그래서 넓은 k 만 본다.
    이들이 0 이 아닌 같은 자리를 가리키면 밀림이고, 제각각이면 노이즈다.
    """
    wide = [r["peak_offset"] for r in rows if r["k"] >= min_k]
    if len(wide) < 2:
        return None
    return wide[0] if len(set(wide)) == 1 and wide[0] != 0 else None


def null_space_zones(delta_gap: np.ndarray) -> list[dict]:
    """랭크 결손을 만드는 열 조합을 찾아 zone 의 말로 옮긴다. ★순수

    "설명 안 된 결손 1" 은 조정된 열들 사이에 정확한 선형 관계가 하나 있다는
    뜻이다. 가장 흔한 형태가 총량 보존이다 - 어딘가를 열면 어딘가를 같은 만큼
    닫는 습관이면 그 zone 들의 가중합이 늘 일정해지고, 그러면 gap 의 레벨 성분은
    이 데이터에 아예 들어 있지 않다. 모양은 배울 수 있어도 전체를 얼마나 올려야
    하는지는 못 배운다는 뜻이라, 알고 넘어가는 것과 모르고 넘어가는 것이 다르다.

    한 번도 안 움직인 zone 은 빼고 본다 - 그것은 이미 설명된 결손이라, 같이
    두면 자명한 관계가 목록을 채워 진짜를 덮는다.
    """
    dg = np.nan_to_num(np.asarray(delta_gap, dtype=float))
    if dg.size == 0 or dg.ndim != 2:
        return []
    moved = np.abs(dg).sum(axis=0) > 0
    zones = [z + 1 for z in range(dg.shape[1]) if moved[z]]
    sub = dg[:, moved]
    if sub.shape[1] < 2:
        return []

    _, sv, vt = np.linalg.svd(sub, full_matrices=True)
    tol = sv.max() * _NULL_RATIO if sv.size else 0.0
    # 특이값이 아예 없는 축(열이 행보다 많을 때)도 영공간이다.
    dead = [i for i in range(vt.shape[0]) if i >= len(sv) or sv[i] <= tol]

    out = []
    for i in dead:
        v = vt[i]
        peak = float(np.abs(v).max())
        if peak == 0:
            continue
        members = [
            {"zone": zones[j], "weight": float(v[j] / peak)}
            for j in range(len(zones))
            if abs(v[j]) >= _ZONE_WEIGHT * peak
        ]
        signs = {np.sign(m["weight"]) for m in members}
        out.append({
            "members": members,
            "n_members": len(members),
            # 부호가 한쪽뿐이면 "이 zone 들의 가중합이 늘 일정" = 총량 보존형이다.
            "conserving": len(members) > 2 and len(signs) == 1,
        })
    return out


def render(tables: dict, alpha: float) -> str:
    """세 절을 순서대로 낸다. 크기 → 모양 → 구조.

    순서가 뜻을 만든다. 크기가 자릿수로 틀렸으면 k 를 아무리 돌려도 소용없고,
    모양이 안 잡혔으면 랭크 결손을 봐야 할 이유도 아직 없다.
    """
    ds = tables["07_delta_samples"]
    dg = ds[features.GAP_DELTA_COLS].to_numpy(dtype=float)
    dw = ds[features.WET_DELTA_COLS].to_numpy(dtype=float)
    swept = sweep_kernels(dg, dw, alpha)

    lines = [
        "# 커널 진단 — 왜 그 모양인가",
        "",
        f"- 델타 샘플 {len(ds)}건 × {dg.shape[1]} zone · ridge α={alpha:g}",
        "",
        "## 1. 크기 — 중심 탭이 있어야 할 자리",
        "",
    ]
    exp = expected_center_tap(tables["05_aligned"], tables["06_response_curve"])
    at_k2 = next((r for r in swept if r["k"] == 2), None)
    if exp["expected"] is None or at_k2 is None:
        lines += ["- 05_aligned·06_response_curve 가 없거나 비어 대조할 수 없다.", ""]
    elif not exp["significant"]:
        # 여기서 비율을 내면 노이즈를 노이즈에 견주게 된다.
        lines += [
            f"- 동특성 최종 변화량 {exp['final']:+.4f} (뒤쪽 구간 SEM"
            f" {exp['tail_sem']:.4f}) — **2σ 를 못 넘는다**",
            "- **대조 불가.** 기준으로 쓸 반응 자체가 관측되지 않았다. 이 값으로"
            " 기대 탭을 만들면 노이즈를 노이즈에 견주는 셈이라, 어떤 비율이 나와도"
            " 뜻이 없다. 동특성 절이 '지연 추정 불가' 라면 1절은 건너뛰고 2절만 본다.",
            "",
        ]
    else:
        ratio = abs(at_k2["center"]) / exp["expected"]
        lines += [
            f"- 동특성 최종 변화량 {exp['final']:+.4f} ÷ |Δgap| 중앙값 "
            f"{exp['median_abs_dgap']:.4g}",
            f"  → 기대 중심 탭 ≈ {exp['expected']:.4g} (이웃으로 새는 몫이 있으니 **상한**)",
            f"- 실제 중심 탭 {at_k2['center']:+.4g} (k=2) → 기대 대비 {ratio:.0%}",
        ]
        if exp["final"] * at_k2["center"] < 0:
            lines.append(
                "- ⚠ **부호가 반대다.** 동특성은 한쪽, 커널은 다른 쪽을 가리킨다."
                " 둘 중 하나는 반응이 아니다 - 크기 비교보다 이것이 먼저다."
            )
        lines += ["", _scale_verdict(ratio), ""]

    lines += [
        "## 2. k 스윕 — 잘림인가 노이즈인가",
        "",
        "     k       중심        ±SE   중심σ  이웃차σ  봉우리  비대칭  판정",
    ]
    for r in swept:
        if not r["resolvable"]:
            mark = "판정 불가"
        elif r["plausible"]:
            mark = "종 모양"
        else:
            mark = "물리 아님"
        lines.append(
            f"    {r['k']:>2}  {r['center']:+9.4g}  {r['se_center']:9.4g}"
            f"  {r['center_snr']:>5.1f}  {r['gap_snr']:>6.1f}"
            f"  {r['peak_offset']:>+6d}  {r['asymmetry']:>6.2f}  {mark}"
        )
    lines += ["", _sweep_verdict(swept), ""]

    lines += ["## 3. 랭크 결손의 정체", ""]
    nulls = null_space_zones(dg)
    if not nulls:
        lines.append("- 조정된 열 사이에 선형 관계가 없다. 결손은 전부 설명됐다.")
    for i, n in enumerate(nulls, 1):
        zs = ", ".join(f"z{m['zone']}({m['weight']:+.2f})" for m in n["members"])
        lines.append(f"- 관계 {i}: zone {n['n_members']}개 — {zs}")
        if n["conserving"]:
            lines.append(
                "  - 부호가 한쪽뿐이다 = 이 zone 들의 가중합이 늘 일정하다."
                " 총량을 지키며 배분만 바꾸는 습관이라면 gap 의 **레벨 성분이 이"
                " 데이터에 없다**. 모양은 배울 수 있어도 전체를 얼마나 올릴지는"
                " 못 배운다 - 레벨은 스칼라 제어값(RPM·BP)이 맡아야 한다."
            )
        else:
            lines.append(
                "  - 부호가 섞였다 = 한쪽을 열면 다른 쪽을 닫는 맞교환이다."
                " 이 zone 들의 개별 기여는 이 데이터로 가를 수 없다."
            )
    return "\n".join(lines) + "\n"


def _scale_verdict(ratio: float) -> str:
    """크기 판정. 셋의 다음 행동이 전부 다르므로 문장도 셋으로 가른다."""
    if ratio >= 0.3:
        return (
            "- **크기는 맞다.** 신호를 흘린 것이 아니라 gain 이 원래 이 크기다."
            " 남은 문제는 모양뿐이므로 2번으로 간다."
        )
    if ratio >= 0.05:
        return (
            "- **크기가 부족하다.** 이웃으로 새는 몫을 감안해도 차이가 크다."
            " k 를 넓혀 새는 몫을 담아 본다(2번)."
        )
    return (
        "- **크기가 자릿수로 다르다.** 동특성은 반응을 보는데 커널은 못 본다는"
        " 뜻이라, 데이터가 아니라 프로파일 경로를 의심한다. 이 절을 그대로 알려"
        " 주면 delta_samples 의 Δgap·ΔWet 짝을 함께 본다."
    )


def _sweep_verdict(rows: list[dict]) -> str:
    if not rows:
        return "- 커널을 하나도 못 뽑았다."
    # 모양을 판정할 힘이 없으면 모양에 대해 아무 말도 하지 않는다. "물리 아님"
    # 과 "판정 불가" 는 다음 행동이 정반대다 - 앞은 모델을, 뒤는 표본을 고친다.
    if not any(r["resolvable"] for r in rows):
        worst = min(rows, key=lambda r: r["gap_snr"] if np.isfinite(r["gap_snr"]) else 0)
        return (
            "- **어느 k 에서도 모양을 판정할 힘이 없다.** "
            + worst["resolve_reason"]
            + " 위 표의 '물리 아님' 은 커널이 틀렸다는 뜻이 아니라 이 표본으로는"
            " 종 모양인지 아닌지를 말할 수 없다는 뜻이다. 모델이 아니라 표본을"
            " 고쳐야 한다 — **조정 폭을 키우는 것이 이벤트를 늘리는 것보다 훨씬"
            " 싸다**(필요 표본은 폭의 제곱에 반비례한다)."
        )
    shift = peak_offset_consensus(rows)
    if shift is not None:
        return (
            f"- **넓은 k 가 모두 봉우리를 offset {shift:+d} 에 세운다 = zone 대응이"
            f" {abs(shift)}칸 밀렸다.** 노이즈는 k 마다 다른 자리를 가리키므로 이것은"
            " 노이즈가 아니다. T_Block 볼트와 Wet 측정기가 물리적으로 어긋나 있거나,"
            " MES 항목 번호가 통념과 다르게 매겨진 것이다. 설비에서 1번 볼트와 1번"
            " 측정기가 같은 자리인지 확인한다 — 데이터가 아니라 설비 도면의 문제다."
        )
    ok = [r for r in rows if r["plausible"]]
    if ok:
        best = min(ok, key=lambda r: r["edge_ratio"])
        return (
            f"- **k={best['k']} 에서 종 모양이 된다.** `backend/.env` 의"
            f" COATING_KERNEL_HALF_WIDTH={best['k']} 로 두고 리포트를 다시 돌린다."
        )
    edges = [r["edge_ratio"] for r in rows]
    if len(edges) > 1 and edges[-1] < edges[0] * 0.6:
        return (
            "- k 를 늘릴수록 가장자리 비가 떨어진다 = **잘림 쪽**이다. 아직 종"
            " 모양은 아니므로 더 넓은 k 가 필요하거나 표본이 모자란 것이다."
        )
    return (
        "- **k 와 무관하게 모양이 흔들린다 = 노이즈 쪽**이다. 프로파일 성분이"
        " 노이즈에 묻혔다는 뜻이다. COATING_RIDGE_ALPHA 를 올려 모양을 눌러 보고,"
        " 그래도 안 되면 이벤트를 더 모으는 것 외에 방법이 없다."
    )


def load_dump(dump_dir) -> dict:
    """덤프 폴더에서 필요한 표만 읽는다. ★파일을 만지는 유일한 곳.

    실패는 문장으로 낸다. 트레이스백을 던지면 사용자가 돌려줄 수 있는 것이
    스택뿐인데, 그것으로는 아무 판단도 못 한다(report·merge 와 같은 관례).
    """
    d = Path(dump_dir)
    if not d.is_dir():
        raise SystemExit(
            f"덤프 폴더가 없다: {d}\n"
            "  리포트를 --dump 로 다시 돌린다:\n"
            "    python -m app.coating.report --input <parquet> --dump"
        )
    out = {}
    for name in ("05_aligned", "06_response_curve", "07_delta_samples"):
        p = d / f"{name}.csv"
        out[name] = pd.read_csv(p) if p.exists() else pd.DataFrame()
    if out["07_delta_samples"].empty:
        raise SystemExit(
            f"07_delta_samples.csv 가 없거나 비었다: {d}\n"
            "  이 표가 커널의 입력이다. 예전 코드는 남기지 않았으니 최신 코드로\n"
            "  리포트를 다시 돌린다 (git pull 후 --dump)."
        )
    return out


def render_preprocess(path, s) -> str:
    """원본에서 바로 전처리 전 과정을 낸다. ★파일을 읽는 두 번째 가장자리.

    리포트 전체를 돌리지 않고도 "무엇이 어디서 걸러졌나" 를 보는 경로다. 창을
    정하는 일은 이 출력을 몇 번 들여다보는 일이라, 그때마다 파이프라인 전체를
    도는 것은 비싸다.

    사용자가 돌려줄 것은 §0·§3·§4 세 덩어리뿐이다(docs/measurements 의 전환
    확인 howto 참고). 나머지는 혼자 판정하는 데 쓴다.

    절마다 `_section` 으로 감싼다. 한 절이 죽어도 나머지 절은 이미 계산됐고
    보여줄 수 있다 - 예외 하나가 §0~§n 전체를 삼키면, 계산은 됐는데 아무것도
    못 돌려주는 것이라 트레이스백을 던지는 것과 실질적으로 같은 실패다
    (CLAUDE.md: "진단은 문장으로 출력되게 하고(트레이스백 금지)").
    """
    from app.coating import (
        events as ev_mod, panel as panel_mod, parse, pivot,
        response as resp_mod, segment, trace,
    )

    fmt = parse.format_for(path, None, s.coating_input_format)
    readings = parse.load_readings(path, parse.DEFAULT_DICT_PATH, source=fmt)
    deduped = pivot.dedupe_minute(readings)
    changes = pivot.compress_runs(deduped)
    bounds = segment.lot_bounds(deduped)
    ev, dl = ev_mod.build_events(changes, s.coating_event_merge_minutes)
    iso = ev_mod.isolation(
        ev, s.coating_isolation_pre_minutes, s.coating_isolation_post_minutes, bounds
    )

    lines = [
        "# 전처리 — 무엇이 어디서 걸러졌나",
        "",
        f"- lot {len(bounds)}개",
        f"- 묶음 규칙: 앵커 {s.coating_event_merge_minutes}분"
        f" (한 묶음의 폭이 이 값을 넘지 못한다)",
        f"- 격리 규칙: 앞{s.coating_isolation_pre_minutes}분 /"
        f" 뒤{s.coating_isolation_post_minutes}분, 묶음의 **끝**에서 잰다",
        "",
        "이 판정은 Wet 을 보지 않는다. 이벤트 시각만으로 정해지므로 L 을 몰라도",
        "쓸 수 있고, 그래서 L 을 재는 데 쓸 수 있다.",
        "",
    ]
    lines += _window_invariant_lines(s)
    lines += _section(
        "0. 어디서 얼마나 줄었나",
        lambda: _funnel_lines(trace.funnel(readings, deduped, changes, iso)),
    )
    lines += _section(
        "1. 묶음 원장",
        lambda: _ledger_lines(trace.event_ledger(iso, dl)),
    )
    lines += _section(
        "2. 타임라인",
        lambda: _timeline_lines(*trace.timeline(iso, bounds)),
    )
    lines += _section(
        "3. 구/신 규칙 대조",
        lambda: _comparison_lines(trace.rule_comparison(
            iso, dl, bounds,
            _LEGACY_ISOLATION_PRE_MINUTES, _LEGACY_ISOLATION_POST_MINUTES,
            s.coating_isolation_pre_minutes, s.coating_isolation_post_minutes,
        )),
    )
    lines += _section(
        f"4. {s.coating_response_post_minutes}분 창이 반응을 담고 있나",
        lambda: _window_check_lines(deduped, iso, dl, s, panel_mod, resp_mod),
    )
    lines += _section(
        "5. 격리 창을 바꿔가며",
        lambda: _isolation_window_lines(ev, iso, bounds, ev_mod),
    )
    lines += _section(
        "6. 변경 사이의 간격 분포",
        lambda: _gap_histogram_lines(changes, ev_mod),
    )
    lines += _section(
        "7. 병합창 민감도",
        lambda: _merge_sensitivity_lines(changes, bounds, s, ev_mod),
    )
    return LF.join(lines) + LF


def _section(name: str, builder) -> list[str]:
    """섹션 하나를 계산한다. 실패해도 나머지 절은 영향받지 않는다.

    render_preprocess 는 절마다 나온 줄을 리스트에 계속 이어 붙이고 마지막에
    한 번만 join 한다. 이 래퍼가 없으면 한 절의 계산에서 난 예외가 그 전까지
    이미 계산해 둔 앞 절들까지 통째로 삼킨다. 실측에서 이 자리가 실제로
    터졌다 - Wet zone 이 통째로 결측인 lot 에서 §4 가 all-NaN Series 에
    idxmax 를 불러 ValueError 를 던졌고, 그 순간 §0~§3 이 전부 사라졌다.
    """
    try:
        return builder()
    except Exception as e:  # noqa: BLE001 - 사용자에게 문장으로 돌려줘야 한다
        return [
            f"## {name}",
            "",
            f"   ⚠ 이 절을 만들다 실패했다: {type(e).__name__}: {e}",
            "   (다른 절은 이 실패와 무관하게 계산된다 - 위아래 절을 계속 본다.)",
            "",
        ]


def _window_invariant_lines(s) -> list[str]:
    """뒤 격리 = 응답창 불변식이 깨졌는지 매 실행마다 스스로 검사한다.

    이 불변식은 test_config.py 에서만 검증돼 있었다 - .env 를 손으로 고치는
    사람에게는 아무 도움이 안 된다. 하나만 올리면 정확히 이 문서가 없애려던
    버그가 재발한다: 뒤 10분만 조용함을 보장하는데 20분을 응답으로 읽으면,
    그 10분 중 절반은 다른 조정이 섞여 있어도 통과한다. 예외로 죽이지 않는다
    - 실행을 막을 이유는 없고, 사람이 보고 판단하면 된다.
    """
    if s.coating_isolation_post_minutes == s.coating_response_post_minutes:
        return []
    return [
        f"⚠ 뒤 격리(COATING_ISOLATION_POST_MINUTES={s.coating_isolation_post_minutes}분)"
        f" ≠ 응답창(COATING_RESPONSE_POST_MINUTES={s.coating_response_post_minutes}분)."
        " 격리가 보장하는 조용한 구간보다 더 긴 구간을 응답으로 읽고 있다는 뜻이다"
        " - 그 초과분에는 다른 조정이 섞여 있어도 걸러지지 않는다. 둘을 같은 값으로"
        " 맞춘다.",
        "",
    ]


# --isolation 은 별칭으로 남긴다. 기존 문서와 손버릇이 깨지지 않게.
render_isolation = render_preprocess


# 구 규칙(연쇄 병합·시작 기준)의 옛 창. 실측값이 아니라 이 저장소가 예전에
# 쓰던 상수라, 이름을 붙여야 "누가 방금 입력한 숫자" 와 구별된다.
_LEGACY_ISOLATION_PRE_MINUTES = 30
_LEGACY_ISOLATION_POST_MINUTES = 60


def _funnel_lines(f) -> list[str]:
    """단위가 바뀌는 줄은 Δ 를 숨긴다.

    trace.funnel 은 그 자리에 파이썬 None 을 넣지만, int 와 섞인 열을 DataFrame
    에 실으면 pandas 가 조용히 float64 로 승격시키며 None 을 NaN 으로 바꾼다
    (`r.delta is None` 은 그래서 절대 참이 안 된다 - events.isolation 이 같은
    함정을 dtype=object 로 피하는 이유와 같다). `pd.isna` 로 검사하면 두 표현
    모두 잡는다.
    """
    out = ["## 0. 어디서 얼마나 줄었나", ""]
    for r in f.itertuples(index=False):
        d = "" if pd.isna(r.delta) else f"  ({r.delta:+,.0f})"
        note = f"   {r.note}" if r.note else ""
        out.append(f"   {r.stage:<20} {r.n:>10,}{d}{note}")
    return out + [""]


def _ledger_lines(led, head: int = 20) -> list[str]:
    out = [
        f"## 1. 묶음 원장  ({len(led)}건 중 앞 {min(head, len(led))}"
        " · 전체는 12_event_ledger.csv — `python -m app.coating.report --dump`"
        " 가 남긴다. 이 명령(--preprocess)은 파일을 쓰지 않는다)",
        "",
        "   event         run   첫 변경           마지막   span  n  중복"
        "   앞간격   뒤간격  판정",
    ]
    if led.empty:
        return out + ["   (조정 이벤트가 0건이다)", ""]
    for r in led.head(head).itertuples(index=False):
        run = getattr(r, S.RUN) or "-"
        first = getattr(r, S.AT)
        last = getattr(r, S.LAST_AT)
        dup = f"⚠{r.n_dup_zones}" if r.n_dup_zones else "-"
        gb = "  없음" if pd.isna(r.gap_before) else f"{r.gap_before:6.1f}"
        ga = "  없음" if pd.isna(r.gap_after) else f"{r.gap_after:6.1f}"
        verdict = "✓" if r.isolated else f"✗ {getattr(r, S.ISO_REASON)}"
        out.append(
            f"   {getattr(r, S.EVENT):<13} {run:<5} {first:%m-%d %H:%M:%S}"
            f"  {last:%H:%M:%S} {getattr(r, S.SPAN):5.1f} {r.n_items:>2}"
            f"  {dup:>3} {gb} {ga}  {verdict}"
        )
    out += ["", "   zone 별 Δ 는 CSV 의 zones 열에 있다.", ""]
    return out


def _timeline_lines(mpc, rows) -> list[str]:
    out = [
        f"## 2. 타임라인  (─ 조용  ✓ 격리통과  ✗ 탈락  1칸 = {mpc:.0f}분)",
        "",
    ]
    if not rows:
        # bounds 가 비면(lot 이 0개) 그릴 lot 자체가 없다 - 헤더만 찍고 끝내면
        # "재서 0 이다" 와 "이 절은 원래 비어 있다" 가 안 갈린다.
        return out + ["   (lot 이 0개라 그릴 것이 없다)", ""]
    for r in rows:
        out.append(
            f"   {r[S.LOT]:<12} {r['start']:%m-%d %H:%M} ├{r['cells']}┤"
            f" {r['end']:%H:%M}"
        )
        for run in r["runs"]:
            pad = " " * (len(f"   {r[S.LOT]:<12} ") + 12)
            out.append(f"{pad}└─ {run[S.RUN]}: 조각 {run['n']}개")
    return out + [""]


def _comparison_lines(c) -> list[str]:
    """창 값은 c 에서 읽는다. 헤더에 30/60 을 박아 두면 다른 창으로 부른 날
    제목만 옛 숫자를 말하는 거짓말이 된다."""
    o, n = c["old"], c["new"]
    out = [
        f"## 3. 구 규칙(연쇄·시작 기준·앞{o['pre']}/뒤{o['post']}) 대비"
        f" — 신 규칙은 앵커·끝 기준·앞{n['pre']}/뒤{n['post']}",
        "",
        "                     구       신",
        f"   묶음          {o['n_clusters']:>6}  {n['n_clusters']:>6}",
        f"   격리 통과     {o['n_isolated']:>6}  {n['n_isolated']:>6}",
        f"   Δgap 항목     {o['n_items']:>6}  {n['n_items']:>6}",
        "",
        f"   양쪽 통과 {c['both']}건 · 신에만 {c['only_new']}건"
        f" · 구에만 {c['only_old']}건   (연속 구간 {c['n_runs']}개 중,"
        " 어느 쪽도 통과 못 한 구간은 어디에도 안 든다)",
    ]
    if c["only_old_examples"]:
        out += ["", "   구에만 남은 것 = 연쇄가 삼켰던 긴 구간:"]
        for e in c["only_old_examples"]:
            out.append(
                f"     {e['run']}  구 = {e['first']:%m-%d %H:%M}~{e['last']:%H:%M}"
                f" 한 건 / 신 = 조각 {e['n_fragments']}개"
                f" · 항목 손질 {e['n_item_touches']}회"
            )
    return out + [""]


def _window_check_lines(deduped, iso, dl, s, panel_mod, resp_mod) -> list[str]:
    """응답창이 반응을 담고 있나. 10분은 실측이 아니라 가정이다.

    이 절이 검사하는 것은 "곡선의 최댓값이 마지막 lag 에 섰는가" 하나가
    아니다. 그것만 보면 놓치는 실패 양식이 하나 더 있다 - 순수지연이 창보다
    길면 관측한 0~10분 구간 전부가 순수 노이즈이고, 그때 argmax 는 11칸에
    균등분포한다. Monte Carlo 로 재보면 이 경우에도 "마지막 칸이 아니다" 가
    표본 수와 무관하게 약 90.9% 의 확률로 나온다 - 표본을 늘려도 사라지지
    않는 거짓 ✓ 다. 그래서 판정을 셋으로 가른다.

      가장자리에서 상승   최댓값이 마지막 lag 에 섰다 → 창이 짧아 τ 를 못 담는다.
      노이즈 아래         최댓값이 자기 표준오차(SE)의 _SIGMA_K(=2)배를 못 넘는다
                          → 이 창 안에서는 반응이 검출되지 않았다(순수지연이
                          창보다 길거나 반응이 검출 한계 아래이고, 곡선만으로는
                          둘을 못 가른다).
      안쪽 · 노이즈 위    ✓.

    peak_lag 가 0·1 이면 셋째 판정을 통과했어도 별도로 경고한다 - 순수지연이
    있는 계단 응답이 lag 0~1 에서 이미 봉우리에 서는 것은 물리적으로 불가능하다.
    """
    post = s.coating_response_post_minutes
    out = [f"## 4. {post}분 창이 반응을 담고 있나", ""]
    usable = iso[iso["isolated"]] if len(iso) else iso
    if not len(usable):
        return out + ["   격리를 통과한 이벤트가 0건이라 판정할 수 없다.", ""]
    p = panel_mod.build_panel(deduped, s.coating_panel_ffill_max_minutes)
    # baseline_minutes 는 report._dynamics_facts 와 같은 coating_settle_window_minutes
    # 를 써야 한다. 다른 창(예전에는 coating_delta_window_minutes)을 쓰면 이 절이
    # 검증하는 곡선이 리포트가 실제로 쓰는 곡선과 달라져서, §4 가 ✓ 를 찍어도
    # 리포트의 L·τ 는 다른 기준선에서 나온 값이 된다.
    aligned = resp_mod.align_events(
        p, usable, dl, s.coating_response_pre_minutes, post,
        s.coating_settle_window_minutes,
    )
    curve = resp_mod.response_curve(aligned)
    fwd = curve[curve[resp_mod.LAG] >= 0] if len(curve) else curve
    if not len(fwd) or not fwd["mean"].notna().any():
        # all-NaN Series 에 idxmax 를 부르면 pandas 3.0.5 에서
        # "ValueError: Encountered all NA values" 다 - 조정한 zone 의 Wet 이
        # 이 창에서 통째로 결측이면(features.wet_wide 가 0 을 결측으로 마스킹
        # 하므로 "0으로 기록된 zone" 도 여기 해당한다) 실제로 일어난다. 여기서
        # 막아 문장으로 낸다 - 트레이스백은 사용자가 돌려줄 수 있는 것이 스택뿐이다.
        return out + [
            "   정렬된 응답이 없다. 표본이 모자라거나(격리 통과 건수를 §0 에서",
            "   본다), 이 창의 모든 lag 에서 Wet 이 결측이다 - 조정된 zone 의 Wet 이",
            "   이 lot 에서 통째로 미측정(0)이었을 수 있다.",
            "",
        ]
    peak_row = fwd.loc[fwd["mean"].idxmax()]
    peak_lag = int(peak_row[resp_mod.LAG])
    peak_mean = float(peak_row["mean"])
    peak_sem = float(peak_row["sem"])
    last_lag = int(fwd[resp_mod.LAG].max())
    sigma_k = resp_mod._SIGMA_K
    if not np.isfinite(peak_sem):
        sem_txt = "정의 안 됨(그 lag 의 표본이 1건뿐)"
        significant = False
    else:
        sem_txt = f"{peak_sem:.4f}"
        significant = (
            (peak_mean != 0) if peak_sem == 0 else (abs(peak_mean) >= sigma_k * peak_sem)
        )

    lines = [
        f"   정렬 응답 곡선 최댓값 lag = +{peak_lag}분 (관측 끝 +{last_lag}분)"
        f" — 평균 {peak_mean:+.4f} · SE {sem_txt} (기준 {sigma_k:g}×SE)",
    ]
    post_iso = s.coating_isolation_post_minutes
    post_resp = s.coating_response_post_minutes
    if peak_lag >= last_lag:
        # 마지막 lag 에서 최댓값이 서면 창이 짧다는 것만 알고 얼마나 짧은지는
        # 모른다 - 다음 표준 시도는 두 배로 넓혀 보는 것이다. 설정 이름과 함께
        # 지금 값 → 두 배 값을 그대로 적어야 읽는 사람이 더 고민하지 않고 바로
        # .env 를 고칠 수 있다("올린다" 만으로는 얼마나 올릴지 다시 판단해야 한다).
        lines += [
            "     ⚠ 가장자리에서 상승 — 창이 짧아 τ 를 못 담고 있을 가능성이 크다.",
            f"     다음 시도는 두 배: COATING_ISOLATION_POST_MINUTES 를"
            f" {post_iso} → {post_iso * 2}, COATING_RESPONSE_POST_MINUTES 를"
            f" {post_resp} → {post_resp * 2} 로 두고 다시 돌린다.",
        ]
    elif not significant:
        lines += [
            f"     ⚠ 노이즈 아래 — 최댓값이 자기 SE 의 {sigma_k:g}배를 못 넘는다."
            " 이 창 안에서는 반응이 검출되지 않았다.",
            "     뜻: 순수지연이 이 창보다 길거나, 반응 크기가 지금 표본의 검출"
            " 한계 아래다 - 곡선만으로는 이 둘이 갈리지 않는다.",
            "     먼저 해볼 것: 창을 늘려 다시 본다. 그래도 안 보이면 §0 의 격리"
            " 통과 건수를 늘리는 쪽(표본 부족)을 의심한다.",
        ]
    else:
        lines.append("     ✓ 창 안쪽에서, 노이즈 위로 반응이 잡혔다.")
        if peak_lag <= 1:
            lines.append(
                f"     ⚠ 다만 최댓값이 lag {peak_lag}에 섰다 — 순수지연이 있는"
                " 계단 응답에서는 물리적으로 있을 수 없는 자리다. 위 검정을"
                " 통과했어도 zone 대응이 밀렸거나 우연한 초과일 수 있다 -"
                " `python -m app.coating.diagnose --dump <덤프폴더>` 로"
                " 커널 진단(밀림 탐지)을 본다."
            )
    return out + lines + [""]


def _isolation_window_lines(ev, iso, bounds, ev_mod) -> list[str]:
    """§5 — 창을 고르는 근거 표 하나. 새 다섯 절 뒤에 그대로 유지한다."""
    lines = ["## 5. 격리 창을 바꿔가며", "", "   창(앞=뒤)   홀로 선 이벤트"]
    if ev.empty:
        return lines + ["   (조정 이벤트가 0건이다)", ""]
    table = ev_mod.isolation_table(ev, bounds=bounds)
    for r in table.itertuples(index=False):
        lines.append(
            f"   {r.post_minutes:>6}분   {r.n_isolated:>4} / {r.n_events}"
            f" ({r.ratio:.0%})"
        )
    n_now = int(iso["isolated"].sum()) if len(iso) else 0
    lines += ["", f"- 현재 설정 → **{n_now}건**", "",
              _isolation_verdict(table, n_now), ""]
    return lines


def _gap_histogram_lines(changes, ev_mod) -> list[str]:
    """§6 — 변경 사이의 간격 분포. `changes` 를 입력으로 쓴다 - 이벤트가
    0건이어도(§5 가 "0건" 을 적고 끝나도) 이 절은 계속 그릴 수 있다. 절이
    통째로 사라지면 "재서 0 이다" 와 "이 표는 원래 없다" 를 구별할 수 없다."""
    lines = ["## 6. 변경 사이의 간격 분포 — 병합창은 몇 분이어야 하나", ""]
    hist = ev_mod.gap_histogram(changes)
    peak = max(hist["n"]) or 1
    for r in hist.itertuples(index=False):
        bar = "█" * int(round(r.n / peak * 28))
        lines.append(f"   {r.bucket:>8}  {r.n:>5} ({r.ratio:>4.0%})  {bar}")
    lines += ["", _gap_verdict(hist), ""]
    return lines


def _merge_sensitivity_lines(changes, bounds, s, ev_mod) -> list[str]:
    """§7 — 병합창을 바꾸면 쓸 수 있는 것이 얼마나 달라지나. `changes` 입력."""
    lines = ["## 7. 병합창을 바꾸면 쓸 수 있는 것이 얼마나 달라지나", ""]
    sens = ev_mod.merge_sensitivity(
        changes,
        pre_minutes=s.coating_isolation_pre_minutes,
        post_minutes=s.coating_isolation_post_minutes,
        bounds=bounds,
    )
    lines.append("   병합창   묶음   격리 통과   쓸 수 있는 Δgap 항목")
    for r in sens.itertuples(index=False):
        mark = "  ← 현재" if r.merge_minutes == s.coating_event_merge_minutes else ""
        lines.append(
            f"   {r.merge_minutes:>4}분  {r.n_clusters:>5}   {r.n_isolated:>7}건"
            f"   {r.n_items:>14}개{mark}"
        )
    lines += ["", _merge_verdict(sens, s.coating_event_merge_minutes), ""]
    return lines


def _gap_verdict(hist: pd.DataFrame) -> str:
    """분포에 골이 있는가. 골이 곧 "여기부터는 다른 조정" 이라는 경계다.

    가장 긴 빈 구간을 찾는다. 첫 빈 칸만 보면 골이 여러 칸에 걸쳐 있을 때 그
    폭을 잃는데, 그 폭이 곧 "병합창을 이 사이 아무 값으로 둬도 된다" 는 여유다.
    """
    n = hist["n"].to_numpy()
    if n.sum() == 0:
        return "- 변경이 없어 분포를 낼 수 없다."
    labels = list(hist["bucket"])

    best, run_start = None, None
    for i in range(len(n) + 1):
        empty = i < len(n) and n[i] == 0
        if empty and run_start is None:
            run_start = i
        elif not empty and run_start is not None:
            # 양쪽에 값이 있어야 골이다. 앞뒤 끝의 빈 칸은 그냥 범위 밖이다.
            if n[:run_start].sum() > 0 and (i < len(n)):
                span = i - run_start
                if best is None or span > best[1] - best[0]:
                    best = (run_start, i)
            run_start = None

    if best is None:
        return (
            "- 뚜렷한 골이 없다. 조정 간격이 연속적으로 퍼져 있어 '한 번의 튜닝' 을"
            " 간격만으로 가르기 어렵다는 뜻이다 — 아래 7번 표에서 쓸 수 있는 항목이"
            " 가장 많아지는 창을 고른다."
        )
    lo, hi = _lower(labels[best[0]]), _lower(labels[best[1]])
    return (
        f"- **{lo}~{hi}분 구간이 비어 있다.** 그 아래는 한 번의 튜닝 안에서 볼트를"
        " 옮겨 잡은 간격이고, 그 위는 튜닝과 튜닝 사이의 간격이라는 뜻이다."
        f" 병합창을 이 사이 아무 값으로 둬도 결과가 같다 - 실제로 무엇이 최선인지는"
        " 아래 7번 표가 정한다."
    )


def _lower(label: str) -> str:
    """'3~5분' → '3'. 칸 라벨에서 아래쪽 경계만 꺼낸다."""
    return label.replace("분+", "").replace("분", "").split("~")[0]


def _merge_verdict(sens: pd.DataFrame, current: int) -> str:
    """지금 창이 얼마나 손해인지 한 문장으로."""
    if sens.empty or sens["n_items"].max() == 0:
        return "- 어느 병합창에서도 쓸 수 있는 이벤트가 없다."
    best = sens.loc[sens["n_items"].idxmax()]
    now = sens[sens["merge_minutes"] == current]
    n_now = int(now["n_items"].iloc[0]) if len(now) else 0
    if int(best["merge_minutes"]) == current:
        return f"- **현재 {current}분이 최선이다.** 쓸 수 있는 Δgap 항목 {n_now}개."
    return (
        f"- **{int(best['merge_minutes'])}분으로 넓히면 Δgap 항목이 {n_now}개 →"
        f" {int(best['n_items'])}개로 는다.** 지금 창이 한 번의 튜닝을 여러 묶음으로"
        " 쪼개고, 쪼개진 것들이 서로의 이웃이 되어 격리에서 함께 탈락하고 있다."
        " 데이터가 없는 것이 아니라 우리가 버리고 있다 —"
        f" COATING_EVENT_MERGE_MINUTES 를 {int(best['merge_minutes'])} 로 두고 다시 본다."
    )


def _isolation_verdict(table: pd.DataFrame, n_current: int) -> str:
    """표를 읽어 다음 행동을 한 문장으로 만든다.

    표만 주면 "그래서 뭘 하라는 건가" 가 남는다. 셋으로 갈리고 각각 할 일이 다르다.
    """
    if n_current > 0:
        return (
            f"- **{n_current}건으로 시작할 수 있다.** 동특성(L·τ·T_s)을 이 이벤트들로"
            " 재고, 나온 L+T_s 를 격리 창으로 다시 써서 표본을 늘린다."
        )
    alive = table[table["n_isolated"] > 0]
    if alive.empty:
        return (
            "- **어느 창에서도 홀로 선 이벤트가 없다.** 조정이 쉼 없이 이어졌다는"
            " 뜻이라 설정으로는 못 푼다. 조정 없이 2시간 이상 연속 운전한 구간이"
            " 포함된 데이터를 요청한다."
        )
    best = alive.iloc[0]
    return (
        f"- 현재 설정에서는 0건이지만 뒤 창을 {best['post_minutes']:.0f}분으로 낮추면"
        f" {best['n_isolated']:.0f}건이 산다. COATING_ISOLATION_POST_MINUTES 를"
        f" {best['post_minutes']:.0f}, PRE 를 {best['pre_minutes']:.0f} 로 두고"
        " 리포트를 다시 돌린다."
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m app.coating.diagnose",
        description="덤프를 되읽어 커널이 왜 그 모양인지 따진다.",
        epilog=(
            "예) 전처리 전 과정 — 리포트를 안 돌려도 된다\n"
            "    python -m app.coating.diagnose \\\n"
            "      --preprocess data/coating/raw/merged.parquet\n\n"
            "예) 커널 진단 — 리포트를 --dump 로 먼저 돌린다\n"
            "    python -m app.coating.diagnose \\\n"
            "      --dump data/coating/reports/dump/20260907-101500"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--preprocess", "--isolation", dest="preprocess_input",
        default=None, metavar="PARQUET",
        help="원본에서 전처리 전 과정을 낸다(깔때기·원장·타임라인·구신대조·창 검사)."
             " 리포트를 안 돌려도 된다. --isolation 은 별칭이다.",
    )
    p.add_argument(
        "--dump", dest="dump_dir", default=None, metavar="PATH",
        help="리포트가 --dump 로 남긴 폴더 (커널 진단용)",
    )
    p.add_argument(
        "--alpha", type=float, default=None,
        help="릿지 α 를 이번 실행에만 바꾼다 (생략 시 COATING_RIDGE_ALPHA)",
    )
    return p


def main(argv: list[str] | None = None) -> str:
    console.use_utf8()
    # 설정은 CLI 가 아무것도 안 줬을 때만 본다(.env 단일 출처).
    from app.config import get_settings

    args = build_parser().parse_args(argv)
    s = get_settings()
    if args.preprocess_input:
        text = render_preprocess(args.preprocess_input, s)
    elif args.dump_dir:
        alpha = args.alpha if args.alpha is not None else s.coating_ridge_alpha
        text = render(load_dump(args.dump_dir), alpha)
    else:
        # 둘 다 안 주면 무엇을 진단할지 모른다. 기본값을 정하지 않는 이유는
        # 하나가 원본을, 다른 하나가 파생물을 읽어서 값이 다른 것을 본다는 데 있다.
        raise SystemExit(
            "무엇을 진단할지 정해야 한다.\n"
            "  --preprocess <원본 parquet>   전처리 전 과정 (별칭: --isolation)\n"
            "  --dump <덤프 폴더>             커널 진단 (리포트를 --dump 로 먼저 돌린다)"
        )
    print(text)
    return text


if __name__ == "__main__":
    main()
