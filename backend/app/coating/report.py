"""판정 리포트 — v1 의 실제 산출물.

"모델이 얼마나 정확한가" 보다 "이 데이터로 학습이 가능한가" 가 먼저다.
관문은 둘이다.
  1차: 조정 이벤트가 존재하는가 (lot 당 gap 변경 횟수)
  2차: Δgap 의 유효 랭크가 충분한가

둘 중 하나라도 막히면 verdict = insufficient 를 내고, 무엇이 얼마나
부족한지를 사업부에 요구할 수 있는 형태로 적는다.
"""
import argparse
import html as html_mod
from pathlib import Path

import numpy as np
import pandas as pd

from app.coating import console
from app.coating import dump as dump_mod
from app.coating import evaluate
from app.coating import events as ev_mod
from app.coating import features, panel as panel_mod, parse, pivot, response
from app.coating import schemas as S
from app.coating import segment
from app.coating.model import profile
from app.config import get_settings

_MIN_EVENTS = 20  # Toeplitz 커널(2k+1개)을 견고하게 추정하는 하한
# 커널 절을 못 채울 때의 빈 값. 리포트는 어떤 경우에도 같은 절을 갖는다 -
# 절이 통째로 사라지면 "안 쟀다" 와 "재서 0 이다" 를 구별할 수 없다.
_EMPTY_ZONES = {
    "n_adjusted": 0, "never_adjusted": [], "locked_pairs": [],
    "effective_rank": 0, "reachable_rank": 0, "unexplained_shortfall": 0,
}

# 덤프로 남기는 중간 산출물. 번호는 파이프라인 순서다 - 폴더를 열었을 때
# 무엇이 무엇 다음인지가 파일 이름만으로 보여야 한다.
#
# 전체 changes 는 담지 않는다. Wet 이 매분 흔들려 수만 행이 되는데 그 안에서
# 제어값 변경 몇 줄을 찾는 것은 덤프를 여는 목적과 반대다.
DUMP_TABLES = (
    "01_changes_control",
    "02_events",
    "03_event_deltas",
    "04_events_settled",
    "05_aligned",
    "06_response_curve",
    # 커널이 실제로 무엇을 보고 그 모양이 됐는지. 이것이 없으면 "물리로 보기
    # 어렵다" 를 받은 사람이 다음에 열 것이 없다 - 판정만 있고 근거가 없다.
    "07_delta_samples",
    # 레벨 쪽 입력 둘. 커널이 막혀도 이쪽은 굴러가므로 따로 열어볼 수 있어야 한다.
    "08_absolute_samples",
    "09_lot_finals",
    # 어느 이벤트가 왜 홀로 서지 못했는지. 격리 표의 근거다.
    "10_event_isolation",
)


def profile_dataset(
    csv_path, dict_path, encodings=None, force_encoding=None, *,
    source="csv", sheet=None, tables=None,
) -> dict:
    """경로를 받아 읽고 판정한다. 캐시를 거치지 않는 직통 경로다."""
    readings = parse.load_readings(
        csv_path, dict_path, encodings, force_encoding, source=source, sheet=sheet
    )
    return profile_readings(readings, tables=tables)


def profile_readings(readings: pd.DataFrame, tables: dict | None = None) -> dict:
    """이미 읽은 readings 로 판정한다. 파일을 만지지 않는다.

    읽기와 판정을 떼어 놓으면 원본이 csv·xlsx·parquet 중 무엇이었든 같은 함수를
    지나간다 — 입력 형식마다 판정이 갈라지면 재현이 가장 어려운 버그가 된다.

    `tables` 를 주면 중간 산출물을 거기 **담아만** 준다. 쓰는 것은 호출자와
    dump.py 의 일이다 - 여기서 파일을 열기 시작하면 위 계약이 깨지고, 이 함수를
    테스트에서 자유롭게 부를 수 없게 된다.
    """
    s = get_settings()
    deduped = pivot.dedupe_minute(readings)
    changes = pivot.compress_runs(deduped)
    wet = features.wet_wide(deduped)
    valid = features.valid_zones(wet)
    wm = features.wet_mean_series(wet, valid)

    ev, dl = ev_mod.build_events(changes, s.coating_event_merge_minutes)
    bounds = segment.lot_bounds(deduped)
    if tables is not None:
        # 격리 판정이 붙기 **전** 의 이벤트다. 10 과 나란히 놓고 보면 어느 것이
        # 왜 버려졌는지가 두 파일의 차이로 드러난다.
        tables["01_changes_control"] = changes[changes[S.ITEM].isin(S.CONTROL_ITEM_IDS)]
        tables["02_events"] = ev
        tables["03_event_deltas"] = dl

    iso = ev_mod.isolation(
        ev, s.coating_isolation_pre_minutes, s.coating_isolation_post_minutes, bounds
    ) if not ev.empty else ev
    usable = iso[iso["isolated"]] if len(iso) else iso

    if not ev.empty:
        # 선별에는 안 쓴다. 튜닝 종료 교차검증과 오염 비율 진단용으로만 남긴다.
        ev = ev_mod.annotate_settling(
            ev, wm, s.coating_settle_std_max,
            s.coating_settle_window_minutes, s.coating_settle_max_wait_minutes,
        )
        ds = features.delta_samples(
            usable, dl, wet, valid,
            s.coating_response_post_minutes, s.coating_delta_window_minutes,
        )
    else:
        ds = pd.DataFrame(columns=features.GAP_DELTA_COLS)
    if tables is not None:
        tables["04_events_settled"] = ev
        tables["07_delta_samples"] = ds

    dg = (
        ds[features.GAP_DELTA_COLS].to_numpy(dtype=float)
        if len(ds) else np.zeros((0, S.N_ZONES))
    )
    rank = profile.rank_diagnostics(dg) if len(dg) else {
        "singular_values": np.array([]), "effective_rank": 0, "n_events": 0,
    }

    observed = set(readings[S.ITEM])
    missing = [i for i in S.CONTROL_ITEM_IDS if i not in observed]
    changes_per_lot = (
        changes[changes[S.ITEM].isin(S.CONTROL_ITEM_IDS) & changes[S.PREV_VALUE].notna()]
        .groupby(S.LOT).size()
    )

    facts = {
        "n_lots": int(deduped[S.LOT].nunique()),
        "period": (str(deduped[S.AT].min()), str(deduped[S.AT].max())),
        "n_rows": int(len(readings)),
        "n_events": int(len(ev)),
        # "쓸 수 있는 이벤트" 는 이제 격리 통과 건수 하나뿐이다. 정착 기반
        # contaminated_ratio 는 진단으로만 남는다.
        "n_isolated_events": int(len(usable)),
        "contaminated_ratio": (
            float(ev[S.CONTAMINATED].astype(bool).mean()) if len(ev) else 0.0
        ),
        "changes_per_lot": changes_per_lot.describe().to_dict() if len(changes_per_lot) else {},
        "valid_zones": valid,
        "invalid_zones": [z for z in range(1, S.N_ZONES + 1) if z not in valid],
        "effective_rank": int(rank["effective_rank"]),
        "singular_values": [float(x) for x in rank["singular_values"][:10]],
        "unknown_items": parse.unknown_item_ids(readings),
        "missing_control_items": missing,
        "tuning_end": segment.tuning_end_last_change(changes).to_dict("records"),
    }
    abs_samples = features.absolute_samples(
        changes, wm, bounds,
        s.coating_settle_max_wait_minutes, s.coating_settle_window_minutes,
    )
    if tables is not None:
        tables["08_absolute_samples"] = abs_samples
        tables["09_lot_finals"] = features.lot_finals(changes, bounds)

    facts["isolation"] = _isolation_facts(ev, bounds, s)
    facts["kernel"] = _kernel_facts(ds, dg, s)
    facts["level"] = evaluate.compare_level_models(abs_samples, s.coating_ridge_alpha)
    facts["dynamics"] = _dynamics_facts(readings, ev, dl, s, bounds, tables)
    facts.update(_verdict(facts))
    return facts


def _kernel_facts(ds, dg, s) -> dict:
    """영향행렬 — 랭크가 "풀 수 있다" 고 한 뒤에 실제로 풀어 본다.

    지금까지 이 리포트는 rank_diagnostics 로 자격만 물었다. 자격과 답은 다른
    사실이라, 랭크가 22 여도 커널이 두 봉우리로 나오면 식별된 것이 아니다.

    못 뽑아도 리포트는 계속 간다. 커널은 이 리포트의 결론이 아니라 관측이고,
    여기서 예외를 던지면 앞 절의 멀쩡한 판정까지 같이 사라진다.
    """
    zones = profile.zone_adjustment_diagnostics(dg)
    out = {"zones": zones, "half_width": s.coating_kernel_half_width,
           "alpha": s.coating_ridge_alpha}
    if not len(ds) or zones["effective_rank"] < 1:
        return out
    dw = ds[features.WET_DELTA_COLS].to_numpy(dtype=float)
    try:
        kernel = profile.fit_kernel(
            dg, dw, s.coating_kernel_half_width, s.coating_ridge_alpha
        )
    except (np.linalg.LinAlgError, ValueError):
        return out
    out["diagnostics"] = profile.kernel_diagnostics(kernel)
    return out


def _isolation_facts(ev, bounds, s) -> dict:
    """제어 구간 격리 — 창을 넓혀가며 몇 건이 홀로 서는지.

    이 표가 먼저 나와야 창을 고를 수 있다. 60분 격리가 3건뿐이라면 그 자체가
    사업부에 낼 요구서이고, 30분에서 20건이 산다면 거기서 시작하면 된다.
    숫자를 고르는 일을 사람에게 남기되 고를 근거를 함께 준다.
    """
    if ev.empty:
        return {"table": [], "n_isolated": 0,
                "pre": s.coating_isolation_pre_minutes,
                "post": s.coating_isolation_post_minutes}
    table = ev_mod.isolation_table(ev, bounds=bounds).to_dict("records")
    iso = ev_mod.isolation(
        ev, s.coating_isolation_pre_minutes, s.coating_isolation_post_minutes, bounds
    )
    return {
        "table": table,
        "n_isolated": int(iso["isolated"].sum()),
        "pre": s.coating_isolation_pre_minutes,
        "post": s.coating_isolation_post_minutes,
    }


def _isolation_lines(f: dict) -> list[str]:
    """격리 절. 이벤트 수만으로는 알 수 없는 것을 말한다.

    "이벤트 35건" 과 "그중 홀로 선 것 4건" 은 전혀 다른 사실이고, 뒤가 실제로
    쓸 수 있는 수다. 앞만 적으면 표본이 있는 줄 안다.
    """
    if not f:
        return []
    lines = [
        "## 제어 구간 격리 (앞뒤로 다른 조정이 없는 이벤트)",
        "",
        "  뒤 창   앞 창   홀로 선 이벤트",
    ]
    for r in f["table"]:
        lines.append(
            f"  {r['post_minutes']:>4}분  {r['pre_minutes']:>4}분"
            f"   {r['n_isolated']:>3} / {r['n_events']} ({r['ratio']:.0%})"
        )
    lines += [
        "",
        f"- 현재 설정({f['pre']}/{f['post']}분)에서 **{f['n_isolated']}건**"
        " — 동특성은 이 이벤트들로만 잰다.",
        "- 이 판정은 Wet 을 보지 않는다. 이벤트 시각만으로 정해지므로 L 을"
        " 몰라도 쓸 수 있고, 그래서 L 을 재는 데 쓸 수 있다.",
    ]
    if f["n_isolated"] == 0:
        lines.append(
            "  - ⚠ 홀로 선 이벤트가 없다. 위 표에서 건수가 살아나는 창을 골라"
            " COATING_ISOLATION_PRE/POST_MINUTES 를 낮춘다. 표 전체가 0 이면"
            " 조정이 쉼 없이 이어졌다는 뜻이고, 그때는 조용한 구간을 요청해야 한다."
        )
    return lines + [""]


def _dynamics_facts(readings, ev, dl, s, bounds, tables=None) -> dict:
    """동특성 — "지연이 몇 분인가" 보다 "지연을 말할 수 있는가" 가 먼저다.

    표본이 모자란 채로 낸 L 은 숫자처럼 보여서 더 위험하다. 그래서 못 낼 때는
    숫자 자리에 '몇 건이 더 필요한지' 를 넣는다.

    쓸 이벤트는 **격리**로 고른다. 정착 판정(annotate_settling)으로 고르면
    순환에 빠진다 - 정착 시각을 제대로 잡으려면 L 이 필요한데 L 을 여기서
    재기 때문이다. 게다가 그 판정은 순수 지연 구간을 "이미 정착함" 으로 읽는다
    (반응 전이라 평평하니까). 격리는 이벤트 시각만 보므로 그 고리 밖에 있다.
    """
    p = panel_mod.build_panel(
        pivot.dedupe_minute(readings), s.coating_panel_ffill_max_minutes
    )
    # σ 의 가드는 **모든** 이벤트로 친다. 격리 여부와 무관하게 조정은 Wet 을
    # 흔들고, 그 흔들림이 σ 에 섞이면 안 된다.
    sigma, quiet_minutes = response.noise_floor(
        panel_mod.build_delta(p), ev, s.coating_response_post_minutes
    )
    iso = ev_mod.isolation(
        ev, s.coating_isolation_pre_minutes, s.coating_isolation_post_minutes, bounds
    ) if len(ev) else ev
    usable = iso[iso["isolated"]] if len(iso) else ev
    aligned = response.align_events(
        p, usable, dl, s.coating_response_pre_minutes,
        s.coating_response_post_minutes, s.coating_settle_window_minutes,
    )
    curve = response.response_curve(aligned)
    dyn = response.dynamics(curve, sigma)
    if tables is not None:
        # 리포트가 내는 L·τ 는 이 두 표에서 나온 것이다. 숫자가 이상할 때
        # 곡선을 직접 보는 것 말고는 확인할 방법이 없다.
        tables["05_aligned"] = aligned
        tables["06_response_curve"] = curve
        tables["10_event_isolation"] = iso
    n_clean = int(len(usable))
    n_pairs = aligned.groupby([S.EVENT, S.ZONE]).ngroups if len(aligned) else 0
    # τ 를 아직 모를 때의 대입값. 관측 창의 절반을 쓴다 - 이보다 느린 반응은
    # 애초에 이 창으로 못 본다.
    tau_guess = dyn["tau"] or (s.coating_response_post_minutes / 2)
    return {
        "quiet_minutes": quiet_minutes,
        # 환산은 순수 함수(response)가 하고 설정은 여기서 읽는다 - response.py 는
        # 설정을 만지지 않는다는 경계를 지킨다.
        "line_speed_mpm": s.coating_line_speed_mpm,
        "implied_distance_m": response.implied_distance_m(
            dyn["dead_time"], s.coating_line_speed_mpm
        ),
        **dyn,
        **response.verdict(sigma, n_clean, n_pairs, dyn, tau_guess),
    }


def _verdict(f: dict) -> dict:
    if f["n_isolated_events"] == 0:
        return {
            "verdict": "insufficient",
            "verdict_reason": (
                "조정 이벤트가 0건이다. 제어값이 한 번도 바뀌지 않은 구간만 있으면 "
                "입력→출력 관계를 배울 수 없다. 튜닝 과정이 포함된 구간의 데이터가 필요하다."
            ),
        }
    if f["n_isolated_events"] < _MIN_EVENTS:
        return {
            "verdict": "insufficient",
            "verdict_reason": (
                f"격리된 조정 이벤트가 {f['n_isolated_events']}건으로 하한 {_MIN_EVENTS}건에 못 미친다."
            ),
        }
    if f["effective_rank"] < 3:
        return {
            "verdict": "rank_deficient",
            "verdict_reason": (
                f"Δgap 유효 랭크가 {f['effective_rank']}이다. 작업자가 늘 비슷한 패턴으로 "
                "조정하고 있어 이벤트 수와 무관하게 영향행렬을 식별할 수 없다. "
                "커널을 1~2 파라미터로 고정하고, 파일럿에서 가진(DOE) 실험을 제안한다."
            ),
        }
    return {"verdict": "trainable", "verdict_reason": "1·2차 관문을 통과했다."}


def _dynamics_lines(d: dict) -> list[str]:
    """동특성 섹션. 못 낼 때 빈 칸을 두지 않고 '무엇이 더 필요한지' 를 적는다."""
    if not d:
        return []
    sigma = d.get("sigma")
    lines = ["## 동특성 (조정 → 반영까지)"]
    if sigma is None or not np.isfinite(sigma):
        return lines + ["- 노이즈를 잴 조용한 구간이 없다. 판정 불가.", ""]

    lines.append(
        f"- 노이즈 바닥: σ = {sigma:.4f} (분당 ΔWet, 조정 없는 {d['quiet_minutes']:,}분에서)"
    )
    lines.append(
        f"- 깨끗한 이벤트 {d['n_events']}건 → (이벤트×zone) 표본 {d['n_pairs']}개"
    )
    if not d.get("identifiable"):
        return lines + _not_identifiable_lines(d) + [""]

    lines.append(
        f"- **순수 지연 L = {d['dead_time']}분** — 조정 후 이 시간이 지나야 Wet 이 움직인다"
    )
    dist, speed = d.get("implied_distance_m"), d.get("line_speed_mpm")
    if dist is not None and speed:
        # L 에는 참값이 없어 맞는지 확인할 방법이 없다. 라인 속도가 설비 고정값이므로
        # 이 환산 거리만이 외부 기준이 된다 - 다이~측정기가 그만큼 떨어져 있을 리
        # 없으면 그 L 은 노이즈를 문 것이다.
        lines.append(
            f"  - 환산 거리 {dist:,.0f} m (라인 속도 {speed:g} m/min, 설비 고정·전 제품 공통)"
            " — 다이~측정기 실제 거리와 자릿수가 다르면 이 L 은 노이즈를 문 것이다."
        )
    if d.get("tau") is not None:
        lines.append(f"- 시정수 τ = {d['tau']}분 (최종 변화의 63% 도달)")
    if d.get("settle") is not None:
        lines.append(
            f"- 정착 T_s = {d['settle']}분 — 다음 조정까지 최소 이만큼 띄워야"
            " 두 조정의 효과가 섞이지 않는다"
        )
    lines.append(f"- 최종 변화량 {d['final']:+.4f} (부호 정렬, |Δgap| 1회분 기준)")
    if d.get("plateau_reached") is False:
        lines.append(
            "  - ⚠ 관측 창이 끝날 때까지 아직 오르고 있다. τ 와 최종 변화량은 **하한**이다"
            " — COATING_RESPONSE_POST_MINUTES 를 늘려 다시 본다."
        )
    return lines + [""]


def _not_identifiable_lines(d: dict) -> list[str]:
    """왜 못 내는지에 따라 다른 말을 한다. "반응이 없다" 와 "표본이 모자라다" 는
    다른 진단이고 다음 행동도 다르다 - 앞은 조정 폭을, 뒤는 이벤트 수를 늘려야 한다."""
    reason = d.get("reason")
    if reason == "no_events":
        return ["- **판정: 지연 추정 불가** — 깨끗한 조정 이벤트가 없다.",
                "  - 제어값이 바뀐 구간의 데이터가 있어야 한다."]
    if reason == "no_response":
        det = d.get("detectable")
        size = f"±{det:.4f}" if det and np.isfinite(det) else "검출 한계"
        return [
            f"- **판정: 반응이 관측되지 않았다** — 지금 표본으로 볼 수 있는 최소 반응은 {size} 다.",
            "  - 그보다 작은 반응은 있어도 못 본다. 조정 폭을 키우거나 이벤트를 늘려야 한다.",
        ]
    need, short = d.get("required_events", -1), d.get("shortfall_events", -1)
    want = f"약 {need}건" if need > 0 else "더 많은 이벤트가"
    tail = f" (지금 {d['n_events']}건, {short}건 부족)." if short > 0 else "."
    return [
        f"- **판정: 지연 추정 불가** — 1분 단위로 가르려면 {want} 필요하다{tail}",
        "  - 반응은 보인다. 튜닝 구간이 포함된 기간의 데이터를 더 요청한다.",
    ]


def _kernel_lines(kf: dict) -> list[str]:
    """영향행렬 섹션. 랭크 결손을 zone 의 말로 옮기고, 커널을 눈으로 보게 한다.

    막대를 그리는 이유는 장식이 아니다. 종 모양인지 두 봉우리인지는 숫자 다섯
    개를 읽는 것보다 모양을 보는 쪽이 빠르고, 이 판정의 실패 양식이 정확히
    "숫자는 그럴듯한데 모양이 물리가 아닌 것" 이다.
    """
    z = kf["zones"]
    lines = [
        "## 영향행렬 (조정 → 이웃으로 퍼짐)",
        f"- 조정된 zone: {z['n_adjusted']} / {S.N_ZONES}",
    ]
    if z["never_adjusted"]:
        lines.append(
            f"- 한 번도 안 움직인 zone: {z['never_adjusted']}"
            " — 항목이 없거나 작업자가 안 건드리는 자리다. 랭크 결손의 정상적인 이유다."
        )
    if z["locked_pairs"]:
        lines.append(
            f"- 항상 같이 움직인 쌍: {z['locked_pairs']}"
            " — 이 둘의 기여는 이 데이터로 영영 가를 수 없다. 따로 움직인 구간을 요청한다."
        )
    lines.append(
        f"- 도달 가능 랭크 {z['reachable_rank']} · 실제 {z['effective_rank']}"
        f" → 설명 안 된 결손 {z['unexplained_shortfall']}"
    )
    if z["unexplained_shortfall"] > 0:
        lines.append(
            "  - ⚠ 위 둘로 설명되지 않는 결손이 남았다. 세 개 이상이 묶여 움직이는"
            " 패턴일 수 있다 — 03_event_deltas 를 직접 본다(`--dump`)."
        )

    d = kf.get("diagnostics")
    if not d:
        return lines + ["- 커널을 뽑지 못했다 (표본 또는 랭크 부족).", ""]

    lines.append(
        f"- 커널 (k={kf['half_width']}, ridge α={kf['alpha']:g})"
        " — 볼트 하나를 1 움직였을 때 offset 만큼 떨어진 zone 이 받는 몫"
    )
    lines += _kernel_bar(d["kernel"])
    mark = "종 모양이다" if d["plausible"] else "물리로 보기 어렵다"
    lines.append(f"- **판정: {mark}** — " + " ".join(d["reasons"]))
    lines.append(
        f"- 중심 집중도 {d['mass_ratio']:.0%} · 좌우 비대칭 {d['asymmetry']:.2f}"
        f" · 가장자리 비 {d['edge_ratio']:.2f}"
    )
    if d["truncated"]:
        lines.append(
            "  - ⚠ 가장자리 탭이 아직 크다. 퍼짐이 k 에서 잘렸다는 뜻이다 —"
            " COATING_KERNEL_HALF_WIDTH 를 늘려 다시 본다."
        )
    if d["asymmetry"] > 0.2:
        lines.append(
            "  - ⚠ 좌우가 뚜렷이 다르다. 흐름 방향 효과일 수도, zone 정렬이"
            " 틀린 것일 수도 있다 — 둘은 다른 문제다."
        )
    return lines + [""]


def _kernel_bar(kernel: list[float], width: int = 36) -> list[str]:
    """커널을 막대로. 부호가 뒤집힌 탭은 다른 글자로 찍어 눈에 걸리게 한다."""
    scale = max((abs(v) for v in kernel), default=0.0) or 1.0
    k = (len(kernel) - 1) // 2
    center_sign = 1.0 if kernel[k] >= 0 else -1.0
    out = []
    for i, v in enumerate(kernel):
        n = int(round(abs(v) / scale * width))
        glyph = "█" if v * center_sign >= 0 else "▒"
        off = i - k
        out.append(f"      {off:+d}" if off else "      " + " 0")
        out[-1] += f"  {v:+8.4f}  {glyph * n}"
    return out


def _level_lines(lv: dict) -> list[str]:
    """레벨 모델 절. 이 절의 산출물은 정확도가 아니라 "모델이 필요한가" 다.

    영향행렬과 나란히 두는 이유: Wet 은 레벨(평균 두께)과 프로파일(폭 방향
    모양)로 나뉘고 둘의 담당이 다르다. 한쪽이 막혀도 다른 쪽은 갈 수 있는데,
    절이 하나뿐이면 그 사실이 안 보인다.
    """
    if not lv:
        return []
    lines = [
        "## 레벨 모델 (제어값 → 평균 두께)",
        f"- 절대 샘플 {lv['n_samples']}건 · lot {lv['n_lots']}개"
        f" · 평가 lot {lv['n_eval_lots']}개 (walk-forward, 과거만 본다)",
    ]
    if lv["features"]:
        lines.append(
            f"- 제어 피처: {lv['features']}"
            + (f" · 이 중 실제로 변하는 것: {lv['varying_features']}"
               if lv["varying_features"] != lv["features"] else "")
        )
    if lv["mae_model"] is None:
        return lines + [f"- **판정: 아직 못 낸다** — {lv['reason']}", ""]

    lines += [
        f"- MAE — 모델 {lv['mae_model']:.4f} · 직전 lot {lv['mae_prev_lot']:.4f}"
        f" · 전역 중앙값 {lv['mae_global']:.4f}",
        f"- **판정: {lv['verdict']}** — {lv['reason']}",
    ]
    if lv["coefficients"]:
        signs = ", ".join(f"{k} {v:+.3f}" for k, v in lv["coefficients"].items())
        lines.append(f"- 계수(표준화 후, 부호를 본다): {signs}")
        # 물리 정합성. 토출량이 늘면 로딩이 늘어야 한다.
        rpm = lv["coefficients"].get("pump_rpm")
        if rpm is not None and rpm < 0:
            lines.append(
                "  - ⚠ Pump RPM 계수가 음수다. 토출량↑ ⇒ 로딩↑ 이라는 물리와"
                " 어긋난다. 교란변수(고형분·점도)가 빠졌을 가능성을 먼저 본다."
            )
    return lines + [""]


def render_markdown(f: dict) -> str:
    lines = [
        "# 코팅 초기조건 데이터 실사 리포트",
        "",
        f"- **판정: {f['verdict']}** — {f['verdict_reason']}",
        "",
        "## 규모",
        f"- lot 수: {f['n_lots']}",
        f"- 기간: {f['period'][0]} ~ {f['period'][1]}",
        f"- 원본 행 수: {f['n_rows']}",
        "",
        "## 조정 이벤트",
        f"- 전체 이벤트: {f['n_events']}",
        f"- 격리된 이벤트: {f['n_isolated_events']} (아래 '제어 구간 격리' 절과 같은 수치)",
        f"- 오염 비율: {f['contaminated_ratio']:.1%}",
        f"- lot 당 제어값 변경 횟수: {f['changes_per_lot'] or '없음'}",
        "",
        "## 식별성",
        f"- Δgap 유효 랭크: {f['effective_rank']} / 25",
        f"- 상위 특이값: {[round(x, 3) for x in f['singular_values']]}",
        "",
        *_isolation_lines(f.get("isolation") or {}),
        *_kernel_lines(f.get("kernel") or {"zones": _EMPTY_ZONES}),
        *_level_lines(f.get("level") or {}),
        *_dynamics_lines(f.get("dynamics") or {}),
        "## 유효 폭",
        f"- 유효 zone: {f['valid_zones']}",
        f"- 제외 zone: {f['invalid_zones']}",
        "",
        "## 추가 데이터 요청",
    ]
    if f["missing_control_items"]:
        lines.append(f"- 데이터에 없는 제어 항목: {f['missing_control_items']}")
        lines.append(
            "  - `50030111`(Pump RPM) · `10030009`(BP open rate)가 없으면 "
            "레벨 모델은 제품 상수와 OS/DS Gap 만으로 만들어야 하고, 정확도 기대치를 "
            "크게 낮춰야 한다."
        )
    if f["unknown_items"]:
        lines.append(f"- 사전에 없는 항목(사전 갱신 필요): {f['unknown_items']}")
    lines += [
        "- 고형분(%) · 점도 · 호기: 같은 제어값이라도 이 값들이 다르면 "
        "L/L 이 달라지므로, 빠지면 모델이 설명 못 하는 분산으로 남는다.",
        f"  - 라인 속도는 {f['dynamics']['line_speed_mpm']:g} m/min 고정(전 제품 공통)이라 "
        "이 목록에서 뺐다. 값은 COATING_LINE_SPEED_MPM 이 정한다.",
        "- 제품별 목표 L/L 표준값과 스펙 상하한.",
        "",
    ]
    return "\n".join(lines)


def render_html(f: dict) -> str:
    body = html_mod.escape(render_markdown(f))
    return (
        "<!doctype html>\n<html lang=\"ko\"><head><meta charset=\"utf-8\">"
        "<title>코팅 초기조건 데이터 실사</title>"
        "<style>body{font-family:system-ui,'Malgun Gothic',sans-serif;"
        "max-width:60rem;margin:2rem auto;padding:0 1rem;line-height:1.7;}"
        "pre{white-space:pre-wrap;background:#f6f7f9;padding:1rem;border-radius:.5rem;}"
        "</style></head><body>\n"
        f"<pre>{body}</pre>\n</body></html>\n"
    )


def run(
    csv_path=None,
    dict_path=None,
    out_dir=None,
    encodings=None,
    force_encoding=None,
    source=None,
    sheet=None,
    dump=None,
) -> tuple[str, str]:
    s = get_settings()
    root = Path(s.resolved_coating_data_dir)
    # CSV 는 실데이터라 런타임 디렉터리에서, 사전은 스키마라 패키지에서 읽는다.
    csv_path = csv_path or _default_input_path()
    dict_path = dict_path or parse.DEFAULT_DICT_PATH
    out = Path(out_dir) if out_dir else root / "reports"
    out.mkdir(parents=True, exist_ok=True)
    # 인코딩 후보도 기본값 결정은 여기 한 곳이다(.env 단일 출처).
    encodings = encodings or s.coating_csv_encoding_list
    source = parse.format_for(csv_path, source, s.coating_input_format)
    sheet = sheet or s.coating_xlsx_sheet or None

    # 기본값 결정은 여기 한 곳이다(--input·--out 과 같은 관례). 인자가 None 이면
    # '지정 안 함' 이고, 그때만 .env 를 본다.
    want_dump = s.coating_dump_enabled if dump is None else bool(dump)
    tables = {} if want_dump else None

    facts = profile_dataset(
        csv_path, dict_path, encodings, force_encoding,
        source=source, sheet=sheet, tables=tables,
    )
    md_path = out / "data_profile.md"
    html_path = out / "data_profile.html"
    md_path.write_text(render_markdown(facts), encoding="utf-8")
    html_path.write_text(render_html(facts), encoding="utf-8")
    if tables is not None:
        # 리포트 옆에 쌓는다. --out 하나로 산출물이 전부 따라오게 하려는 것이다.
        dump_mod.write_tables(
            tables,
            dump_mod.new_run_dir(out / "dump"),
            meta=_dump_meta(csv_path, source, sheet, s),
        )
    return str(md_path), str(html_path)


def _dump_meta(csv_path, source, sheet, s) -> dict:
    """매니페스트에 적을 것 — 무엇을 읽었고 어떤 설정이 이 표들을 만들었나."""
    return {
        "input": str(csv_path),
        "format": source,
        "sheet": sheet or "(첫 시트)",
        "event_merge_minutes": s.coating_event_merge_minutes,
        "settle_window_minutes": s.coating_settle_window_minutes,
        "settle_std_max": s.coating_settle_std_max,
        "settle_max_wait_minutes": s.coating_settle_max_wait_minutes,
        "panel_ffill_max_minutes": s.coating_panel_ffill_max_minutes,
        "response_pre_minutes": s.coating_response_pre_minutes,
        "response_post_minutes": s.coating_response_post_minutes,
    }


def build_parser() -> argparse.ArgumentParser:
    """CLI 파서. 기본 경로를 여기 또 적지 않는다 — 기본값 결정은 run() 한 곳이다."""
    p = argparse.ArgumentParser(
        prog="python -m app.coating.report",
        description="코팅 초기조건 데이터 실사 리포트를 MD·HTML 로 낸다.",
        epilog=(
            "예) python -m app.coating.report --csv data/coating/raw/실데이터.csv\n"
            "생략하면 <COATING_DATA_DIR>/raw/sample_long.csv 를 읽는다."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--input", "--csv", dest="input_path", default=None,
        help="원본 long 테이블 경로 (생략 시 COATING_INPUT_PATH)",
    )
    p.add_argument(
        "--format", choices=("csv", "xlsx", "parquet"), default=None,
        help="입력 형식. 생략하면 확장자로 판별하고, 모르는 확장자면 COATING_INPUT_FORMAT.",
    )
    p.add_argument(
        "--sheet", default=None,
        help="xlsx 에서 읽을 시트 (생략 시 COATING_XLSX_SHEET, 그것도 비면 첫 시트)",
    )
    p.add_argument(
        "--dict", dest="dict_path", default=None,
        help="항목 사전 CSV 경로 (생략 시 패키지에 든 스키마)",
    )
    p.add_argument(
        "--out", dest="out_dir", default=None,
        help="리포트 출력 디렉터리 (생략 시 <COATING_DATA_DIR>/reports)",
    )
    p.add_argument(
        "--encoding", default=None,
        help=(
            "원본 CSV 인코딩을 하나로 강제한다. 생략하면 BOM·시그니처로 판별하고 "
            "안 되면 COATING_CSV_ENCODINGS 후보를 차례로 시도한다."
        ),
    )
    p.add_argument(
        "--dump", action="store_const", const=True, default=None,
        help=(
            "중간 산출물(이벤트·정렬표·응답곡선)을 <out>/dump/시각/ 에 CSV 로 "
            "남긴다. 생략하면 COATING_DUMP_ENABLED."
        ),
    )
    return p


def main(argv: list[str] | None = None) -> tuple[str, str]:
    console.use_utf8()
    args = build_parser().parse_args(argv)
    s = get_settings()
    source = parse.format_for(
        args.input_path or _default_input_path(), args.format,
        s.coating_input_format,
    )
    in_path = Path(args.input_path) if args.input_path else _default_input_path()
    if not in_path.exists():
        # 기본 입력은 backend/data/ 아래인데 그건 실데이터가 들어가는 곳이라
        # gitignore 대상이다. 새로 클론한 곳에는 없으므로, 맨 트레이스백 대신
        # 무엇을 하면 되는지 적어준다. 안내는 형식별로 다르다 - xlsx 를 쓰는
        # 사람에게 CSV 샘플을 복사하라고 하면 틀린 길로 보내는 것이다.
        hint = (
            "    backend/tests/fixtures/coating/sample_long.csv"
            f" -> {_default_input_path()}"
            if source == "csv"
            else "  .env 의 COATING_INPUT_PATH 가 가리키는 곳에 xlsx 를 둔다."
        )
        raise SystemExit(
            f"입력 파일을 찾을 수 없다: {in_path} (형식: {source})\n"
            "  --input 으로 경로를 지정하거나, COATING_INPUT_PATH 를 고친다.\n"
            + hint
        )

    try:
        md_path, html_path = run(
            in_path,
            args.dict_path,
            args.out_dir,
            force_encoding=args.encoding,
            source=source,
            sheet=args.sheet,
            dump=args.dump,
        )
    except ValueError as e:
        # parse·excel_source 가 원인을 이미 문장으로 만들어 뒀다. 트레이스백을
        # 그대로 던지면 그 문장이 스택 밑에 묻힌다.
        raise SystemExit(str(e)) from e
    print(md_path)
    print(html_path)
    return md_path, html_path


def _default_input_path() -> Path:
    """기본 입력 경로. 형식(csv/xlsx)과 함께 .env 가 정한다."""
    return Path(get_settings().resolved_coating_input_path)


if __name__ == "__main__":
    main()
