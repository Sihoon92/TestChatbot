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
