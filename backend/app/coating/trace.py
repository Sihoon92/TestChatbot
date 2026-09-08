"""전처리 로그용 집계. ★순수 — 파일·설정을 만지지 않는다.

규칙(events.py)과 보고(여기)를 나눈 이유는 하나다. 규칙은 파이프라인이 매번
쓰는 것이고 이 표들은 사람이 볼 때만 필요하다. 섞어 두면 규칙을 읽을 때
렌더링 관심사가 같이 딸려온다.

이 파일의 표들은 **사용자가 혼자 읽고 혼자 판정할 수 있어야** 한다. 실데이터는
사내 PC 에만 있고 여기서는 볼 수 없으므로(CLAUDE.md), 숫자 옆에 판정 기준이
같이 붙지 않으면 그 숫자는 왕복 한 번을 더 쓰게 만든다.
"""
import pandas as pd

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
