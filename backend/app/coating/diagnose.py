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
    out = {"final": None, "median_abs_dgap": None, "expected": None, "n_pairs": 0}
    if aligned.empty or curve.empty:
        return out
    final = resp.dynamics(curve, sigma=0.0).get("final")
    uniq = aligned.groupby([S.EVENT, S.ZONE])[resp.D_GAP].first()
    med = float(np.nanmedian(np.abs(uniq.to_numpy(dtype=float)))) if len(uniq) else None
    out.update(final=final, median_abs_dgap=med, n_pairs=int(len(uniq)))
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
        rows.append({
            "k": k,
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
    else:
        ratio = abs(at_k2["center"]) / exp["expected"]
        lines += [
            f"- 동특성 최종 변화량 {exp['final']:+.4f} ÷ |Δgap| 중앙값 "
            f"{exp['median_abs_dgap']:.4g}",
            f"  → 기대 중심 탭 ≈ {exp['expected']:.4g} (이웃으로 새는 몫이 있으니 **상한**)",
            f"- 실제 중심 탭 {at_k2['center']:+.4g} (k=2) → 기대 대비 {ratio:.0%}",
            "",
            _scale_verdict(ratio),
            "",
        ]

    lines += [
        "## 2. k 스윕 — 잘림인가 노이즈인가",
        "",
        "     k       중심   봉우리위치  가장자리비  비대칭  집중도  판정",
    ]
    for r in swept:
        mark = "종 모양" if r["plausible"] else "물리 아님"
        lines.append(
            f"    {r['k']:>2}  {r['center']:+9.4g}  {r['peak_offset']:>+9d}"
            f"  {r['edge_ratio']:>9.2f}  {r['asymmetry']:>6.2f}"
            f"  {r['mass_ratio']:>6.0%}  {mark}"
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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m app.coating.diagnose",
        description="덤프를 되읽어 커널이 왜 그 모양인지 따진다.",
        epilog=(
            "예) python -m app.coating.diagnose \\\n"
            "      --dump data/coating/reports/dump/20260907-101500"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--dump", dest="dump_dir", required=True, metavar="PATH",
        help="리포트가 --dump 로 남긴 폴더",
    )
    p.add_argument(
        "--alpha", type=float, default=None,
        help="릿지 α 를 이번 실행에만 바꾼다 (생략 시 COATING_RIDGE_ALPHA)",
    )
    return p


def main(argv: list[str] | None = None) -> str:
    # 설정은 CLI 가 아무것도 안 줬을 때만 본다(.env 단일 출처).
    from app.config import get_settings

    args = build_parser().parse_args(argv)
    alpha = args.alpha if args.alpha is not None else get_settings().coating_ridge_alpha
    text = render(load_dump(args.dump_dir), alpha)
    print(text)
    return text


if __name__ == "__main__":
    main()
