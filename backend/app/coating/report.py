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

from app.coating import events as ev_mod
from app.coating import features, panel as panel_mod, parse, pivot, response
from app.coating import schemas as S
from app.coating import segment
from app.coating.model import profile
from app.config import get_settings

_MIN_EVENTS = 20  # Toeplitz 커널(2k+1개)을 견고하게 추정하는 하한


def profile_dataset(
    csv_path, dict_path, encodings=None, force_encoding=None, *, source="csv", sheet=None
) -> dict:
    """경로를 받아 읽고 판정한다. 캐시를 거치지 않는 직통 경로다."""
    readings = parse.load_readings(
        csv_path, dict_path, encodings, force_encoding, source=source, sheet=sheet
    )
    return profile_readings(readings)


def profile_readings(readings: pd.DataFrame) -> dict:
    """이미 읽은 readings 로 판정한다. 파일을 만지지 않는다.

    읽기와 판정을 떼어 놓으면 원본이 csv·xlsx·parquet 중 무엇이었든 같은 함수를
    지나간다 — 입력 형식마다 판정이 갈라지면 재현이 가장 어려운 버그가 된다.
    """
    s = get_settings()
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
    facts["dynamics"] = _dynamics_facts(readings, ev, dl, s)
    facts.update(_verdict(facts))
    return facts


def _dynamics_facts(readings, ev, dl, s) -> dict:
    """동특성 — "지연이 몇 분인가" 보다 "지연을 말할 수 있는가" 가 먼저다.

    표본이 모자란 채로 낸 L 은 숫자처럼 보여서 더 위험하다. 그래서 못 낼 때는
    숫자 자리에 '몇 건이 더 필요한지' 를 넣는다.
    """
    p = panel_mod.build_panel(
        pivot.dedupe_minute(readings), s.coating_panel_ffill_max_minutes
    )
    sigma, quiet_minutes = response.noise_floor(
        panel_mod.build_delta(p), ev, s.coating_response_post_minutes
    )
    aligned = response.align_events(
        p, ev, dl, s.coating_response_pre_minutes,
        s.coating_response_post_minutes, s.coating_settle_window_minutes,
    )
    curve = response.response_curve(aligned)
    dyn = response.dynamics(curve, sigma)
    n_clean = int((~ev[S.CONTAMINATED].astype(bool)).sum()) if len(ev) else 0
    n_pairs = aligned.groupby([S.EVENT, S.ZONE]).ngroups if len(aligned) else 0
    # τ 를 아직 모를 때의 대입값. 관측 창의 절반을 쓴다 - 이보다 느린 반응은
    # 애초에 이 창으로 못 본다.
    tau_guess = dyn["tau"] or (s.coating_response_post_minutes / 2)
    return {
        "quiet_minutes": quiet_minutes,
        **dyn,
        **response.verdict(sigma, n_clean, n_pairs, dyn, tau_guess),
    }


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


def run(
    csv_path=None,
    dict_path=None,
    out_dir=None,
    encodings=None,
    force_encoding=None,
    source=None,
    sheet=None,
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

    facts = profile_dataset(
        csv_path, dict_path, encodings, force_encoding, source=source, sheet=sheet
    )
    md_path = out / "data_profile.md"
    html_path = out / "data_profile.html"
    md_path.write_text(render_markdown(facts), encoding="utf-8")
    html_path.write_text(render_html(facts), encoding="utf-8")
    return str(md_path), str(html_path)


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
    return p


def main(argv: list[str] | None = None) -> tuple[str, str]:
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
