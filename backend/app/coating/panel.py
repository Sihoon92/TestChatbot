"""분 단위 등간격 패널과 변화량. ★순수 — 파일·설정을 만지지 않는다.

왜 필요한가. 지금 파이프라인은 값이 바뀐 시점만 남긴다(pivot.compress_runs). 그
구조로는 "t 시점의 입력이 t+k 시점의 출력에 어떻게 나타나는가" 를 볼 수 없다 —
lag 를 주려면 행이 등간격으로 늘어서 있어야 한다. 여기서 그 토대를 만든다.

값이 계단형 유지값이므로(pivot.py 참고) 빈 분을 직전 값으로 채우는 것이 물리적으로
맞다. 다만 **상한을 둔다**. 설비가 멈춰 관측이 끊긴 구간까지 채우면 있지도 않은
안정 구간이 생기고, 그게 노이즈 추정 σ 를 실제보다 작게 만든다. σ 는 뒤따르는
지연 판정 전부의 기준선이라, 거기가 낙관적으로 기울면 없는 반응을 있다고 말하게
된다. lot 경계는 어떤 경우에도 넘지 않는다 — lot 이 바뀌면 다른 물건이다.
"""
import pandas as pd

from app.coating import features
from app.coating import schemas as S


def build_panel(deduped: pd.DataFrame, ffill_limit_minutes: int) -> pd.DataFrame:
    """(lot_id, worked_at) 1분 간격 wide 패널.

    컬럼: g1..g25(입력 gap) · z1..z25(출력 Wet) · 스칼라 제어값 4종.
    lot_id·worked_at 은 인덱스가 아니라 컬럼이다 — 이 패키지의 다른 표들과 같다.

    Wet 의 0 은 결측으로 둔다(features.wet_wide 규칙). 입력의 0 은 진짜 0 이라
    그대로 둔다.
    """
    if deduped.empty:
        return pd.DataFrame(columns=[S.LOT, S.AT, *S.PANEL_VALUE_COLS])

    # 초 단위가 섞여 있으면 분 격자에 안 붙는다. 먼저 분으로 내린다.
    d = deduped.copy()
    d[S.AT] = d[S.AT].dt.floor("min")

    wide = features.wet_wide(d)                       # lot·at·z1..z25
    wide = wide.merge(_pivot(d, _gap_map(), S.GAP_COLS), on=[S.LOT, S.AT], how="outer")
    wide = wide.merge(
        _pivot(d, S.CONTROL_SCALARS, S.SCALAR_COLS), on=[S.LOT, S.AT], how="outer"
    )

    grid = _minute_grid(wide)
    out = grid.merge(wide, on=[S.LOT, S.AT], how="left").sort_values([S.LOT, S.AT])
    # limit 는 '연속으로 몇 칸' 이다. 격자가 1분 간격이라 곧 분 수와 같다.
    out[S.PANEL_VALUE_COLS] = out.groupby(S.LOT)[S.PANEL_VALUE_COLS].ffill(
        limit=ffill_limit_minutes
    )
    return out.reset_index(drop=True)


def build_delta(panel: pd.DataFrame) -> pd.DataFrame:
    """1분 변화량. lot 별로 차분하므로 lot 첫 행은 NaN(직전이 없다).

    계단형이라 대부분 0 이다. 그 0 의 패턴 자체가 "언제 손댔나" 이므로 버리지
    않는다 — 0 을 결측으로 접으면 등간격이 깨져 lag 를 못 준다.
    """
    if panel.empty:
        return panel.copy()
    out = panel[[S.LOT, S.AT]].copy()
    out[S.PANEL_VALUE_COLS] = panel.groupby(S.LOT)[S.PANEL_VALUE_COLS].diff()
    return out


def initial_state(panel: pd.DataFrame) -> pd.DataFrame:
    """lot 별 시작 상태 — 패널 첫 행.

    결측은 "그 항목이 lot 시작 시점에 아직 관측되지 않았다" 는 뜻이다. 뒤에서
    관측된 값을 끌어와 채우지 않는다 - 그건 측정이 아니라 추측이다.

    groupby.first() 를 쓰면 안 된다. 그건 첫 '행' 이 아니라 컬럼마다 첫 '비결측값'
    을 집어서, 3분 뒤에야 처음 찍힌 값을 시작값 자리에 넣는다. 조용히 틀린다.
    """
    if panel.empty:
        return panel.copy()
    return (
        panel.sort_values([S.LOT, S.AT])
        .groupby(S.LOT, as_index=False)
        .head(1)
        .reset_index(drop=True)
    )


def observed_minutes(panel: pd.DataFrame) -> int:
    """Wet 이 한 zone 이라도 관측된 분 수. σ 를 몇 분에서 쟀는지 보고할 때 쓴다."""
    if panel.empty:
        return 0
    return int(panel[S.ZONE_COLS].notna().any(axis=1).sum())


# ── 내부 ────────────────────────────────────────────────────────────────


def _gap_map() -> dict[str, str]:
    """item_id -> 패널 컬럼명. GAP_ITEM_IDS 는 zone 순서대로 들어 있다."""
    return {item: S.gap_col(z) for z, item in enumerate(S.GAP_ITEM_IDS, start=1)}


def _pivot(d: pd.DataFrame, id_to_col: dict[str, str], cols: list[str]) -> pd.DataFrame:
    """항목 몇 개를 (lot, at) 기준 wide 로. 없는 항목도 열은 만든다.

    열을 항상 만드는 이유: 데이터에 없는 항목 때문에 패널의 모양이 달라지면,
    뒤 단계가 파일마다 다른 컬럼 집합을 상대해야 한다.
    """
    sub = d[d[S.ITEM].isin(id_to_col)]
    if sub.empty:
        return pd.DataFrame(columns=[S.LOT, S.AT, *cols])
    wide = sub.pivot_table(
        index=[S.LOT, S.AT], columns=S.ITEM, values=S.VALUE, aggfunc="last"
    )
    wide.columns = [id_to_col[i] for i in wide.columns]
    return wide.reindex(columns=cols).reset_index()


def _minute_grid(wide: pd.DataFrame) -> pd.DataFrame:
    """lot 마다 첫 관측~마지막 관측을 1분 간격으로 채운 (lot, at) 격자."""
    rows = []
    for lot, g in wide.groupby(S.LOT):
        rows.append(
            pd.DataFrame({
                S.LOT: lot,
                S.AT: pd.date_range(g[S.AT].min(), g[S.AT].max(), freq="min"),
            })
        )
    return pd.concat(rows, ignore_index=True)
