"""판정 리포트 — v1 의 실제 산출물.

"모델이 얼마나 정확한가" 보다 "이 데이터로 학습이 가능한가" 가 먼저다.
관문은 둘이다.
  1차: 조정 이벤트가 존재하는가 (lot 당 gap 변경 횟수)
  2차: Δgap 의 유효 랭크가 충분한가

둘 중 하나라도 막히면 verdict = insufficient 를 내고, 무엇이 얼마나
부족한지를 사업부에 요구할 수 있는 형태로 적는다.
"""
import html as html_mod
from pathlib import Path

import numpy as np
import pandas as pd

from app.coating import events as ev_mod
from app.coating import features, parse, pivot
from app.coating import schemas as S
from app.coating import segment
from app.coating.model import profile
from app.config import get_settings

_MIN_EVENTS = 20  # Toeplitz 커널(2k+1개)을 견고하게 추정하는 하한


def profile_dataset(csv_path, dict_path) -> dict:
    s = get_settings()
    readings = parse.load_readings(csv_path, dict_path)
    deduped = pivot.dedupe_minute(readings)
    changes = pivot.compress_runs(deduped)
    wet = features.wet_wide(deduped)
    valid = features.valid_zones(wet)
    wm = features.wet_mean_series(wet, valid)

    ev, dl = ev_mod.build_events(changes, s.coating_event_merge_minutes)
    if not ev.empty:
        ev = ev_mod.annotate_settling(
            ev, wm, s.coating_settle_std_max,
            s.coating_settle_window_minutes, s.coating_settle_max_wait_minutes,
        )
        ds = features.delta_samples(
            ev, dl, wet, valid, s.coating_settle_window_minutes
        )
    else:
        ds = pd.DataFrame(columns=features.GAP_DELTA_COLS)

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
        "n_clean_events": int((~ev[S.CONTAMINATED].astype(bool)).sum()) if len(ev) else 0,
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
    facts.update(_verdict(facts))
    return facts


def _verdict(f: dict) -> dict:
    if f["n_clean_events"] == 0:
        return {
            "verdict": "insufficient",
            "verdict_reason": (
                "조정 이벤트가 0건이다. 제어값이 한 번도 바뀌지 않은 구간만 있으면 "
                "입력→출력 관계를 배울 수 없다. 튜닝 과정이 포함된 구간의 데이터가 필요하다."
            ),
        }
    if f["n_clean_events"] < _MIN_EVENTS:
        return {
            "verdict": "insufficient",
            "verdict_reason": (
                f"깨끗한 조정 이벤트가 {f['n_clean_events']}건으로 하한 {_MIN_EVENTS}건에 못 미친다."
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
        f"- 깨끗한 이벤트: {f['n_clean_events']}",
        f"- 오염 비율: {f['contaminated_ratio']:.1%}",
        f"- lot 당 제어값 변경 횟수: {f['changes_per_lot'] or '없음'}",
        "",
        "## 식별성",
        f"- Δgap 유효 랭크: {f['effective_rank']} / 25",
        f"- 상위 특이값: {[round(x, 3) for x in f['singular_values']]}",
        "",
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
        "- 고형분(%) · 점도 · 라인 속도 · 호기: 같은 제어값이라도 이 값들이 다르면 "
        "L/L 이 달라지므로, 빠지면 모델이 설명 못 하는 분산으로 남는다.",
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


def run(csv_path=None, dict_path=None, out_dir=None) -> tuple[str, str]:
    s = get_settings()
    root = Path(s.resolved_coating_data_dir)
    # CSV 는 실데이터라 런타임 디렉터리에서, 사전은 스키마라 패키지에서 읽는다.
    csv_path = csv_path or root / "raw" / "sample_long.csv"
    dict_path = dict_path or parse.DEFAULT_DICT_PATH
    out = Path(out_dir) if out_dir else root / "reports"
    out.mkdir(parents=True, exist_ok=True)

    facts = profile_dataset(csv_path, dict_path)
    md_path = out / "data_profile.md"
    html_path = out / "data_profile.html"
    md_path.write_text(render_markdown(facts), encoding="utf-8")
    html_path.write_text(render_html(facts), encoding="utf-8")
    return str(md_path), str(html_path)


if __name__ == "__main__":
    m, h = run()
    print(m)
    print(h)
