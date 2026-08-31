"""시계열 그래프 조립. ★순수 — 파일·설정·streamlit 을 만지지 않는다.

dashboard.py 는 위젯만 달고 여기에 값을 넘긴다. 차트에 대한 판단(무엇을 어떤 행에,
0 을 어떻게, 이벤트를 어디에)이 전부 여기 있어야 그 판단들을 단위 테스트로 고정할
수 있다 - streamlit 앱은 그 자체로는 테스트가 거의 불가능하다.

두 가지를 여기서 안 잡으면 그래프가 거짓말을 한다.

1. Wet 의 0 은 값이 아니라 '미측정' 이다. 그대로 그리면 18.2 에서 0 으로 떨어지는
   절벽이 생겨 사고처럼 보인다. features.wet_wide 가 이미 같은 마스킹을 한다.
2. 값은 계단형이다. 원본은 스냅샷 로그라 사람이 바꾼 순간부터 새 값이 계속 찍힌다
   (pivot.py 참고). 점 사이를 직선으로 이으면 없는 변화를 그리는 셈이다.
"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from app.coating import pivot
from app.coating import schemas as S

# 행 이름이자 그룹 키. 입력을 둘로 나누는 이유는 단위가 다르기 때문이다 -
# Gap Offset zone 은 수백대인데 Pump RPM·BP open rate 는 자릿수가 다르다.
# 한 축에 올리면 작은 쪽이 바닥에 눌려 아무것도 안 보인다.
INPUT_ZONE = "입력 · Gap Offset (zone)"
INPUT_SCALAR = "입력 · 제어값 (스칼라)"
OUTPUT = "출력 · Wet (L/L)"
GROUP_ORDER = (INPUT_ZONE, INPUT_SCALAR, OUTPUT)

# 조정 이벤트 세로선. 오염 이벤트는 인과 분리가 안 되는 것이라 색을 달리한다
# (events.annotate_settling 의 contaminated).
_EVENT_COLOR = "#2b6cb0"
_CONTAMINATED_COLOR = "#c53030"


def group_of(item_id: str) -> str | None:
    """항목이 어느 행에 갈지. 사전에 없는 항목은 None - 추측하지 않는다."""
    if item_id in S.CONTROL_SCALARS:
        return INPUT_SCALAR
    if item_id in S.GAP_ITEM_IDS:
        return INPUT_ZONE
    if item_id in S.WET_ITEM_IDS:
        return OUTPUT
    return None


def available_items(readings: pd.DataFrame, changed_only: bool = False) -> pd.DataFrame:
    """선택 목록에 올릴 항목. 그 구간에 실제로 있는 것만, 읽는 순서대로.

    item_id 를 그대로 보여주면 사람이 고를 수 없다. 사전에서 온 item_name 을
    label 로 쓰고, 없으면 id 로 떨어진다(사전 갱신이 필요하다는 신호다).
    """
    if readings.empty:
        return pd.DataFrame(columns=[S.ITEM, "label", "group", "zone"])

    rows = readings[[S.ITEM, S.ITEM_NAME]].drop_duplicates(subset=[S.ITEM]).copy()
    rows["group"] = rows[S.ITEM].map(group_of)
    rows = rows[rows["group"].notna()]
    if changed_only:
        rows = rows[rows[S.ITEM].isin(_changed_item_ids(readings))]

    rows["zone"] = rows[S.ITEM].map(_zone_of)
    rows["label"] = rows.apply(
        lambda r: r[S.ITEM_NAME] if pd.notna(r[S.ITEM_NAME]) else r[S.ITEM], axis=1
    )
    rows["_g"] = rows["group"].map(GROUP_ORDER.index)
    return (
        rows.sort_values(["_g", "zone", S.ITEM])
        .drop(columns="_g")
        .reset_index(drop=True)[[S.ITEM, "label", "group", "zone"]]
    )


def default_items(readings: pd.DataFrame, limit: int = 8) -> list[str]:
    """처음 열었을 때 켜 둘 항목.

    전부 켜면 54계열이라 읽을 수가 없고, 아무것도 안 켜면 빈 화면이 나온다.
    값이 변한 항목을 먼저 고른다 - 한 번도 안 변한 항목은 직선이라 볼 것이 없다.
    변한 것이 없으면(안정 구간만 있는 데이터) 앞에서부터 채운다.
    """
    items = available_items(readings)
    if items.empty:
        return []
    changed = _changed_item_ids(readings)
    picked = items[items[S.ITEM].isin(changed)][S.ITEM].tolist()[:limit]
    if picked:
        return picked
    return items[S.ITEM].tolist()[:min(limit, 5)]


def timeseries_figure(
    readings: pd.DataFrame,
    items: list[str],
    events: pd.DataFrame | None = None,
) -> go.Figure:
    """선택한 항목을 그룹별 행으로 그린다. 세 행이 시간축을 공유한다.

    선택이 없는 그룹은 행을 만들지 않는다 - 빈 칸이 화면을 먹는다.
    """
    catalog = available_items(readings)
    chosen = catalog[catalog[S.ITEM].isin(items)]
    groups = [g for g in GROUP_ORDER if (chosen["group"] == g).any()]
    if not groups:
        return _empty_figure()

    fig = make_subplots(
        rows=len(groups),
        cols=1,
        shared_xaxes=True,          # 시간축 공유. 한 곳을 확대하면 나머지도 따라온다
        vertical_spacing=0.06,
        subplot_titles=groups,
    )

    for row, group in enumerate(groups, start=1):
        for _, item in chosen[chosen["group"] == group].iterrows():
            s = _series(readings, item[S.ITEM], mask_zero=(group == OUTPUT))
            fig.add_trace(
                go.Scatter(
                    x=s[S.AT],
                    y=s[S.VALUE],
                    name=item["label"],
                    legendgroup=group,
                    legendgrouptitle_text=group,
                    mode="lines+markers",
                    # 계단형. 직선 보간은 없는 변화를 그린다.
                    line_shape="hv",
                    marker_size=4,
                    connectgaps=False,   # 결측(미측정)을 이어 붙이지 않는다
                ),
                row=row,
                col=1,
            )

    if events is not None and not events.empty:
        _add_event_lines(fig, events, n_rows=len(groups))

    fig.update_layout(
        height=260 * len(groups) + 80,
        margin=dict(l=60, r=20, t=60, b=40),
        hovermode="x unified",       # 같은 시각의 입력·출력을 한 번에 읽는다
        legend=dict(groupclick="toggleitem"),
    )
    fig.update_xaxes(title_text="시각", row=len(groups), col=1)
    return fig


# ── 내부 ────────────────────────────────────────────────────────────────


def _series(readings: pd.DataFrame, item_id: str, mask_zero: bool) -> pd.DataFrame:
    """한 항목의 시계열. 시간순으로 정렬하고, 같은 분의 중복은 마지막 값."""
    s = readings[readings[S.ITEM] == item_id][[S.AT, S.VALUE, S.ROW_NO]]
    s = s.sort_values(S.ROW_NO).groupby(S.AT, as_index=False).last()
    if mask_zero:
        # Wet 의 0 은 '미사용/미측정' 이다(features.wet_wide 와 같은 규칙).
        # 값으로 그리면 18.2 에서 0 으로 떨어지는 절벽이 생겨 사고처럼 보인다.
        s = s.copy()
        s.loc[s[S.VALUE] == 0, S.VALUE] = np.nan
    return s.sort_values(S.AT)


def _changed_item_ids(readings: pd.DataFrame) -> set[str]:
    """그 구간에서 값이 한 번이라도 바뀐 항목.

    compress_runs 는 lot 안에서 '변한 시점' 만 남기되 첫 관측도 시작값으로
    남긴다. 그래서 prev_value 가 있는 행만이 진짜 '변화' 다.
    """
    if readings.empty:
        return set()
    changes = pivot.compress_runs(pivot.dedupe_minute(readings))
    return set(changes.loc[changes[S.PREV_VALUE].notna(), S.ITEM])


def _zone_of(item_id: str) -> float:
    for ids in (S.GAP_ITEM_IDS, S.WET_ITEM_IDS):
        if item_id in ids:
            return ids.index(item_id) + 1
    return 0.0   # 스칼라는 zone 이 없다. 정렬에서 앞에 온다.


def _add_event_lines(fig: go.Figure, events: pd.DataFrame, n_rows: int) -> None:
    """조정 이벤트를 모든 행에 같은 x 위치로 긋는다.

    이 분석의 핵심 질문이 "gap 을 바꿨을 때 Wet 이 어떻게 따라오는가" 다. 입력과
    출력에 같은 세로선이 있어야 그 지연이 눈으로 보인다.
    """
    contaminated = (
        events[S.CONTAMINATED].astype(bool)
        if S.CONTAMINATED in events.columns
        else pd.Series(False, index=events.index)
    )
    for (_, ev), bad in zip(events.iterrows(), contaminated):
        for row in range(1, n_rows + 1):
            fig.add_vline(
                x=ev[S.AT],
                row=row,
                col=1,
                line_width=1,
                line_dash="dot" if bad else "dash",
                line_color=_CONTAMINATED_COLOR if bad else _EVENT_COLOR,
            )


def _empty_figure() -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(
        text="표시할 항목을 선택한다", showarrow=False, font_size=15
    )
    fig.update_layout(
        height=240,
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
    )
    return fig
