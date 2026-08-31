"""코팅 데이터 브라우저 뷰어.

    cd backend
    streamlit run app/coating/dashboard.py

위젯만 단다. 차트에 대한 판단은 전부 viz.py 에 있다 - 여기서 하기 시작하면
테스트할 수 없는 곳에 로직이 쌓인다.
"""
import sys
from pathlib import Path

# `streamlit run` 은 **스크립트가 있는 폴더**를 sys.path 에 넣는다. cwd 가 아니다.
# 그래서 backend/ 에서 실행해도 `from app.coating import ...` 가 ModuleNotFoundError
# 로 죽는다(`python -m` 과 다른 점이다). backend 루트를 직접 넣어 준다.
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from app.coating import events as ev_mod  # noqa: E402
from app.coating import features, parse, pivot, viz  # noqa: E402
from app.coating import schemas as S  # noqa: E402
from app.config import get_settings  # noqa: E402

ALL_LOTS = "(전체)"


@st.cache_data(show_spinner="원본을 읽는 중…")
def load(path: str, fmt: str, encodings: tuple[str, ...], _mtime_ns: int) -> pd.DataFrame:
    """원본을 읽는다. streamlit 은 위젯을 건드릴 때마다 스크립트를 처음부터 다시
    실행하므로, 캐시가 없으면 슬라이더를 움직일 때마다 파일을 통째로 다시 읽는다.

    _mtime_ns 는 함수 안에서 안 쓴다. 캐시 키에만 들어간다 - 원본을 갱신했는데
    옛 데이터가 계속 나오는 것을 막는 것이 목적이다.
    """
    return parse.load_readings(path, encodings=list(encodings), source=fmt)


@st.cache_data(show_spinner=False)
def build_events(readings: pd.DataFrame, merge_minutes: int, std_max: float,
                 window_minutes: int, max_wait_minutes: int) -> pd.DataFrame:
    """조정 이벤트 + 정착/오염 표시. 전부 기존 파이프라인 함수를 그대로 쓴다.

    시간 필터 전의 lot 전체로 계산한다 - 구간을 좁힌 데이터로 계산하면 경계에
    걸친 이벤트의 정착 판정이 달라진다.
    """
    deduped = pivot.dedupe_minute(readings)
    changes = pivot.compress_runs(deduped)
    ev, _ = ev_mod.build_events(changes, merge_minutes)
    if ev.empty:
        return ev
    wet = features.wet_wide(deduped)
    wm = features.wet_mean_series(wet, features.valid_zones(wet))
    return ev_mod.annotate_settling(ev, wm, std_max, window_minutes, max_wait_minutes)


def main() -> None:
    st.set_page_config(page_title="코팅 데이터 뷰어", layout="wide")
    st.title("코팅 데이터 뷰어")
    s = get_settings()

    with st.sidebar:
        st.subheader("원본")
        path_text = st.text_input(
            "경로", value=s.resolved_coating_input_path,
            help="parquet·csv·xlsx 모두 열린다. 형식은 확장자로 판별한다.",
        )
        path = Path(path_text)
        if not path.exists():
            st.error(f"파일이 없다: {path}")
            st.stop()

    fmt = parse.format_for(path, default=s.coating_input_format)
    readings = load(
        str(path), fmt, tuple(s.coating_csv_encoding_list), path.stat().st_mtime_ns
    )
    st.caption(f"{path.name} · {fmt} · {len(readings):,}행")

    with st.sidebar:
        st.subheader("필터")
        lots = sorted(readings[S.LOT].dropna().unique().tolist())
        lot = st.selectbox("lot", [ALL_LOTS, *lots])
        scoped = readings if lot == ALL_LOTS else readings[readings[S.LOT] == lot]

        scoped = _time_filter(scoped)
        changed_only = st.checkbox(
            "값이 변한 항목만 목록에 표시", value=False,
            help="한 번도 안 바뀐 항목은 직선이라 볼 것이 없다.",
        )
        st.button(
            "선택 초기화", on_click=_reset_picks, args=(str(path),),
            help="그 lot 에 없는 항목만 남아 그래프가 비었을 때.",
        )
        picked = _item_pickers(scoped, changed_only, str(path))
        show_events = st.checkbox("조정 이벤트 표시", value=True)

    events = None
    if show_events:
        lot_scope = readings if lot == ALL_LOTS else readings[readings[S.LOT] == lot]
        events = build_events(
            lot_scope, s.coating_event_merge_minutes, s.coating_settle_std_max,
            s.coating_settle_window_minutes, s.coating_settle_max_wait_minutes,
        )
        events = _clip_events(events, scoped)
        st.caption(_event_caption(events))

    st.plotly_chart(viz.timeseries_figure(scoped, picked, events), width="stretch")


def _time_filter(scoped: pd.DataFrame) -> pd.DataFrame:
    """시간 구간 슬라이더. 관측이 한 시점뿐이면 슬라이더를 만들지 않는다
    (min == max 이면 streamlit 슬라이더가 뜨지 않는다)."""
    if scoped.empty:
        return scoped
    lo, hi = scoped[S.AT].min().to_pydatetime(), scoped[S.AT].max().to_pydatetime()
    if lo >= hi:
        st.caption(f"시각 {lo:%Y-%m-%d %H:%M} (한 시점)")
        return scoped
    start, end = st.slider(
        "시간 구간", min_value=lo, max_value=hi, value=(lo, hi), format="MM/DD HH:mm"
    )
    return scoped[(scoped[S.AT] >= start) & (scoped[S.AT] <= end)]


def _item_pickers(scoped: pd.DataFrame, changed_only: bool, scope_key: str) -> list[str]:
    """그룹마다 멀티선택 하나. 넣고 빼는 것이 이 화면의 주된 조작이다.

    위젯 key 에 원본 경로를 넣는 이유. key 가 있으면 streamlit 이 선택을
    session_state 에 붙들어 두는데, 그게 없으면 시간 슬라이더를 움직일 때마다
    (선택지 목록이 바뀌어) 선택이 초기화된다. 반대로 key 를 그룹 이름만으로 두면
    **다른 파일을 열어도 옛 선택이 남아** 새 파일에 없는 항목만 남고 그래프가
    비어 버린다. 그래서 파일 단위로 나눈다 - lot 을 바꿀 때는 유지되고(같은 zone
    을 lot 끼리 비교하는 것이 흔한 조작이다) 파일을 바꾸면 기본값으로 돌아간다.
    """
    catalog = viz.available_items(scoped, changed_only=changed_only)
    defaults = set(viz.default_items(scoped))
    picked: list[str] = []
    for group in viz.GROUP_ORDER:
        rows = catalog[catalog["group"] == group]
        if rows.empty:
            continue
        labels = dict(zip(rows["label"], rows[S.ITEM]))
        chosen = st.multiselect(
            group,
            options=list(labels),
            default=[la for la, i in labels.items() if i in defaults],
            key=f"pick::{scope_key}::{group}",
        )
        picked += [labels[la] for la in chosen]
    return picked


def _reset_picks(scope_key: str) -> None:
    """선택을 지워 기본값으로 되돌린다. lot 을 옮겨 다니다 보면 그 lot 에 없는
    항목만 남아 빈 그래프가 되는 때가 있는데, 그때의 탈출구다."""
    for key in [k for k in st.session_state if k.startswith(f"pick::{scope_key}::")]:
        del st.session_state[key]


def _clip_events(events: pd.DataFrame, scoped: pd.DataFrame) -> pd.DataFrame:
    """보이는 구간의 이벤트만. 계산은 lot 전체로 하고 표시만 자른다."""
    if events is None or events.empty or scoped.empty:
        return events
    lo, hi = scoped[S.AT].min(), scoped[S.AT].max()
    return events[(events[S.AT] >= lo) & (events[S.AT] <= hi)]


def _event_caption(events: pd.DataFrame) -> str:
    if events is None or events.empty:
        return "이 구간에 조정 이벤트가 없다 — 제어값이 한 번도 바뀌지 않았다."
    bad = int(events[S.CONTAMINATED].astype(bool).sum()) if S.CONTAMINATED in events else 0
    return f"조정 이벤트 {len(events)}건 (오염 {bad}건 — 붉은 점선)"


main()
