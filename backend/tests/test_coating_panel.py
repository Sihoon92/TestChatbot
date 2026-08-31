"""분 단위 패널 — 여기가 틀리면 그 위의 지연 통계가 전부 조용히 틀린다."""
import numpy as np
import pandas as pd
import pytest

from app.coating import panel, parse, pivot
from app.coating import schemas as S

GAP1, GAP2 = S.GAP_ITEM_IDS[0], S.GAP_ITEM_IDS[1]
WET1 = S.WET_ITEM_IDS[0]
RPM = "50030111"


def make(rows, lot="L1") -> pd.DataFrame:
    """(분, item_id, value) → dedupe_minute 를 거친 readings."""
    df = pd.DataFrame(
        [
            {S.LOT: lot if len(r) < 4 else r[3],
             S.AT: pd.Timestamp("2026-03-01 08:00") + pd.Timedelta(minutes=r[0]),
             S.PRODUCT: "P1", S.ITEM: r[1], S.VALUE: float(r[2])}
            for r in rows
        ]
    )
    df[S.ROW_NO] = range(len(df))
    joined = df.merge(parse.load_item_dictionary(), on=S.ITEM, how="left")
    return pivot.dedupe_minute(joined)


# ── 격자 ────────────────────────────────────────────────────────────────

def test_grid_is_one_minute_and_fills_the_holes():
    """빠진 분이 채워져야 lag 시프트가 성립한다. 등간격이 이 층의 존재 이유다."""
    p = panel.build_panel(make([(0, GAP1, 300), (4, GAP1, 320)]), 30)
    assert len(p) == 5
    assert p[S.AT].diff().dropna().eq(pd.Timedelta(minutes=1)).all()
    assert list(p[S.gap_col(1)]) == [300, 300, 300, 300, 320]


def test_fill_stops_at_the_limit():
    """설비가 멈춰 관측이 끊긴 구간까지 채우면 있지도 않은 안정 구간이 생기고,
    그게 노이즈 σ 를 실제보다 작게 만든다. σ 는 지연 판정 전부의 기준선이다."""
    p = panel.build_panel(make([(0, GAP1, 300), (10, GAP1, 320)]), ffill_limit_minutes=3)
    g = list(p[S.gap_col(1)])
    assert g[:4] == [300, 300, 300, 300]      # 0 + 3분까지만
    assert all(np.isnan(v) for v in g[4:10])  # 그 뒤는 결측
    assert g[10] == 320


def test_fill_never_crosses_a_lot_boundary():
    """lot 이 바뀌면 다른 물건이다. 앞 lot 의 값이 넘어오면 안 된다."""
    rows = [(0, GAP1, 300, "A"), (0, GAP2, 111, "A"), (1, GAP1, 500, "B")]
    p = panel.build_panel(make(rows), 30)
    b = p[p[S.LOT] == "B"]
    assert b[S.gap_col(1)].iloc[0] == 500
    assert b[S.gap_col(2)].isna().all()       # A 의 값이 안 넘어온다


def test_columns_exist_even_when_the_item_does_not():
    """데이터에 없는 항목 때문에 패널 모양이 달라지면 뒤 단계가 파일마다 다른
    컬럼 집합을 상대해야 한다."""
    p = panel.build_panel(make([(0, GAP1, 300)]), 30)
    assert set(S.PANEL_VALUE_COLS) <= set(p.columns)


def test_seconds_are_floored_to_the_minute():
    """초가 섞이면 분 격자에 안 붙어 lag 정렬이 통째로 어긋난다."""
    d = make([(0, GAP1, 300)])
    d[S.AT] = d[S.AT] + pd.Timedelta(seconds=37)
    p = panel.build_panel(d, 30)
    assert p[S.AT].iloc[0].second == 0


# ── 0 의 의미 ───────────────────────────────────────────────────────────

def test_zero_wet_is_missing_but_zero_gap_is_a_value():
    """Wet 의 0 은 '미사용/미측정' 이고(features.wet_wide 규칙) 입력의 0 은 진짜
    0 이다. 한쪽 규칙을 반대쪽에 적용하면 데이터가 사라지거나 절벽이 생긴다."""
    p = panel.build_panel(make([(0, WET1, 0.0), (0, GAP1, 0.0)]), 30)
    assert np.isnan(p[S.zone_col(1)].iloc[0])
    assert p[S.gap_col(1)].iloc[0] == 0.0


# ── 시작 상태와 변화량 ──────────────────────────────────────────────────

def test_initial_state_is_the_first_row_per_lot():
    rows = [(0, GAP1, 300, "A"), (5, GAP1, 320, "A"), (0, GAP1, 700, "B")]
    init = panel.initial_state(panel.build_panel(make(rows), 30))
    assert dict(zip(init[S.LOT], init[S.gap_col(1)])) == {"A": 300.0, "B": 700.0}


def test_initial_state_leaves_unobserved_items_missing():
    """뒤에서 관측된 값을 끌어와 채우면 그건 측정이 아니라 추측이다."""
    init = panel.initial_state(panel.build_panel(make([(0, GAP1, 300), (3, RPM, 1400)]), 30))
    assert np.isnan(init["pump_rpm"].iloc[0])


def test_delta_is_zero_while_the_value_holds():
    """계단형이라 대부분 0 이다. 그 0 의 패턴이 곧 '언제 손댔나' 이므로 버리지 않는다."""
    p = panel.build_panel(make([(0, GAP1, 300), (3, GAP1, 320)]), 30)
    d = panel.build_delta(p)[S.gap_col(1)]
    assert np.isnan(d.iloc[0])                 # lot 첫 행은 직전이 없다
    assert list(d.iloc[1:]) == [0.0, 0.0, 20.0]


def test_delta_does_not_cross_lots():
    rows = [(0, GAP1, 300, "A"), (1, GAP1, 900, "B")]
    d = panel.build_delta(panel.build_panel(make(rows), 30))
    assert d.groupby(S.LOT)[S.gap_col(1)].first().isna().all()


def test_empty_input_is_not_an_error():
    empty = make([(0, GAP1, 300)]).iloc[0:0]
    p = panel.build_panel(empty, 30)
    assert p.empty and set(S.PANEL_VALUE_COLS) <= set(p.columns)
    assert panel.build_delta(p).empty
    assert panel.initial_state(p).empty


@pytest.mark.parametrize("n_lots,minutes", [(1, 10), (3, 7)])
def test_observed_minutes_counts_only_rows_with_wet(n_lots, minutes):
    """σ 를 몇 분에서 쟀는지 보고할 때 쓴다. gap 만 있는 분은 세면 안 된다."""
    rows = [(m, WET1, 18.2, f"L{i}") for i in range(n_lots) for m in range(minutes)]
    rows += [(0, GAP1, 300, f"L{i}") for i in range(n_lots)]
    assert panel.observed_minutes(panel.build_panel(make(rows), 30)) == n_lots * minutes
