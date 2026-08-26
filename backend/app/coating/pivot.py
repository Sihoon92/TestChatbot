"""분 단위 중복 해소와 run-length 압축. ★순수 — 파일·설정을 만지지 않는다.

원본은 모든 항목을 매 시점 반복 기록하는 스냅샷 로그다. 값은 계단형이라
사람이 바꾼 순간부터 새 값이 계속 찍힌다. 따라서 "변경된 시점"만 남기면
정보 손실 없이 데이터가 수십 분의 일로 줄고, 무엇보다 **조정 이벤트가
직접 보이는 형태**가 된다.
"""
import pandas as pd

from app.coating import schemas as S


def dedupe_minute(readings: pd.DataFrame) -> pd.DataFrame:
    """(lot, 분, item) 중복을 마지막 값으로 접는다.

    '마지막' 의 기준은 파일에 적힌 순서(row_no)다. 값이 계단형 유지값이므로
    같은 분의 여러 기록 중 나중 것이 그 분의 상태다.
    """
    ordered = readings.sort_values(S.ROW_NO)
    return (
        ordered.groupby([S.LOT, S.AT, S.ITEM], as_index=False, sort=False)
        .tail(1)
        .sort_values([S.LOT, S.AT, S.ITEM])
        .reset_index(drop=True)
    )


def compress_runs(deduped: pd.DataFrame) -> pd.DataFrame:
    """항목별로 값이 바뀐 시점만 남긴다. lot 의 첫 관측은 시작값으로 항상 남는다."""
    d = deduped.sort_values([S.LOT, S.ITEM, S.AT])
    prev = d.groupby([S.LOT, S.ITEM])[S.VALUE].shift()
    # prev 가 NaN = lot 안에서 그 항목의 첫 관측 = 시작값
    changed = prev.isna() | (d[S.VALUE] != prev)
    out = d.loc[changed, [S.LOT, S.ITEM, S.AT, S.VALUE]].copy()
    out[S.PREV_VALUE] = prev.loc[changed].to_numpy()
    return out.sort_values([S.LOT, S.AT, S.ITEM]).reset_index(drop=True)


def state_at(changes: pd.DataFrame, lot_id: str, at) -> dict[str, float]:
    """해당 lot 의 `at` 시점 전체 상태 벡터. 아직 관측 안 된 항목은 빠진다."""
    upto = changes[(changes[S.LOT] == lot_id) & (changes[S.AT] <= at)]
    latest = upto.sort_values(S.AT).groupby(S.ITEM)[S.VALUE].last()
    return latest.to_dict()
