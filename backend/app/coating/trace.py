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


def event_ledger(iso: pd.DataFrame, event_deltas: pd.DataFrame) -> pd.DataFrame:
    """묶음 하나당 한 줄. ★순수

    원시 타임스탬프를 그대로 싣는다. 파생된 분 수만 있으면 사람이 원본과 대조할
    방법이 없고, 대조가 안 되면 이 표는 믿을 근거가 없는 숫자 더미가 된다.
    """
    if iso.empty:
        return pd.DataFrame(columns=LEDGER_COLS)

    out = iso.copy()
    # run 이 이벤트 하나짜리면 비운다 - 눈에 띄어야 할 것은 쪼개진 것뿐이다.
    sizes = out.groupby(S.RUN)[S.EVENT].transform("size")
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
) -> pd.DataFrame:
    """행이 어느 단계에서 얼마나 줄었나. ★순수

    **이름 없는 감소가 없어야 한다.** 원본 100만 행이 이벤트 31건이 되는 것은
    정상인데, 어느 줄에서 얼마가 빠졌는지 이름이 붙지 않으면 사람은 그 정상을
    이상으로 읽고 파이프라인을 의심하는 데 시간을 쓴다.
    """
    ctrl = changes[changes[S.ITEM].isin(S.CONTROL_ITEM_IDS)]
    real = ctrl[ctrl[S.PREV_VALUE].notna()] if S.PREV_VALUE in ctrl.columns else ctrl

    n_events = int(len(iso))
    if n_events:
        sizes = iso.groupby(S.RUN)[S.EVENT].transform("size")
        n_frag = int((sizes > 1).sum())
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
    out = pd.DataFrame(rows, columns=["stage", "n", "note", "_chains_from_prev"])
    ns = out["n"].tolist()
    chains = out["_chains_from_prev"].tolist()
    out["delta"] = [
        int(ns[i] - ns[i - 1]) if chains[i] else None for i in range(len(out))
    ]
    return out[FUNNEL_COLS]


def legacy_events(iso: pd.DataFrame) -> pd.DataFrame:
    """구 규칙의 묶음. ★순수

    구 규칙은 연쇄 병합이고, 연쇄 묶음은 run 그 자체다(schemas.RUN). 그래서 새
    이벤트 표를 run 으로 접으면 구 규칙의 묶음이 그대로 나온다 - 원본을 다시
    훑을 필요가 없다.

    `last_at` 을 **넣지 않는다.** events.isolation 은 그 열이 없으면 시작 기준으로
    간격을 재는데, 그것이 구 규칙의 나머지 절반(앞30/뒤60 은 호출부에서 넣는다)이다.
    """
    if iso.empty:
        return pd.DataFrame(columns=[S.LOT, S.EVENT, S.AT, "n_items"])
    out = (
        iso.groupby([S.LOT, S.RUN], as_index=False)
        .agg(**{S.AT: (S.AT, "min"), "n_items": ("n_items", "sum")})
    )
    out[S.EVENT] = out[S.RUN]
    return out[[S.LOT, S.EVENT, S.AT, "n_items"]].sort_values(
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

    겹침은 **run 단위**로 센다. 구는 묶음이 run 이고 신은 그 안의 조각이라
    단위가 다른데, 그대로 빼면 뜻이 없는 숫자가 나온다.
    """
    empty = {"n_clusters": 0, "n_isolated": 0, "n_items": 0}
    if iso.empty:
        return {"old": empty, "new": empty, "both": 0,
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

    examples = []
    for r in sorted(only_old)[:3]:
        g = iso[iso[S.RUN] == r]
        examples.append({
            "run": r,
            "first": g[S.AT].min(),
            "last": g[S.LAST_AT].max(),
            "n_fragments": int(len(g)),
            "n_items": int(g["n_items"].sum()),
        })

    return {
        "old": {"n_clusters": int(len(old)),
                "n_isolated": int(len(old_ok_runs)),
                "n_items": n_items_old},
        "new": {"n_clusters": int(len(iso)),
                "n_isolated": int(new_ok.sum()),
                "n_items": n_items_new},
        "both": len(both),
        "only_new": len(only_new),
        "only_old": len(only_old),
        "only_old_examples": examples,
        "n_runs": len(all_runs),
    }
