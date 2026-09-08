"""전처리 로그용 집계. ★순수 — 파일·설정을 만지지 않는다.

규칙(events.py)과 보고(여기)를 나눈 이유는 하나다. 규칙은 파이프라인이 매번
쓰는 것이고 이 표들은 사람이 볼 때만 필요하다. 섞어 두면 규칙을 읽을 때
렌더링 관심사가 같이 딸려온다.

이 파일의 표들은 **사용자가 혼자 읽고 혼자 판정할 수 있어야** 한다. 실데이터는
사내 PC 에만 있고 여기서는 볼 수 없으므로(CLAUDE.md), 숫자 옆에 판정 기준이
같이 붙지 않으면 그 숫자는 왕복 한 번을 더 쓰게 만든다.
"""
import pandas as pd

from app.coating import events as ev_mod
from app.coating import schemas as S

LEDGER_COLS = [
    S.EVENT, S.LOT, S.RUN, S.AT, S.LAST_AT, S.SPAN, "n_items",
    "n_dup_zones", "gap_before", "gap_after", "zones", "isolated", S.ISO_REASON,
]


def _run_sizes(df: pd.DataFrame) -> pd.Series:
    """run_id 하나에 이벤트가 몇 개 들었나. 인덱스가 run_id 인 시리즈. ★순수

    event_ledger·funnel·timeline 셋 다 "이 run 이 쪼개진 것인가(size>1)" 를
    묻는다. 묻는 방법이 갈린다 - 행 단위(각 이벤트에 자기 run 크기를 붙임)로
    쓰는 곳도, run 단위(run 하나당 한 줄)로 쓰는 곳도 있다. 여기서는 더 작은
    쪽(run 단위, groupby().size())을 진짜로 삼는다 - 행 단위가 필요한 곳은
    `df[S.RUN].map(_run_sizes(df))` 로 이 결과를 다시 펼치면 되지만, 거꾸로
    run 단위가 필요한 곳에서 행 단위 결과를 되접는 것은 groupby 를 한 번 더
    부르는 것과 다르지 않다.
    """
    return df.groupby(S.RUN)[S.EVENT].size()


def event_ledger(iso: pd.DataFrame, event_deltas: pd.DataFrame) -> pd.DataFrame:
    """묶음 하나당 한 줄. ★순수

    원시 타임스탬프를 그대로 싣는다. 파생된 분 수만 있으면 사람이 원본과 대조할
    방법이 없고, 대조가 안 되면 이 표는 믿을 근거가 없는 숫자 더미가 된다.
    """
    if iso.empty:
        return pd.DataFrame(columns=LEDGER_COLS)

    out = iso.copy()
    # run 이 이벤트 하나짜리면 비운다 - 눈에 띄어야 할 것은 쪼개진 것뿐이다.
    sizes = out[S.RUN].map(_run_sizes(out))
    out[S.RUN] = out[S.RUN].where(sizes > 1, "")

    zoned = event_deltas[event_deltas[S.ZONE].notna()].copy()
    zoned[S.ZONE] = zoned[S.ZONE].astype(int)
    per = zoned.groupby([S.EVENT, S.ZONE]).size().rename("k").reset_index()

    zones = (
        per.sort_values([S.EVENT, S.ZONE])
        .groupby(S.EVENT)[S.ZONE]
        .apply(lambda z: ",".join(f"z{int(v)}" for v in z))
    )
    dups = per[per["k"] > 1].groupby(S.EVENT).size()

    out["zones"] = out[S.EVENT].map(zones).fillna("")
    out["n_dup_zones"] = out[S.EVENT].map(dups).fillna(0).astype(int)
    return out[LEDGER_COLS].reset_index(drop=True)


FUNNEL_COLS = ["stage", "n", "delta", "note"]


def funnel(
    readings: pd.DataFrame,
    deduped: pd.DataFrame,
    changes: pd.DataFrame,
    iso: pd.DataFrame,
    delta_samples: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """행이 어느 단계에서 얼마나 줄었나. ★순수

    **이름 없는 감소가 없어야 한다.** 원본 100만 행이 이벤트 31건이 되는 것은
    정상인데, 어느 줄에서 얼마가 빠졌는지 이름이 붙지 않으면 사람은 그 정상을
    이상으로 읽고 파이프라인을 의심하는 데 시간을 쓴다.

    `delta_samples` 를 주면 마지막에 "ΔWet 표본" 한 줄이 더 붙는다.
    `features.delta_samples` 는 격리를 통과한 이벤트라도 앞뒤 Wet 창이 비어
    있으면(그 이벤트를 조정 전후로 Wet 이 안 잡힌 lot) 그 행을 조용히
    버린다 - 07_delta_samples 가 §0 의 "격리 통과" 보다 적어질 수 있는데,
    이름 없이 사라지면 그 감소도 "정상" 과 "이상" 이 안 갈린다. 안 주면
    이 줄은 없다(기존 호출부와 호환).

    `delta` 열은 **nullable 정수(`Int64`)** 다 — 단위가 바뀌어 뺄셈이 뜻을
    잃는 줄은 파이썬 `int` 와 `None` 을 그냥 리스트로 섞어 담으면 pandas 가
    조용히 `float64` 로 승격시켜 `None` 을 `NaN` 으로 바꾼다(정확히 이 코드가
    한 번 그렇게 당했었다 - Task 10 diagnose._funnel_lines 가 `is None` 으로
    걸렀다가 매 실행 `(+nan)` 을 냈다). `Int64` 로 명시하면 "델타 없음" 이
    `pd.NA` 로 정직하게 남고, `pd.isna()` 로 float 승격 없이도 잡힌다 -
    events.isolation 이 `iso_reason` 에 `dtype=object` 를 쓰는 것과 같은 이유다.
    """
    ctrl = changes[changes[S.ITEM].isin(S.CONTROL_ITEM_IDS)]
    real = ctrl[ctrl[S.PREV_VALUE].notna()] if S.PREV_VALUE in ctrl.columns else ctrl

    n_events = int(len(iso))
    if n_events:
        run_sizes = _run_sizes(iso)
        n_frag = int(run_sizes[run_sizes > 1].sum())
        ok = iso["isolated"].astype(bool)
        n_iso = int(ok.sum())
        rej = iso.loc[~ok, S.ISO_REASON].fillna("")
        n_before = int(rej.str.startswith("앞 ").sum())
        n_after = int(rej.str.startswith("뒤 ").sum())
        n_both = int(rej.str.startswith("앞뒤").sum())
        note = f"탈락 앞 {n_before} · 뒤 {n_after} · 양쪽 {n_both}"
    else:
        n_frag = n_iso = 0
        note = "탈락 앞 0 · 뒤 0 · 양쪽 0"

    # 네 번째 자리(bool)는 "이 줄이 바로 위 줄의 부분집합이라 뺄셈이 뜻을
    # 갖는가" 다. 인덱스로 자르면(예: i <= 4) 중간에 단계가 하나 끼어드는
    # 순간 경계가 소리 없이 밀린다 — 그래서 자름을 자리(위치)가 아니라
    # 각 줄 자신에 실어 둔다.
    #
    # 앞 다섯 줄(원본 행 ~ 시작값 제외)은 같은 "행" 을 계속 걸러낸 것이라
    # 이웃과 뺄 수 있다. "앵커 묶음" 부터는 단위가 행에서 묶음으로 바뀌어
    # 뺄셈이 의미를 잃고, 그 뒤 "연속 조작 구간 조각"·"격리 통과" 도 서로
    # 부분집합 관계가 아니라 앵커 묶음을 각각 다른 잣대(연쇄 조각인가 /
    # 격리를 통과했는가)로 다시 센 것이라 체인이 거기서도 이어지지 않는다.
    rows = [
        ("원본 행", len(readings), "", False),
        ("분 중복 접기", len(deduped), "", True),
        ("값이 바뀐 시점만", len(changes), "", True),
        ("제어 항목만", len(ctrl), "", True),
        ("시작값 제외", len(real), "lot·항목별 첫 관측은 사람이 바꾼 것이 아니다", True),
        ("앵커 묶음", n_events, "묶음 수 — 위와 단위가 다르다", False),
        ("연속 조작 구간 조각", n_frag, "2분 가정을 넘겨 쪼개진 것", False),
        ("격리 통과", n_iso, note, False),
    ]
    if delta_samples is not None:
        n_ds = int(len(delta_samples))
        n_dropped = max(n_iso - n_ds, 0)
        ds_note = f"Wet 창이 비어 {n_dropped}건 제외" if n_dropped else ""
        # 같은 단위(이벤트)의 부분집합이라 앞 줄("격리 통과")과 체인이 이어진다.
        rows.append(("ΔWet 표본", n_ds, ds_note, True))
    out = pd.DataFrame(rows, columns=["stage", "n", "note", "_chains_from_prev"])
    ns = out["n"].tolist()
    chains = out["_chains_from_prev"].tolist()
    deltas = [
        (ns[i] - ns[i - 1]) if chains[i] else None for i in range(len(out))
    ]
    # dtype 을 명시한다 - 리스트 그대로 대입하면 int·None 혼합을 pandas 가
    # float64 로 승격시켜 None 을 NaN 으로 바꾼다(위 docstring 참고).
    out["delta"] = pd.array(deltas, dtype="Int64")
    return out[FUNNEL_COLS]


def legacy_events(iso: pd.DataFrame) -> pd.DataFrame:
    """구 규칙의 묶음. ★순수

    구 규칙은 연쇄 병합이고, 연쇄 묶음은 run 그 자체다(schemas.RUN). 그래서 새
    이벤트 표를 run 으로 접으면 구 규칙의 묶음이 그대로 나온다 - 원본을 다시
    훑을 필요가 없다.

    `last_at` 을 **넣지 않는다.** events.isolation 은 그 열이 없으면 시작 기준으로
    간격을 재는데, 그것이 구 규칙의 나머지 절반(앞30/뒤60 은 호출부에서 넣는다)이다.

    출력 열은 `n_items` 가 아니라 **`n_item_touches`** 다. 앵커 이벤트의 `n_items`
    는 그 이벤트 하나 안에서의 nunique 항목 수인데, 여기서는 그것을 run 안의
    fragment 여러 개에 걸쳐 그대로 **더한다** - 같은 항목이 fragment 두 개에서
    손질됐으면 두 번 잡힌다(횟수이지 항목 수가 아니다). `rule_comparison` 이
    `only_old_examples[i]["n_item_touches"]` 에서 이미 이 구분을 문서화하고
    있었는데 이 함수의 열 이름만 옛 이름(`n_items`)을 쓰고 있었다.
    """
    if iso.empty:
        return pd.DataFrame(columns=[S.LOT, S.EVENT, S.AT, "n_item_touches"])
    out = (
        iso.groupby([S.LOT, S.RUN], as_index=False)
        .agg(**{S.AT: (S.AT, "min"), "n_item_touches": ("n_items", "sum")})
    )
    out[S.EVENT] = out[S.RUN]
    return out[[S.LOT, S.EVENT, S.AT, "n_item_touches"]].sort_values(
        [S.LOT, S.AT]
    ).reset_index(drop=True)


def rule_comparison(
    iso: pd.DataFrame,
    event_deltas: pd.DataFrame,
    bounds: pd.DataFrame | None,
    old_pre: int,
    old_post: int,
    new_pre: int,
    new_post: int,
) -> dict:
    """구 규칙과 신 규칙이 무엇을 살리고 무엇을 죽였나. ★순수

    구 규칙은 **세 가지를 다 되돌린 것**이다 - 연쇄 병합 + 시작 기준 간격 +
    옛 창(앞30/뒤60). 하나만 되돌리면 무엇이 차이를 만들었는지 갈리지 않는다.
    (연쇄 병합과 시작 기준은 legacy_events 가 last_at 을 빼는 것으로 한 번에
    되돌리고, 옛 창은 여기서 old_pre/old_post 로 넣는다.)

    `new_pre`/`new_post` 는 **기록만 하고 적용하지 않는다.** `iso` 는 이미 그
    창으로 격리가 끝난 채로 들어온다 - 이 함수가 다시 격리를 돌리지 않는다.
    그런데도 인자로 받는 이유는, 호출부(Task 10)의 리포트 제목이 "구 규칙
    (연쇄·시작 기준·앞30/뒤60) 대비" 처럼 창 값을 문장에 박아 넣기 때문이다.
    창 값이 반환 dict 에 없으면 그 문장은 실제로 쓰인 창과 다른 숫자를 말할
    수 있다 - 숫자를 믿는 그 한 줄에서 거짓말이 난다. 여기 기록해 두면
    렌더러가 실제로 일어난 일을 그대로 찍을 수 있다. `iso` 를 만들 때 쓴
    창과 여기 넘긴 `new_pre`/`new_post` 가 실제로 같은지는 이 함수가
    검증하지 않는다 - 그 어긋남은 여전히 가능하지만, 최소한 결과에 찍힌
    창 값을 보고 사람이 알아챌 수는 있다(이전에는 그 창 값 자체가 어디에도
    남지 않았다).

    겹침은 **run 단위**로 센다. 구는 묶음이 run 이고 신은 그 안의 조각이라
    단위가 다른데, 그대로 빼면 뜻이 없는 숫자가 나온다.

    `both`/`only_new`/`only_old` 는 **old 로 격리되었거나 new 로 격리된 run
    들의 합집합**만 가른다 - 둘 다에서 탈락한 run 은 셋 중 어디에도 들지
    않는다. 그래서 `both + only_new + only_old` 는 `n_runs` 와 같지 않다.

    `only_old_examples[i]["n_item_touches"]` 는 **항목 수가 아니라 손질
    횟수**다. run 을 이룬 fragment 마다 조정된 항목의 nunique 를 구해
    fragment 간에 그대로 더하므로, 같은 zone 이 두 fragment 에서 손질됐으면
    두 번 잡힌다. `old["n_items"]`/`new["n_items"]`(event_deltas 행을 세어
    중복이 없다)와 이름이 비슷해 보여도 다른 값이다 - 사람이 읽는 예시 한
    줄에만 쓰고, 계산된 비교치로는 쓰지 않는다.
    """
    if iso.empty:
        empty_old = {"n_clusters": 0, "n_isolated": 0, "n_items": 0,
                     "pre": old_pre, "post": old_post}
        empty_new = {"n_clusters": 0, "n_isolated": 0, "n_items": 0,
                     "pre": new_pre, "post": new_post}
        return {"old": empty_old, "new": empty_new, "both": 0,
                "only_new": 0, "only_old": 0, "only_old_examples": [],
                "n_runs": 0}

    old = ev_mod.isolation(legacy_events(iso), old_pre, old_post, bounds)
    new_ok = iso["isolated"].astype(bool)

    zoned = event_deltas[event_deltas[S.ZONE].notna()]
    n_items_new = int(zoned[zoned[S.EVENT].isin(iso.loc[new_ok, S.EVENT])].shape[0])
    old_ok_runs = set(old.loc[old["isolated"].astype(bool), S.EVENT])
    n_items_old = int(zoned[zoned[S.EVENT].isin(
        iso.loc[iso[S.RUN].isin(old_ok_runs), S.EVENT]
    )].shape[0])

    runs_with_new = set(iso.loc[new_ok, S.RUN])
    all_runs = set(iso[S.RUN])
    both = old_ok_runs & runs_with_new
    only_old = old_ok_runs - runs_with_new
    only_new = runs_with_new - old_ok_runs

    # run id 문자열 정렬은 "L1@10" 이 "L1@2" 보다 앞에 오는 사전식 함정이 있다
    # (사용자가 그대로 붙여넣는 예시 세 줄이라 순서가 실제 발생 순서와 어긋나면
    # 안 된다). run 의 첫 변경 시각으로 정렬한다.
    run_first_at = iso.groupby(S.RUN)[S.AT].min()
    examples = []
    for r in sorted(only_old, key=lambda run_id: run_first_at[run_id])[:3]:
        g = iso[iso[S.RUN] == r]
        examples.append({
            "run": r,
            "first": g[S.AT].min(),
            "last": g[S.LAST_AT].max(),
            "n_fragments": int(len(g)),
            "n_item_touches": int(g["n_items"].sum()),
        })

    return {
        "old": {"n_clusters": int(len(old)),
                "n_isolated": int(len(old_ok_runs)),
                "n_items": n_items_old,
                "pre": old_pre, "post": old_post},
        "new": {"n_clusters": int(len(iso)),
                "n_isolated": int(new_ok.sum()),
                "n_items": n_items_new,
                "pre": new_pre, "post": new_post},
        "both": len(both),
        "only_new": len(only_new),
        "only_old": len(only_old),
        "only_old_examples": examples,
        "n_runs": len(all_runs),
    }


# 칸 하나에 이벤트가 둘 겹칠 때 어느 표식이 이길지. 숫자가 클수록 우선한다.
# ✗(탈락)이 ✓(통과)를 덮어야 한다 - 통과 표식 뒤에 탈락이 숨으면, 그 lot 은
# 실제로는 뭔가 잃었는데 그림만 보면 다 통과한 것처럼 보인다. 이 파일의
# 다른 표들이 지키는 원칙과 같다(funnel: "이름 없는 감소가 없어야 한다") -
# 그림에서도 나쁜 소식이 좋은 소식 뒤에 조용히 묻히면 안 된다.
_CELL_PRIORITY = {"─": 0, "✓": 1, "✗": 2}


def timeline(
    iso: pd.DataFrame, bounds: pd.DataFrame, width: int = 80
) -> tuple[float, list[dict]]:
    """lot 하나를 한 줄로 그린다. ★순수 — 문자열만 만들고 출력은 안 한다.

    스케일은 **전체 공통**이다. 가장 긴 lot 이 width 칸에 들어가도록 분/칸을 정하고
    모든 lot 에 같은 값을 쓴다. lot 마다 폭에 맞춰 늘이면 모든 lot 이 같은 길이로
    보여 "이 lot 은 짧다" 는 정보가 사라진다 - 표로는 안 보이고 이 그림으로만
    보이는 것이 바로 그 길이 차이다.

    통과가 0건인 lot 도 그린다. 그런 lot 이야말로 봐야 할 것이다 - 이벤트가
    하나도 안 남았다는 사실은 행이 아예 없으면 조용히 사라진다.

    한 칸에 이벤트가 둘 겹치면(스케일이 굵을 때 생긴다) ✗ 가 ✓ 를 덮는다
    (_CELL_PRIORITY). 이유는 위 상수 주석에 있다.
    """
    if bounds is None or bounds.empty:
        return 0.0, []

    minute = pd.Timedelta(minutes=1)
    spans = (bounds["end"] - bounds["start"]) / minute
    longest = float(spans.max()) if len(spans) else 0.0
    mpc = max(longest / width, 1.0) if longest > 0 else 1.0

    rows = []
    for b in bounds.sort_values(S.LOT).itertuples(index=False):
        lot = getattr(b, S.LOT)
        span = (b.end - b.start) / minute
        n = max(int(round(span / mpc)), 1)
        cells = ["─"] * n
        g = iso[iso[S.LOT] == lot]

        pos = {}
        for e in g.itertuples(index=False):
            i = max(0, min(int(((getattr(e, S.AT) - b.start) / minute) / mpc), n - 1))
            pos[getattr(e, S.EVENT)] = i
            mark = "✓" if bool(e.isolated) else "✗"
            if _CELL_PRIORITY[mark] >= _CELL_PRIORITY[cells[i]]:
                cells[i] = mark

        runs = []
        if len(g):
            sizes = _run_sizes(g)
            for run_id, k in sizes[sizes > 1].items():
                idx = [pos[e] for e in g.loc[g[S.RUN] == run_id, S.EVENT]]
                runs.append({S.RUN: run_id, "n": int(k),
                             "first_cell": min(idx), "last_cell": max(idx)})

        rows.append({S.LOT: lot, "start": b.start, "end": b.end,
                     "cells": "".join(cells), "runs": runs})
    return mpc, rows
